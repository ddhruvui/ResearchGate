"""Stage vendor files for tickers OUTSIDE the acquisition pipeline's universe.

The acquisition pipeline (InvestOpediaClaude) publishes data/<T>.json and
data/splits/<T>.json for its own universe to the read-only source volume.
Tickers in tickers.json that it does not cover are fetched here straight from
EODHD and staged on the RESULTS volume under the same keys, where LayeredSource
picks them up. The source volume is never touched.

Idempotent: a re-run refetches the full history and overwrites the staged file,
so it doubles as the daily refresh for these tickers (run it before the daily
pipeline, or staged bars go stale and their predictions stop grading).

  python -m src.fetch_ext              # stage every ticker missing from the source
  python -m src.fetch_ext --dry-run    # show what would be fetched

Needs EODHD_API_TOKEN in the environment (see .env.example). Local tool — the
pods never run this.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import ROOT, Env, load_config, load_tickers
from .storage import ResultStore, SourceStore

_EOD_COLS = {"date", "open", "high", "low", "close", "adjusted_close", "volume"}


def _source_tickers(src: SourceStore, prefix: str) -> set[str]:
    """Tickers with an EOD file directly under <prefix>/ (not in a subfolder)."""
    out = set()
    plen = len(prefix) + 1
    for key in src.list_keys(prefix + "/"):
        rest = key[plen:]
        if rest.endswith(".json") and "/" not in rest:
            out.add(rest[:-len(".json")])
    return out


def _get(session, url: str, params: dict):
    r = session.get(url, params=params, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        import requests
    except ImportError:
        print("fetch_ext needs the `requests` package (pip install requests)",
              file=sys.stderr)
        return 1

    token = os.environ.get("EODHD_API_TOKEN")
    if not token:
        print("EODHD_API_TOKEN not set (see .env.example)", file=sys.stderr)
        return 1

    cfg = load_config(args.config)
    env = Env.load()
    src, dst = SourceStore(env), ResultStore(env)
    prefix = cfg["data"]["source_prefix"]
    splits_prefix = cfg["data"]["splits_prefix"]

    with open(ROOT / cfg["data"]["tickers_file"]) as fh:
        start = json.load(fh).get("from", "2000-01-01")

    want = load_tickers(cfg)
    have = _source_tickers(src, prefix)
    ext = [t for t in want if t not in have]
    print(f"universe {len(want)} | on source volume {len(want) - len(ext)} | "
          f"to stage on {dst.bucket}: {len(ext)}", flush=True)
    if not ext:
        return 0
    if args.dry_run:
        print(" ".join(ext))
        return 0

    session = requests.Session()
    failed = []
    for k, t in enumerate(ext, 1):
        params = {"api_token": token, "fmt": "json"}
        try:
            rows = _get(session, f"https://eodhd.com/api/eod/{t}.US",
                        {**params, "from": start, "order": "a"})
            if not rows or not _EOD_COLS.issubset(rows[0]):
                raise ValueError(f"unusable EOD payload: {str(rows)[:80]}")
            splits = _get(session, f"https://eodhd.com/api/splits/{t}.US", params)
        except Exception as exc:
            failed.append(t)
            print(f"[{k:2d}/{len(ext)}] {t:6} FAILED: {exc}", flush=True)
            continue
        dst.put_json(f"{prefix}/{t}.json", rows)
        if splits:
            dst.put_json(f"{splits_prefix}/{t}.json", splits)
        print(f"[{k:2d}/{len(ext)}] {t:6} {len(rows):5d} bars "
              f"{rows[0]['date']} .. {rows[-1]['date']}"
              f"{'  +' + str(len(splits)) + ' splits' if splits else ''}", flush=True)

    if failed:
        print(f"\n{len(failed)} ticker(s) failed: {' '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nstaged {len(ext)} tickers under s3://{dst.bucket}/{prefix}/", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
