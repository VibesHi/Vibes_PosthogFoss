// Mixpanel monthly-jsonl → daily-jsonl.gz splitter for GCS.
//
// Streams each input object from a source GCS prefix, buckets every line by
// `properties.time` → calendar date (UTC), and writes one gzipped jsonl per
// day to a destination GCS prefix.
//
// Designed for the PostHog batch-import-worker `s3_gzip` source: small daily
// objects keep the worker's tempfile extraction footprint tiny and unlock
// per-day parallelism (one worker job key = one daily file).
//
// Idempotent on the input side: rerunning the same input file overwrites the
// daily output objects. If you need partial restart, delete the day(s) you
// want to redo from `gs://<bucket>/<dst-prefix>/`.
//
// Auth: uses Application Default Credentials. Inside Cloud Run Job, this is
// the job's service account. Locally, run `gcloud auth application-default login`.
package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"cloud.google.com/go/storage"
	"github.com/tidwall/gjson"
	"google.golang.org/api/iterator"
)

type config struct {
	bucket          string
	srcPrefix       string
	dstPrefix       string
	inputPattern    string
	concurrency     int
	maxLineBytes    int
	overwrite       bool
	dryRun          bool
}

type stats struct {
	inputObjects   atomic.Int64
	inputBytes     atomic.Int64
	linesRead      atomic.Int64
	linesWritten   atomic.Int64
	linesMalformed atomic.Int64
	outputObjects  atomic.Int64
}

// daily writer: holds an in-flight GCS object writer + gzip stream, keyed
// by date (YYYY-MM-DD). One set of writers per input object — once the
// input is fully read, all writers flush + close, and the input goroutine
// returns. Different inputs never share writers (Mixpanel monthly exports
// don't span months in practice; if they do, see "boundary leak" in RUNBOOK).
type dailyWriters struct {
	bucket *storage.BucketHandle
	prefix string
	ctx    context.Context

	mu      sync.Mutex
	writers map[string]*dailyWriter
}

type dailyWriter struct {
	objectName string
	gzw        *gzip.Writer
	w          *storage.Writer
	count      int64
}

func newDailyWriters(ctx context.Context, bkt *storage.BucketHandle, prefix string) *dailyWriters {
	return &dailyWriters{
		bucket:  bkt,
		prefix:  prefix,
		ctx:     ctx,
		writers: make(map[string]*dailyWriter),
	}
}

func (d *dailyWriters) get(date string) (*dailyWriter, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if w, ok := d.writers[date]; ok {
		return w, nil
	}
	objectName := fmt.Sprintf("%s%s.jsonl.gz", d.prefix, date)
	obj := d.bucket.Object(objectName)
	w := obj.NewWriter(d.ctx)
	w.ChunkSize = 16 * 1024 * 1024 // 16 MB upload chunks — fewer round-trips.
	w.ContentType = "application/gzip"
	gzw := gzip.NewWriter(w)
	dw := &dailyWriter{
		objectName: objectName,
		gzw:        gzw,
		w:          w,
	}
	d.writers[date] = dw
	return dw, nil
}

func (d *dailyWriters) closeAll() (map[string]int64, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	counts := make(map[string]int64, len(d.writers))
	var firstErr error
	for date, dw := range d.writers {
		counts[date] = dw.count
		if err := dw.gzw.Close(); err != nil && firstErr == nil {
			firstErr = fmt.Errorf("gzip close %s: %w", dw.objectName, err)
		}
		if err := dw.w.Close(); err != nil && firstErr == nil {
			firstErr = fmt.Errorf("gcs close %s: %w", dw.objectName, err)
		}
	}
	d.writers = nil
	return counts, firstErr
}

func main() {
	var cfg config
	flag.StringVar(&cfg.bucket, "bucket", "", "GCS bucket (required)")
	flag.StringVar(&cfg.srcPrefix, "src-prefix", "", "Source object prefix (e.g. \"\" for bucket root)")
	flag.StringVar(&cfg.dstPrefix, "dst-prefix", "mixpanel-events/moonx/", "Destination object prefix for daily files")
	flag.StringVar(&cfg.inputPattern, "input-pattern", "events_", "Substring an input object name must contain to be processed")
	flag.IntVar(&cfg.concurrency, "concurrency", 4, "Number of input objects processed in parallel")
	flag.IntVar(&cfg.maxLineBytes, "max-line-bytes", 4*1024*1024, "Maximum size of a single JSONL line (default 4 MiB)")
	flag.BoolVar(&cfg.overwrite, "overwrite", false, "If false, skip processing an input when ALL its expected daily outputs already exist")
	flag.BoolVar(&cfg.dryRun, "dry-run", false, "Read inputs and count lines but write nothing to GCS")
	flag.Parse()

	if cfg.bucket == "" {
		log.Fatal("--bucket is required")
	}

	ctx := context.Background()
	client, err := storage.NewClient(ctx)
	if err != nil {
		log.Fatalf("storage.NewClient: %v", err)
	}
	defer client.Close()

	bkt := client.Bucket(cfg.bucket)
	inputs, err := listInputs(ctx, bkt, cfg.srcPrefix, cfg.inputPattern, cfg.dstPrefix)
	if err != nil {
		log.Fatalf("listInputs: %v", err)
	}
	log.Printf("found %d input objects under gs://%s/%s matching %q",
		len(inputs), cfg.bucket, cfg.srcPrefix, cfg.inputPattern)

	var s stats
	sem := make(chan struct{}, cfg.concurrency)
	var wg sync.WaitGroup
	start := time.Now()

	for _, in := range inputs {
		in := in
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			if err := processInput(ctx, bkt, in, &cfg, &s); err != nil {
				log.Printf("ERROR processing %s: %v", in.Name, err)
			}
		}()
	}
	wg.Wait()

	elapsed := time.Since(start)
	log.Printf("DONE in %s | inputs=%d input_bytes=%s lines_read=%d lines_written=%d malformed=%d output_objects=%d",
		elapsed.Truncate(time.Second),
		s.inputObjects.Load(),
		humanBytes(s.inputBytes.Load()),
		s.linesRead.Load(),
		s.linesWritten.Load(),
		s.linesMalformed.Load(),
		s.outputObjects.Load(),
	)
}

