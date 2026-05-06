# syntax=docker/dockerfile:1.6
#
# Backup container for self-hosted PostHog (production-ish).
#
# Runs `crond -f` as PID 1 (via tini for clean SIGTERM propagation). Cron jobs
# invoke scripts under /usr/local/bin/backup-scripts/ which:
#   * dump Postgres via `pg_dumpall` and stream straight to GCS
#   * issue ClickHouse `BACKUP TABLE ... TO S3(...)` over the HTTP API
#   * track success via sentinel files in /var/run/backup/
#
# Single auth path: GCS HMAC keys (S3-compatible) for both PG uploads (via
# `aws s3` against the GCS interop endpoint) AND ClickHouse `BACKUP` (which
# already speaks S3 natively). One credential pair — no service-account JSON,
# no `gcloud-sdk` image, no metadata-server dance.
#
# `aws-cli` from alpine repos depends on python3 + a heap of transitive deps;
# the `--no-cache` install keeps the image around 80 MB.
#
FROM alpine:3.19

RUN apk add --no-cache \
        bash \
        coreutils \
        util-linux \
        tini \
        curl \
        gzip \
        ca-certificates \
        postgresql16-client \
        aws-cli \
        tzdata \
    && mkdir -p /var/run/backup /etc/crontabs

COPY scripts/ /usr/local/bin/backup-scripts/
COPY crontab  /etc/crontabs/root

RUN chmod +x /usr/local/bin/backup-scripts/*.sh \
    && chmod 0600 /etc/crontabs/root

# crond -f: foreground (PID-1 friendly).
# crond -d 8: log level 8 = INFO (job start/end visible in `docker compose logs`).
# tini reaps zombie children from forked cron jobs (pg_dumpall pipelines
# spawn `gzip` and `aws` subprocesses that crond itself doesn't wait on).
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["crond", "-f", "-d", "8"]
