# syntax=docker/dockerfile:1.6
#
# Daily PostHog events export container.
#
# Mirrors the operational shape of `docker/backup/` (crond -f as PID 1 via
# tini, sentinel files in /var/run/events-export/, healthcheck.sh consumed
# by compose) — different cargo:
#
#   * docker/backup/         pg_dumpall + ClickHouse BACKUP TO S3() to GCS,
#                            for disaster recovery (zip).
#   * docker/posthog-events- streams CH HTTP and writes Mixpanel-shaped
#     export/                JSONL.gz to a separate "forever" bucket, for
#                            analytical archive + round-trip imports.
#
# Auth: GCS HMAC interop pair (EVENTS_EXPORT_GCS_HMAC_KEY +
# EVENTS_EXPORT_GCS_HMAC_SECRET). One pair is used by `s3fs` pointed at the
# GCS interop endpoint (`https://storage.googleapis.com`). fsspec's `gcsfs`
# does NOT support HMAC, so we always go through s3fs from inside this
# container — `export-events.sh` rewrites the operator-friendly `gs://...`
# bucket form to `s3://...` + endpoint at invocation time.
#
# Build context is the REPO ROOT (not docker/posthog-events-export/) so we
# can COPY both the bash scripts under docker/posthog-events-export/scripts/
# AND the python module under ee/scripts/posthog_events_export/. The
# compose service block sets `build.context: .` to make this work.
#
# Image size note: python:3.12-alpine + fsspec/gcsfs/s3fs/requests is
# ~150 MB. Compared to docker/backup/'s 80 MB image: extra ~70 MB is the
# python runtime + fsspec dep tree. Acceptable for a once-a-day cron
# container.
#

FROM python:3.12-alpine

RUN apk add --no-cache \
        bash \
        coreutils \
        util-linux \
        tini \
        ca-certificates \
        tzdata \
    && mkdir -p /var/run/events-export /etc/crontabs

# Python deps from the same requirements.txt the script ships with —
# keeps laptop/`hogli`/container behavior identical. Compile to .pyc for
# faster cold start under cron.
COPY ee/scripts/posthog_events_export/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Bash scripts (cron entrypoint, status, verify, healthcheck).
COPY docker/posthog-events-export/scripts/ /usr/local/bin/events-export-scripts/
COPY docker/posthog-events-export/crontab  /etc/crontabs/root

# Python script (sibling of mixpanel_export, mounted at a stable path).
COPY ee/scripts/posthog_events_export/ /opt/posthog_events_export/

RUN chmod +x /usr/local/bin/events-export-scripts/*.sh \
    && chmod 0600 /etc/crontabs/root

# Where export-events.sh expects to find the python module.
ENV POSTHOG_EVENTS_EXPORT_DIR=/opt/posthog_events_export

# crond -f: foreground (PID-1 friendly).
# crond -d 8: log level 8 = INFO (job start/end visible in `docker compose logs`).
# tini reaps zombie children from the cron-spawned pipelines (curl + python
# + multipart upload threads).
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["crond", "-f", "-d", "8"]
