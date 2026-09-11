#!/usr/bin/env bash
# Pod entrypoint. The RESULTS volume (x3n7kgbbit) is mounted at /workspace.
# The SOURCE volume (8qik4zxpxq) is NOT mounted — it is reached only through the
# S3 API, read-only, so no filesystem write can ever land on it.
set +e

LOGDIR=/workspace/_pod_logs
mkdir -p "$LOGDIR" 2>/dev/null
LOG="$LOGDIR/$(date -u +%Y%m%dT%H%M%SZ)-${RUNPOD_POD_ID:-nopod}.log"
exec > >(tee -a "$LOG") 2>&1
echo "bootstrap start $(date -u +%FT%TZ) pod=${RUNPOD_POD_ID:-?}"
python -c 'import sys; print("python", sys.version)'

WORK=/workspace/app
rm -rf "$WORK"; mkdir -p "$WORK"
tar xzf /workspace/code/bundle.tar.gz -C "$WORK" || { echo "!! bundle extract failed"; }
cd "$WORK" || exit 1
ls -la

echo "installing deps ..."
timeout 900 python -m pip install --quiet --no-input --disable-pip-version-check -r requirements.txt \
  || echo "!! pip install failed"

# One BLAS thread per process: we parallelise across tickers, so per-process
# thread pools only oversubscribe cores and RAM (observed OOM at 2 vCPU).
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
       NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
MEMB=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo "?")
case "$MEMB" in ''|max|?) MEMSTR="unknown" ;; *) MEMSTR="$((MEMB/1024/1024))MB" ;; esac
echo "cpus=$(nproc 2>/dev/null || echo ?) mem=${MEMSTR} (cgroup limit)"
echo "starting run: mode=${RUN_MODE:-both} limit=${RUN_LIMIT:-0} workers=${WORKERS:-auto}"
# `daily` is the incremental path (grade -> learn -> guess); everything else is
# the full walk-forward in src/run.py.
if [ "${RUN_MODE:-both}" = "daily" ]; then
  MODULE="src.daily"; ARGS=""
else
  MODULE="src.run"; ARGS="--mode ${RUN_MODE:-both}"
fi
[ "${RUN_LIMIT:-0}" != "0" ] && ARGS="$ARGS --limit ${RUN_LIMIT}"
timeout "${WATCHDOG_SEC:-25200}" python -m "$MODULE" $ARGS
ec=$?
echo "run exit=$ec ($([ $ec -eq 124 ] && echo 'WATCHDOG TIMEOUT' || echo exited)) $(date -u +%FT%TZ)"
sync 2>/dev/null

# Refresh the $10k stop-loss paper trade over the predictions this run just wrote.
# It only READS latest/predictions.parquet and writes its own keys, so a failure
# here cannot affect the run that already landed — hence no change to $ec.
if [ "$ec" -eq 0 ] && { [ "${RUN_MODE:-both}" = "daily" ] || [ "${SHARDS:-1}" -le 1 ]; }; then
  echo "computing stop-loss paper trade ..."
  timeout 1800 python -m src.strategy || echo "!! strategy failed (the run itself succeeded)"
fi

# Publish latest/ to MongoDB for the deployed dashboard. Only when this pod wrote
# latest/ itself (a daily run, or an unsharded full run); a sharded backtest is
# merged and published from the laptop by src.merge. The run has already landed
# on the volume, so a Mongo failure is never fatal.
if [ "$ec" -eq 0 ] && [ -n "${MONGO_URI:-}" ] && { [ "${RUN_MODE:-both}" = "daily" ] || [ "${SHARDS:-1}" -le 1 ]; }; then
  FULL=""; [ "${RUN_MODE:-both}" != "daily" ] && FULL="--full"
  echo "publishing latest/ to MongoDB ${FULL} ..."
  timeout 900 python -m src.publish_mongo $FULL || echo "!! mongo publish failed (the run itself succeeded)"
fi

# Self-terminate with the ACCOUNT key; the pod-injected key 403s on DELETE.
for attempt in $(seq 1 12); do
  timeout 60 python - <<'PY'
import os, ssl, sys, urllib.error, urllib.request as u
pid = os.environ.get("RUNPOD_POD_ID", ""); key = os.environ.get("RUNPOD_TERMINATE_KEY", "")
if not pid or not key:
    print("MISSING RUNPOD_POD_ID or RUNPOD_TERMINATE_KEY", file=sys.stderr); sys.exit(2)
url = "https://rest.runpod.io/v1/pods/" + pid
for ctx in (None, ssl._create_unverified_context()):
    try:
        req = u.Request(url, method="DELETE"); req.add_header("Authorization", "Bearer " + key)
        st = u.urlopen(req, timeout=30, context=ctx).status
        if st == 204: print("terminated (204)"); sys.exit(0)
        print("unexpected terminate status:", st, file=sys.stderr)
    except urllib.error.HTTPError as e:
        if e.code == 404: print("already gone (404)"); sys.exit(0)
        print("terminate HTTPError:", e.code, file=sys.stderr)
    except Exception as e:
        print("terminate error:", e, file=sys.stderr)
sys.exit(1)
PY
  [ $? -eq 0 ] && exit 0
  echo "terminate attempt $attempt unconfirmed — retrying in 20s"; sleep 20
done
echo "!! TERMINATION NOT CONFIRMED — kill pod ${RUNPOD_POD_ID:-?} manually"
sleep 30
