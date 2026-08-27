"""Configuration: YAML experiment settings + environment credentials."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# The source volume is READ-ONLY, always. It is declared here as a constant so the
# guard in storage.py has a single authority to compare against.
SOURCE_VOLUME_ID = "8qik4zxpxq"


@dataclass(frozen=True)
class Env:
    access_key: str
    secret_key: str
    endpoint: str
    region: str
    source_volume: str
    results_volume: str

    @staticmethod
    def load() -> "Env":
        src = os.environ.get("SOURCE_VOLUME_ID", SOURCE_VOLUME_ID)
        dst = os.environ.get("RESULTS_VOLUME_ID", "x3n7kgbbit")
        if src != SOURCE_VOLUME_ID:
            raise RuntimeError(
                f"SOURCE_VOLUME_ID is pinned to {SOURCE_VOLUME_ID}; got {src!r}")
        if dst == SOURCE_VOLUME_ID:
            raise RuntimeError("RESULTS_VOLUME_ID must not be the read-only source volume")
        missing = [k for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
                   if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"missing env: {', '.join(missing)} (see .env.example)")
        return Env(
            access_key=os.environ["AWS_ACCESS_KEY_ID"],
            secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            endpoint=os.environ.get("RUNPOD_S3_ENDPOINT", "https://s3api-eu-ro-1.runpod.io"),
            region=os.environ.get("RUNPOD_S3_REGION", "eu-ro-1"),
            source_volume=src,
            results_volume=dst,
        )


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config" / "experiment.yaml"
    with open(p) as fh:
        return yaml.safe_load(fh)


def load_tickers(cfg: dict) -> list[str]:
    p = ROOT / cfg["data"]["tickers_file"]
    with open(p) as fh:
        return sorted(json.load(fh)["stocks"])
