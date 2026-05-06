FROM python:3.12-slim
WORKDIR /app

# tini gives the script a real PID 1 so SIGTERM (cron, k8s, Cloud Run) cleanly
# stops the in-flight HTTP stream + flushes/cleans the temp gzip file.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY export_daily.py __init__.py ./

ENTRYPOINT ["/usr/bin/tini", "--", "python", "export_daily.py"]
