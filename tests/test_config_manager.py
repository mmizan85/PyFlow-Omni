"""ConfigManager: atomic YAML load/save, recent-inputs list, validation."""
from __future__ import annotations

import pytest

from pyflow_omni.config_manager import AppConfig, ConfigManager


def test_first_load_creates_defaults_and_writes_file(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    cfg = cm.load()
    assert isinstance(cfg, AppConfig)
    assert cm.config_path.exists()


def test_save_reload_roundtrip(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    cm.update(theme="high-contrast", clipboard_monitor_enabled=True)

    reloaded = ConfigManager(config_dir=config_dir).load()
    assert reloaded.theme == "high-contrast"
    assert reloaded.clipboard_monitor_enabled is True


def test_nested_aria2_defaults_roundtrip(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    cfg = cm.load()
    cfg.aria2_defaults.split = 16
    cfg.aria2_defaults.max_upload_limit = "500K"
    cm.save(cfg)

    reloaded = ConfigManager(config_dir=config_dir).load()
    assert reloaded.aria2_defaults.split == 16
    assert reloaded.aria2_defaults.max_upload_limit == "500K"


def test_update_rejects_unknown_field(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    with pytest.raises(AttributeError):
        cm.update(not_a_real_field=123)


def test_recent_inputs_dedupes_and_moves_to_front(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    cm.add_recent_input("a")
    cm.add_recent_input("b")
    cm.add_recent_input("c")
    cm.add_recent_input("a")  # re-add -> should move to front, not duplicate

    cfg = cm.load()
    assert cfg.recent_inputs == ["a", "c", "b"]


def test_recent_inputs_respects_limit(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    for i in range(30):
        cm.add_recent_input(f"item-{i}", limit=5)
    assert len(cm.load().recent_inputs) == 5
    assert cm.load().recent_inputs[0] == "item-29"


def test_corrupt_config_file_falls_back_to_defaults(config_dir):
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("{ not: valid: yaml: [[[", encoding="utf-8")
    cm = ConfigManager(config_dir=config_dir)
    cfg = cm.load()  # must not raise
    assert isinstance(cfg, AppConfig)
