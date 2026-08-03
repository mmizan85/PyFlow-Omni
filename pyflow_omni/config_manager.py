"""Global configuration manager: load/save the persistent YAML settings file.

Everything here is *permanent* settings (spec 4.6). Per-batch overrides
(Session Quick Config) live next to the aria2 engine instead and are never
written to disk.
"""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .utils.file_utils import atomic_write_text

APP_NAME = "pyflow_omni"


def default_config_dir() -> Path:
    """Platform-appropriate config directory (~/.config/pyflow_omni on POSIX)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return Path(base) / APP_NAME if base else Path.home() / APP_NAME
    return Path.home() / ".config" / APP_NAME


def default_download_dir() -> Path:
    home = Path.home()
    downloads = home / "Downloads"
    return downloads if downloads.exists() else home


@dataclass
class Aria2Defaults:
    """Permanent aria2 defaults; a session may override any of these temporarily."""

    max_concurrent_downloads: int = 5
    max_connection_per_server: int = 8
    split: int = 8
    max_overall_download_limit: str = "0"  # aria2 syntax, "0" = unlimited, "5M" etc.
    max_upload_limit: str = "1M"
    seed_ratio: float = 1.0


@dataclass
class BandwidthRule:
    """One time-window speed cap for the Bandwidth Scheduler (spec 4.8)."""

    start: str = "09:00"  # 24h "HH:MM", local time
    end: str = "18:00"
    limit: str = "2M"  # aria2 syntax


@dataclass
class AppConfig:
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    aria2c_path: str = "aria2c"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ytdlp_output_template: str = "%(title)s [%(id)s].%(ext)s"
    ytdlp_max_concurrent: int = 3
    theme: str = "midnight"  # built-in: midnight, high-contrast
    proxy_http: str = ""
    proxy_socks5: str = ""
    notification_command: str = ""  # empty = auto-detect per platform
    clipboard_monitor_enabled: bool = False
    high_contrast: bool = False
    aria2_defaults: Aria2Defaults = field(default_factory=Aria2Defaults)
    bandwidth_rules: List[BandwidthRule] = field(default_factory=list)
    recent_inputs: List[str] = field(default_factory=list)


class ConfigManager:
    """Loads/saves `AppConfig` from YAML with atomic, crash-safe writes.

    Reads/writes are synchronous (a few KB on local disk) but callers on the
    event loop's hot path should still push them through an executor.
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = config_dir or default_config_dir()
        self.config_path = self.config_dir / "config.yaml"
        self._config: Optional[AppConfig] = None

    def load(self, *, force: bool = False) -> AppConfig:
        if self._config is not None and not force:
            return self._config
        if not self.config_path.exists():
            self._config = AppConfig()
            self.save(self._config)
            return self._config
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            raw = {}
        self._config = _config_from_dict(raw)
        return self._config

    def save(self, config: Optional[AppConfig] = None) -> None:
        config = config or self._config or AppConfig()
        self._config = config
        text = yaml.safe_dump(asdict(config), sort_keys=False, allow_unicode=True)
        atomic_write_text(self.config_path, text)

    def update(self, **changes: Any) -> AppConfig:
        """Set one or more top-level fields and persist immediately."""
        config = self.load()
        for key, value in changes.items():
            if not hasattr(config, key):
                raise AttributeError(f"Unknown config field: {key}")
            setattr(config, key, value)
        self.save(config)
        return config

    def add_recent_input(self, value: str, *, limit: int = 20) -> None:
        config = self.load()
        items = [value] + [v for v in config.recent_inputs if v != value]
        config.recent_inputs = items[:limit]
        self.save(config)


def _config_from_dict(raw: Dict[str, Any]) -> AppConfig:
    raw = copy.deepcopy(raw)
    aria2_raw = raw.pop("aria2_defaults", {}) or {}
    bandwidth_raw = raw.pop("bandwidth_rules", []) or []
    known_fields = set(AppConfig.__dataclass_fields__)
    filtered = {k: v for k, v in raw.items() if k in known_fields}
    config = AppConfig(**filtered)
    config.aria2_defaults = Aria2Defaults(
        **{k: v for k, v in aria2_raw.items() if k in Aria2Defaults.__dataclass_fields__}
    )
    config.bandwidth_rules = [
        BandwidthRule(**{k: v for k, v in item.items() if k in BandwidthRule.__dataclass_fields__})
        for item in bandwidth_raw
    ]
    return config
