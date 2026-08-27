#!/usr/bin/env bash
# Sourced by launch.sh / results.sh. Loads .env and sets S3 flags for BOTH volumes.
#
# SRC_BUCKET is READ-ONLY. Every helper here that touches it uses `aws s3 ls` or
# `cp` FROM it only. Never add a write path against SRC_BUCKET.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$ROOT/.env"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE — copy .env.example and fill it in" >&2; exit 1; }
# Load .env WITHOUT clobbering anything the caller already exported, so
# `RUNPOD_VCPU=8 scripts/launch.sh` actually takes effect.
_saved=$(export -p)
set -a; . "$ENV_FILE"; set +a
eval "$_saved"; unset _saved

: "${AWS_ACCESS_KEY_ID:?set in .env}"
: "${AWS_SECRET_ACCESS_KEY:?set in .env}"
: "${RUNPOD_API_KEY:?account rpa_ key, set in .env}"

SOURCE_VOLUME_ID="${SOURCE_VOLUME_ID:-8qik4zxpxq}"
RESULTS_VOLUME_ID="${RESULTS_VOLUME_ID:-x3n7kgbbit}"
S3_REGION="${RUNPOD_S3_REGION:-eu-ro-1}"
S3_ENDPOINT="${RUNPOD_S3_ENDPOINT:-https://s3api-eu-ro-1.runpod.io}"
DC="${RUNPOD_DATACENTER:-EU-RO-1}"

if [ "$RESULTS_VOLUME_ID" = "$SOURCE_VOLUME_ID" ]; then
  echo "FATAL: RESULTS_VOLUME_ID equals the read-only source volume" >&2; exit 2
fi

S3FLAGS="--region $S3_REGION --endpoint-url $S3_ENDPOINT"
SRC_BUCKET="s3://$SOURCE_VOLUME_ID"      # read-only
DST_BUCKET="s3://$RESULTS_VOLUME_ID"     # writable

command -v aws >/dev/null || { echo "aws CLI not found — install awscli" >&2; exit 1; }
