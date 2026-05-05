-- ClickHouse verification queries for the Mixpanel historical import.
-- Run with: docker compose -f docker-compose.prod.yml exec clickhouse clickhouse-client
-- Replace <UUID> placeholders with actual BatchImport IDs.

-- ─────────────────────────────────────────────────────────────────────────
-- 1. PER-IMPORT JOB EVENT COUNT (idempotency check)
-- ─────────────────────────────────────────────────────────────────────────
-- All events ingested by a specific BatchImport row carry that import_job_id.
-- Run BEFORE and AFTER a re-run of the same BatchImport — the count must
-- not change. If it doubles, $insert_id-based deduplication is broken.
SELECT
    properties.$import_job_id AS job_id,
    count() AS events,
    min(timestamp) AS earliest_event,
    max(timestamp) AS latest_event
FROM events
WHERE properties.$import_job_id = '<UUID>'
GROUP BY job_id;


-- ─────────────────────────────────────────────────────────────────────────
-- 2. SHAPE SANITY ON A FRESH IMPORT
-- ─────────────────────────────────────────────────────────────────────────
-- Confirms the parser did its job:
--   * historical_migration flag set
--   * analytics_source = 'mixpanel'
--   * $pageview translated (no $mp_web_page_view leaking through)
--   * Geo props on the right keys
--   * Mixpanel-internal noise stripped
SELECT
    countIf(properties.historical_migration = true) AS historical_flag_set,
    countIf(properties.analytics_source = 'mixpanel') AS source_tagged,
    countIf(event = '$mp_web_page_view') AS unmapped_pageview_count,         -- want 0
    countIf(properties.$mp_api_endpoint != '') AS leaked_mp_api_endpoint,    -- want 0
    countIf(properties.mp_processing_time_ms != 0) AS leaked_mp_processing,  -- want 0
    countIf(properties.$insert_id != '') AS preserved_insert_id_count,
    count() AS total
FROM events
WHERE properties.$import_job_id = '<UUID>';


-- ─────────────────────────────────────────────────────────────────────────
-- 3. EVENT-NAME DISTRIBUTION (post-import)
-- ─────────────────────────────────────────────────────────────────────────
SELECT
    event,
    count() AS n
FROM events
WHERE properties.$import_job_id = '<UUID>'
GROUP BY event
ORDER BY n DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────────────────
-- 4. DAILY EVENT COUNT (compare against splitter output expectations)
-- ─────────────────────────────────────────────────────────────────────────
-- Splitter's per-day log lines tell you what to expect; this query confirms
-- ClickHouse received the same number per UTC day.
SELECT
    toDate(timestamp) AS day,
    count() AS events,
    uniqExact(distinct_id) AS unique_distinct_ids
FROM events
WHERE properties.$import_job_id = '<UUID>'
GROUP BY day
ORDER BY day;


-- ─────────────────────────────────────────────────────────────────────────
-- 5. INGESTION LAG OBSERVABILITY (during full run)
-- ─────────────────────────────────────────────────────────────────────────
-- "Recent ingestion rate" — should match Kafka consumer throughput.
SELECT
    toStartOfMinute(_timestamp) AS minute,
    count() AS events_inserted
FROM events
WHERE _timestamp > now() - INTERVAL 30 MINUTE
  AND properties.$import_job_id != ''
GROUP BY minute
ORDER BY minute DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────────────────
-- 6. PERSON COUNT (sanity, not a strict equality)
-- ─────────────────────────────────────────────────────────────────────────
-- Should roughly equal `uniq($user_id)` from the original Mixpanel data
-- minus any anonymous-only users that became orphan Persons (see RUNBOOK
-- "person-merge gap" — was 168/142754 ~ 0.12% in pre-import sample).
SELECT
    team_id,
    count() AS persons,
    countIf(is_identified) AS identified
FROM person
WHERE team_id = <TEAM_ID>;


-- ─────────────────────────────────────────────────────────────────────────
-- 7. PROBE A SINGLE EVENT (smoke test)
-- ─────────────────────────────────────────────────────────────────────────
-- Pick any user_id from the original data and confirm an event arrived
-- with the right shape end-to-end.
SELECT
    event,
    timestamp,
    distinct_id,
    properties.$insert_id,
    properties.$geoip_country_code,
    properties.$geoip_city_name,
    properties.$os,
    properties.$os_version,
    properties.$app_version_string,
    properties.historical_migration,
    properties.analytics_source,
    properties.$import_job_id
FROM events
WHERE distinct_id = '<USER_ID>'
  AND properties.$import_job_id = '<UUID>'
ORDER BY timestamp ASC
LIMIT 5;


-- ─────────────────────────────────────────────────────────────────────────
-- 8. UNIQUE INSERT_ID SAMPLE (deeper idempotency probe)
-- ─────────────────────────────────────────────────────────────────────────
-- For a given day, $insert_id should be 1:1 with events. If you ever see
-- > 1 row per insert_id, ReplacingMergeTree hasn't merged yet; that's
-- harmless (it'll merge in background) but means dedupe count is
-- transiently inflated.
SELECT
    properties.$insert_id AS insert_id,
    count() AS rows,
    countDistinct(uuid) AS distinct_uuids
FROM events
WHERE properties.$import_job_id = '<UUID>'
  AND toDate(timestamp) = '2024-03-01'
GROUP BY insert_id
HAVING rows > 1
LIMIT 20;
-- Force background merges to verify if you see duplicates:
-- OPTIMIZE TABLE events FINAL;     -- ⚠ heavy on a 400M row table; only run during quiet hours
