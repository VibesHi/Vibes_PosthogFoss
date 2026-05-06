#!/usr/bin/env bash
#
# verify.sh -- manifest-only validation of the backups in GCS.
#
# Answers "are the artifacts uploaded, sane size, and metadata-readable?"
# without doing a full restore. Catches the most common silent-failure modes:
#   * upload truncated mid-stream (size < threshold)
#   * `aws s3 ls` returns nothing (auth broken)
#   * CH BACKUP wrote a 0-byte zip (rare but seen)
#   * latest PG dump is older than expected cadence (cron not firing)
#
# What it does NOT catch:
#   * "the .zip is intact but RESTORE will fail because the source schema
#     diverged from the destination". For that, restore into a throwaway --
#     this script intentionally does not do that (~30s instead of ~30min).
#
# Usage:
#   verify.sh                          # check all classes
#   verify.sh pg                       # only postgres/
#   verify.sh ch-current               # only clickhouse/events/current/
#   verify.sh ch-frozen                # only clickhouse/events/frozen/
#   verify.sh ch-persons               # only clickhouse/persons-weekly/
#
# Exits non-zero if ANY check fails. Suitable for CI / Uptime-Kuma scrape:
#   docker compose exec -T backup /usr/local/bin/backup-scripts/verify.sh

SCRIPT_NAME=verify
# shellcheck source=_lib.sh
source "$(dirname "$0")/_lib.sh"

# Don't `die` on first failure -- we want to print all violations for the run.
set +e

require_gcs_creds

CLASS="${1:-all}"

NOW=$(date -u +%s)
FAIL=0

# Minimum acceptable artifact sizes (bytes) -- below this is almost certainly
# a truncated/empty upload, not a "tiny but valid" backup.
readonly MIN_SIZE_PG=$((10 * 1024))             # 10 KB (empty PostHog DB still emits role + schema definitions)
readonly MIN_SIZE_CH=$((1024))                  # 1 KB  (empty BACKUP zip has metadata.json)

# Maximum acceptable age (seconds) for the LATEST artifact in each class.
# Slightly more permissive than healthcheck.sh (which is for the running
# container) since this might be invoked manually after a long downtime.
#
# ch-frozen has NO max-age check: a single-month install will legitimately
# have a single frozen artifact months old. The lifecycle policy keeps these
# alive forever (no Delete rule), so age means nothing here.
declare -A MAX_AGE=(
    [pg]=$((30*3600))
    [ch-current]=$((30*3600))
    [ch-persons]=$((9*86400))
)

# Classes where "no objects yet" is OK (won't increment FAIL). ch-frozen on
# a fresh install legitimately has nothing for ~30d after deploy.
declare -A ALLOW_EMPTY=(
    [ch-frozen]=1
)

verify_class() {
    local label="$1" prefix="$2" min_size="$3" max_age="$4"

    log "Class '${label}' -> s3://$(gcs_bucket_name)/${prefix}"
    local listing
    listing=$(aws_gcs ls "$(s3_uri "$prefix")" --recursive 2>/dev/null)
    if [ -z "$listing" ]; then
        if [ -n "${ALLOW_EMPTY[$label]:-}" ]; then
            warn "  no objects under ${prefix} -- expected on fresh installs less than 1 month old, otherwise CHECK CRON LOGS"
            return
        fi
        warn "  no objects under ${prefix} -- backups may have never run for this class"
        FAIL=$((FAIL + 1))
        return
    fi

    local total_count total_bytes
    total_count=$(echo "$listing" | wc -l | tr -d ' ')
    total_bytes=$(echo "$listing" | awk '{sum+=$3} END {print sum+0}')
    log "  ${total_count} objects, $(numfmt --to=iec --suffix=B "${total_bytes}") total"

    # Latest object: lex-sort works because all keys embed YYYYMMDDTHHMMSSZ
    # timestamps in path components.
    local latest_line latest_key latest_size latest_date latest_time latest_ts
    latest_line=$(echo "$listing" | sort -k1,2 | tail -n 1)
    latest_date=$(echo "$latest_line" | awk '{print $1}')
    latest_time=$(echo "$latest_line" | awk '{print $2}')
    latest_size=$(echo "$latest_line" | awk '{print $3}')
    latest_key=$(echo "$latest_line"  | awk '{print $4}')

    log "  latest: ${latest_key} ($(numfmt --to=iec --suffix=B "${latest_size}"), ${latest_date} ${latest_time}Z)"

    # Size check
    if [ "${latest_size:-0}" -lt "$min_size" ]; then
        warn "  FAIL: latest is ${latest_size}B, below threshold ${min_size}B"
        FAIL=$((FAIL + 1))
    fi

    # Age check is skipped when caller passes empty max_age (e.g. frozen
    # artifacts -- they're retained forever, age is meaningless).
    if [ -n "$max_age" ]; then
        local latest_epoch age
        latest_epoch=$(date -u -d "${latest_date} ${latest_time} UTC" +%s 2>/dev/null || echo 0)
        if [ "${latest_epoch}" -eq 0 ]; then
            warn "  could not parse latest timestamp ('${latest_date} ${latest_time}'). Skipping age check."
        else
            age=$((NOW - latest_epoch))
            if [ "$age" -gt "$max_age" ]; then
                warn "  FAIL: latest is ${age}s old, above threshold ${max_age}s ($(printf '%dd' $((age/86400))) old)"
                FAIL=$((FAIL + 1))
            fi
        fi
    fi
}

case "$CLASS" in
    pg|all)         verify_class pg          "postgres/"                    "$MIN_SIZE_PG" "${MAX_AGE[pg]}" ;;
esac
case "$CLASS" in
    ch-current|all) verify_class ch-current  "clickhouse/events/current/"   "$MIN_SIZE_CH" "${MAX_AGE[ch-current]}" ;;
esac
case "$CLASS" in
    # Pass empty max_age -> verify_class skips the age check. Frozen
    # artifacts are intentionally retained forever, so age is meaningless.
    ch-frozen|all)  verify_class ch-frozen   "clickhouse/events/frozen/"    "$MIN_SIZE_CH" "" ;;
esac
case "$CLASS" in
    ch-persons|all) verify_class ch-persons  "clickhouse/persons-weekly/"   "$MIN_SIZE_CH" "${MAX_AGE[ch-persons]}" ;;
esac

if [ "$FAIL" -gt 0 ]; then
    log "VERIFY FAILED: ${FAIL} violation(s)"
    exit 1
fi
log "VERIFY OK"
exit 0
