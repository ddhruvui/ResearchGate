"""S3 access to the two RunPod network volumes.

HARD RULE: volume 8qik4zxpxq is READ-ONLY. It is enforced three ways —

  1. `SourceStore` exposes ONLY get/list. It has no put, copy, or delete method,
     so there is no code path through which this program can write to it.
  2. `ResultStore.__init__` refuses to construct against the source volume id.
  3. The pod never MOUNTS the source volume (see scripts/launch.sh: networkVolumeId
     is the results volume), so no filesystem write can reach it either.

Anything extra you want alongside the source data — derived columns, caches,
diagnostics — goes to the results volume.
"""
from __future__ import annotations

import io
import json
from typing import Iterator

import boto3
from botocore.config import Config as BotoConfig

from .config import SOURCE_VOLUME_ID, Env


def _client(env: Env):
    return boto3.client(
        "s3",
        aws_access_key_id=env.access_key,
        aws_secret_access_key=env.secret_key,
        endpoint_url=env.endpoint,
        region_name=env.region,
        config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"},
                          max_pool_connections=32),
    )


class SourceStore:
    """READ-ONLY view of the market-data volume. Deliberately has no write methods."""

    def __init__(self, env: Env):
        self._s3 = _client(env)
        self._bucket = env.source_volume

    @property
    def bucket(self) -> str:
        return self._bucket

    def get_bytes(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def get_json(self, key: str):
        return json.loads(self.get_bytes(key))

    def try_get_json(self, key: str):
        """None when the key is absent (e.g. a ticker with no split history)."""
        try:
            return self.get_json(key)
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:                      # botocore 404 variants
            if "NoSuchKey" in type(exc).__name__ or "404" in str(exc):
                return None
            raise

    def list_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]


class ResultStore:
    """Writable view of the results volume. Never the source."""

    def __init__(self, env: Env):
        if env.results_volume == SOURCE_VOLUME_ID:
            raise RuntimeError(
                f"refusing to write to {SOURCE_VOLUME_ID}: it is the read-only source volume")
        self._s3 = _client(env)
        self._bucket = env.results_volume

    @property
    def bucket(self) -> str:
        return self._bucket

    def put_bytes(self, key: str, blob: bytes, content_type: str = "application/octet-stream"):
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=blob, ContentType=content_type)

    def put_json(self, key: str, obj) -> None:
        self.put_bytes(key, json.dumps(obj, indent=2, default=str).encode(), "application/json")

    def put_text(self, key: str, text: str) -> None:
        self.put_bytes(key, text.encode(), "text/plain")

    def put_dataframe(self, key: str, df) -> None:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, compression="snappy")
        self.put_bytes(key, buf.getvalue(), "application/octet-stream")
