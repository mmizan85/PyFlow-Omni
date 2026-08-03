"""Shared fixtures.

Two things every test here needs to not step on: the user's real
~/.config/pyflow_omni (fixed via the `config_dir` fixture, never the
default), and the real state of the machine running the suite — aria2c
might not be installed, and outbound network might be firewalled. The
`require_aria2c` / `require_network` fixtures skip cleanly rather than
failing when either is missing.
"""
from __future__ import annotations

import shutil
import socket

import pytest


@pytest.fixture
def config_dir(tmp_path):
    """An isolated config directory so tests never touch the real one."""
    return tmp_path / "config"


@pytest.fixture
def download_dir(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return d


@pytest.fixture(scope="session")
def require_aria2c():
    if shutil.which("aria2c") is None:
        pytest.skip("aria2c not on PATH — install it to run this test (see README)")


@pytest.fixture(scope="session")
def require_network():
    """Skip cleanly on boxes with no real internet egress (e.g. sandboxed CI)."""
    try:
        socket.create_connection(("raw.githubusercontent.com", 443), timeout=3).close()
    except OSError:
        pytest.skip("no network egress to raw.githubusercontent.com")
