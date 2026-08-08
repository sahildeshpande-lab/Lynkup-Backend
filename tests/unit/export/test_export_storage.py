from __future__ import annotations

from pathlib import Path

from apps.export.storage import LocalExportStorage


def test_local_export_storage_roundtrip(export_tmp_path: Path):
    storage = LocalExportStorage(base_path=export_tmp_path)
    key = "exports/abc.zip"
    assert storage.save(key, b"hello") == key
    assert storage.exists(key)
    with storage.open(key) as handle:
        assert handle.read() == b"hello"
    abs_path = storage.absolute_path(key)
    assert abs_path is not None
    assert abs_path.read_bytes() == b"hello"
    storage.delete(key)
    assert not storage.exists(key)


def test_local_export_storage_rejects_path_traversal(export_tmp_path: Path):
    storage = LocalExportStorage(base_path=export_tmp_path)
    try:
        storage.save("../evil.zip", b"nope")
        raised = False
    except ValueError:
        raised = True
    assert raised
