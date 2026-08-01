"""ConfigManager robustness.

`ConfigManager()` runs inside `MainWindow.__init__`, so anything it raises stops the
application from starting with no GUI available to repair the damage. These tests pin
that every shape of broken config degrades to defaults instead.
"""

import json
import os

import pytest

from pyql3.services.config import ConfigManager


def _config_at(tmp_path, name="config.json"):
    return str(tmp_path / name)


# ------------------------------------------------------- damaged config files


def test_missing_config_starts_empty(tmp_path):
    cfg = ConfigManager(_config_at(tmp_path))
    assert cfg.config == {}
    assert cfg.get("anything") is None


def test_malformed_json_does_not_raise(tmp_path):
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        handle.write("{not json at all")
    cfg = ConfigManager(path)
    assert cfg.config == {}


def test_non_utf8_bytes_do_not_stop_startup(tmp_path):
    """Previously raised UnicodeDecodeError out of __init__ -- the app could not start."""
    path = _config_at(tmp_path)
    with open(path, "wb") as handle:
        handle.write(b"\xff\xfe\x00\x01 not utf-8")
    cfg = ConfigManager(path)
    assert cfg.config == {}


def test_directory_where_the_config_should_be_does_not_stop_startup(tmp_path):
    """Previously raised IsADirectoryError out of __init__."""
    path = tmp_path / "config.json"
    path.mkdir()
    cfg = ConfigManager(str(path))
    assert cfg.config == {}


def test_valid_json_of_the_wrong_shape_is_rejected(tmp_path):
    """"[1, 2, 3]" parsed cleanly and then broke every .get() with AttributeError."""
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        handle.write("[1, 2, 3]")
    cfg = ConfigManager(path)
    assert cfg.config == {}
    assert cfg.get("recent_files", []) == []  # would previously raise


def test_damaged_config_is_moved_aside_not_destroyed(tmp_path):
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        handle.write("{broken")
    ConfigManager(path)

    spoiled = tmp_path / "config.json.corrupt"
    assert spoiled.exists(), "the damaged file should be preserved for inspection"
    assert spoiled.read_text() == "{broken"


def test_recovery_writes_a_clean_config_after_quarantine(tmp_path):
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        handle.write("{broken")

    cfg = ConfigManager(path)
    cfg.set("polling_dir", "/data/tonight")

    reloaded = ConfigManager(path)
    assert reloaded.get("polling_dir") == "/data/tonight"


# ----------------------------------------------------------------- round trip


def test_set_and_reload(tmp_path):
    path = _config_at(tmp_path)
    cfg = ConfigManager(path)
    cfg.set("polling_interval", 3.5)
    assert ConfigManager(path).get("polling_interval") == 3.5


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = _config_at(tmp_path)
    cfg = ConfigManager(path)
    cfg.set("a", 1)
    cfg.set("b", 2)

    leftovers = [n for n in os.listdir(tmp_path) if n != "config.json"]
    assert leftovers == [], f"temp files left behind: {leftovers}"

    with open(path) as handle:
        assert json.load(handle) == {"a": 1, "b": 2}


def test_failed_save_does_not_truncate_the_existing_config(tmp_path, monkeypatch):
    """The point of writing a sibling temp file: a write that dies partway through
    must leave the previous config intact rather than truncating it."""
    path = _config_at(tmp_path)
    cfg = ConfigManager(path)
    cfg.set("keep", "me")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", explode)
    with pytest.raises(OSError):
        cfg.set("new", "value")

    monkeypatch.undo()
    assert ConfigManager(path).get("keep") == "me", "previous config must survive"
    leftovers = [n for n in os.listdir(tmp_path) if n != "config.json"]
    assert leftovers == [], f"temp file stranded after a failed save: {leftovers}"


# --------------------------------------------------------------- recent files


def test_recent_files_tolerates_a_junk_entry(tmp_path):
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        json.dump({"recent_files": ["/a/b.fits", 17, None, {"x": 1}]}, handle)
    cfg = ConfigManager(path)
    assert cfg.get_recent_files() == ["/a/b.fits"]


def test_recent_files_of_the_wrong_type_yields_empty(tmp_path):
    path = _config_at(tmp_path)
    with open(path, "w") as handle:
        json.dump({"recent_files": "not-a-list"}, handle)
    assert ConfigManager(path).get_recent_files() == []
