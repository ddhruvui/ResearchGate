"""The read-only volume must be structurally unwritable from this codebase."""
from __future__ import annotations

import pytest

from src.config import SOURCE_VOLUME_ID, Env
from src.storage import LayeredSource, ResultStore, SourceStore


def test_source_store_exposes_no_write_methods():
    forbidden = {"put_object", "put_bytes", "put_json", "put_text", "put_dataframe",
                 "delete", "delete_object", "upload_file", "copy", "copy_object"}
    for cls in (SourceStore, LayeredSource):
        present = forbidden & set(dir(cls))
        assert not present, f"{cls.__name__} must not expose write methods, found {present}"


class _NoSuchKey(Exception):
    pass


class _StubPrimary:
    """SourceStore stand-in: holds some keys, raises NoSuchKey for the rest."""
    def __init__(self, blobs):
        self._blobs = blobs

    def get_bytes(self, key):
        if key not in self._blobs:
            raise _NoSuchKey(key)
        return self._blobs[key]


class _StubExtClient:
    def __init__(self, blobs):
        self._blobs = blobs

    def get_object(self, Bucket, Key):
        import io
        if Key not in self._blobs:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self._blobs[Key])}


def _layered(primary_blobs, ext_blobs):
    ls = LayeredSource.__new__(LayeredSource)
    ls._source = _StubPrimary(primary_blobs)
    ls._s3 = _StubExtClient(ext_blobs)
    ls._ext_bucket = "ext"
    return ls


def test_layered_source_prefers_the_source_volume():
    ls = _layered({"data/T.json": b'{"who": "source"}'},
                  {"data/T.json": b'{"who": "ext"}'})
    assert ls.get_json("data/T.json") == {"who": "source"}


def test_layered_source_falls_back_to_staged_ext_data():
    ls = _layered({}, {"data/T.json": b'{"who": "ext"}'})
    assert ls.get_json("data/T.json") == {"who": "ext"}


def test_layered_source_absent_on_both_volumes_is_none():
    ls = _layered({}, {})
    assert ls.try_get_json("data/T.json") is None


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
