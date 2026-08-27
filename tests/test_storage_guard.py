"""The read-only volume must be structurally unwritable from this codebase."""
from __future__ import annotations

import pytest

from src.config import SOURCE_VOLUME_ID, Env
from src.storage import ResultStore, SourceStore


def test_source_store_exposes_no_write_methods():
    forbidden = {"put_object", "put_bytes", "put_json", "put_text", "put_dataframe",
                 "delete", "delete_object", "upload_file", "copy", "copy_object"}
    present = forbidden & set(dir(SourceStore))
    assert not present, f"SourceStore must not expose write methods, found {present}"


def test_result_store_refuses_the_source_volume(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    env = Env(access_key="x", secret_key="y", endpoint="https://e", region="r",
              source_volume=SOURCE_VOLUME_ID, results_volume=SOURCE_VOLUME_ID)
    with pytest.raises(RuntimeError, match="read-only source volume"):
        ResultStore(env)


def test_env_rejects_results_equal_to_source(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    monkeypatch.setenv("RESULTS_VOLUME_ID", SOURCE_VOLUME_ID)
    with pytest.raises(RuntimeError, match="must not be the read-only source"):
        Env.load()


def test_env_rejects_a_repointed_source(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    monkeypatch.setenv("SOURCE_VOLUME_ID", "someothervol")
    with pytest.raises(RuntimeError, match="pinned"):
        Env.load()