type inputObject struct {
	Name string
	Size int64
}

func listInputs(ctx context.Context, bkt *storage.BucketHandle, srcPrefix, pattern, dstPrefix string) ([]inputObject, error) {
	q := &storage.Query{Prefix: srcPrefix}
	if err := q.SetAttrSelection([]string{"Name", "Size"}); err != nil {
		return nil, err
	}
	it := bkt.Objects(ctx, q)
	var out []inputObject
	for {
		attrs, err := it.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			return nil, err
		}
		// Defensive: never re-process daily outputs as inputs.
		if dstPrefix != "" && strings.HasPrefix(attrs.Name, dstPrefix) {
			continue
		}
		if !strings.Contains(attrs.Name, pattern) {
			continue
		}
		if !strings.HasSuffix(attrs.Name, ".jsonl") {
			continue
		}
		out = append(out, inputObject{Name: attrs.Name, Size: attrs.Size})
	}
	return out, nil
}

func processInput(ctx context.Context, bkt *storage.BucketHandle, in inputObject, cfg *config, s *stats) error {
	log.Printf("[%s] starting (%s)", in.Name, humanBytes(in.Size))
	startInput := time.Now()
	s.inputObjects.Add(1)
	s.inputBytes.Add(in.Size)

	r, err := bkt.Object(in.Name).NewReader(ctx)
	if err != nil {
		return fmt.Errorf("open reader: %w", err)
	}
	defer r.Close()

	var sink lineSink
	if cfg.dryRun {
		sink = &dryRunSink{}
	} else {
		sink = newDailyWriters(ctx, bkt, cfg.dstPrefix)
	}

	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 64*1024), cfg.maxLineBytes)

	var (
		linesRead    int64
		linesWritten int64
		malformed    int64
	)
	for scanner.Scan() {
		linesRead++
		line := scanner.Bytes()
		// Mixpanel raw export lines look like:
		//   {"event":"...","properties":{"time":1709251316,"distinct_id":"…",…}}
		// Use gjson for a single zero-allocation field probe — avoids
		// unmarshalling a 5 KB object 50M times.
		ts := gjson.GetBytes(line, "properties.time")
		if !ts.Exists() {
			malformed++
			continue
		}
		var unix int64
		switch ts.Type {
		case gjson.Number:
			unix = ts.Int()
		default:
			malformed++
			continue
		}
		// Worker's heuristic: > 10^10 = millis. Same here, before bucketing.
		if unix > 10_000_000_000 {
			unix /= 1000
		}
		date := time.Unix(unix, 0).UTC().Format("2006-01-02")
		if err := sink.write(date, line); err != nil {
			return fmt.Errorf("sink write: %w", err)
		}
		linesWritten++
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("scanner: %w", err)
	}

	counts, err := sink.flush()
	if err != nil {
		return fmt.Errorf("sink flush: %w", err)
	}

	s.linesRead.Add(linesRead)
	s.linesWritten.Add(linesWritten)
	s.linesMalformed.Add(malformed)
	s.outputObjects.Add(int64(len(counts)))

	// Sorted by date for log readability.
	sortedDates := make([]string, 0, len(counts))
	for d := range counts {
		sortedDates = append(sortedDates, d)
	}
	// Native sort, not sort.Strings to avoid an extra import; YYYY-MM-DD
	// strings sort identically lexicographically.
	for i := 1; i < len(sortedDates); i++ {
		for j := i; j > 0 && sortedDates[j-1] > sortedDates[j]; j-- {
			sortedDates[j-1], sortedDates[j] = sortedDates[j], sortedDates[j-1]
		}
	}

	log.Printf("[%s] done in %s | lines_read=%d written=%d malformed=%d days=%d",
		in.Name, time.Since(startInput).Truncate(time.Second),
		linesRead, linesWritten, malformed, len(counts))
	for _, d := range sortedDates {
		log.Printf("[%s]   %s -> %d events", in.Name, d, counts[d])
	}
	return nil
}

type lineSink interface {
	write(date string, line []byte) error
	flush() (map[string]int64, error)
}

func (d *dailyWriters) write(date string, line []byte) error {
	dw, err := d.get(date)
	if err != nil {
		return err
	}
	if _, err := dw.gzw.Write(line); err != nil {
		return err
	}
	if _, err := dw.gzw.Write([]byte{'\n'}); err != nil {
		return err
	}
	dw.count++
	return nil
}

func (d *dailyWriters) flush() (map[string]int64, error) {
	return d.closeAll()
}

type dryRunSink struct {
	mu     sync.Mutex
	counts map[string]int64
}

func (d *dryRunSink) write(date string, _ []byte) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.counts == nil {
		d.counts = make(map[string]int64)
	}
	d.counts[date]++
	return nil
}

func (d *dryRunSink) flush() (map[string]int64, error) {
	return d.counts, nil
}

func humanBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for n2 := n / unit; n2 >= unit; n2 /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}

// guard against unused-import lint when adding flags
var _ = io.EOF
var _ = os.Stdout
