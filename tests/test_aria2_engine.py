"""Aria2Engine: the magnet GID handoff logic (mocked, runs anywhere), plus a real
end-to-end download through an actual aria2c daemon (skipped if aria2c/network
aren't available — see conftest.py)."""
from __future__ import annotations

import types

import pytest

from pyflow_omni.engines.aria2_engine import Aria2Engine
from pyflow_omni.engines.base import TaskStatus


class _FakeRpcClient:
    """Scripted aria2 RPC responses, so the GID-handoff logic can be tested
    without a real daemon or network — it's pure control flow in Aria2Engine."""

    def __init__(self, status_sequence: dict[str, list[dict]], *, add_uri_gid: str = "meta1") -> None:
        self._sequence = status_sequence
        self._add_uri_gid = add_uri_gid
        self.calls: list[tuple[str, object]] = []

    async def get_version(self):
        return {"version": "fake"}

    async def add_uri(self, uris, options):
        self.calls.append(("add_uri", uris))
        return self._add_uri_gid

    async def tell_status(self, gid, keys=None):
        self.calls.append(("tell_status", gid))
        queue = self._sequence[gid]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def close(self):
        pass

    async def shutdown(self):
        pass


async def test_magnet_follows_metadata_gid_to_content_gid():
    """A magnet finishes *metadata* download under gid A, then aria2 spawns a
    new gid B for the actual torrent content. Engine must follow A -> B
    rather than reporting "complete" after only the metadata."""
    status_sequence = {
        "meta1": [
            {"status": "complete", "followedBy": ["real1"], "totalLength": "0",
             "completedLength": "0", "downloadSpeed": "0", "files": []},
        ],
        "real1": [
            {"status": "active", "totalLength": "1000", "completedLength": "200",
             "downloadSpeed": "50", "files": [{"path": "/tmp/movie.mkv"}]},
            {"status": "complete", "totalLength": "1000", "completedLength": "1000",
             "downloadSpeed": "0", "files": [{"path": "/tmp/movie.mkv"}]},
        ],
    }
    engine = Aria2Engine()
    fake_client = _FakeRpcClient(status_sequence)
    engine._client = fake_client  # bypass real daemon spawn entirely

    config = types.SimpleNamespace(download_dir="/tmp", aria2_session=None)
    updates = []

    async def on_progress(update):
        updates.append(update)

    result = await engine.process("magnet:?xt=urn:btih:AAAA&dn=test", config, on_progress)

    gids_polled = [c[1] for c in fake_client.calls if c[0] == "tell_status"]
    assert gids_polled[0] == "meta1"
    assert "real1" in gids_polled
    assert result.status == TaskStatus.COMPLETE
    assert result.name == "movie.mkv"
    # progress updates must reflect the *content* download, not the
    # near-instant metadata fetch (a UI watching this must see real % progress)
    assert any(u.downloaded_bytes == 200 for u in updates)


async def test_non_magnet_uri_does_not_trigger_followedby_logic():
    status_sequence = {"gid1": [{"status": "complete", "totalLength": "10", "completedLength": "10",
                                  "downloadSpeed": "0", "files": [{"path": "/tmp/f.zip"}]}]}
    engine = Aria2Engine()
    engine._client = _FakeRpcClient(status_sequence, add_uri_gid="gid1")
    config = types.SimpleNamespace(download_dir="/tmp", aria2_session=None)

    async def on_progress(update):
        pass

    result = await engine.process("https://example.com/f.zip", config, on_progress)
    assert result.status == TaskStatus.COMPLETE
    assert result.name == "f.zip"


@pytest.mark.network
async def test_configured_concurrency_limit_reaches_the_spawned_daemon(require_aria2c, require_network):
    """The config field existed and was user-editable before this fix, but
    nothing ever read it when starting aria2c — this confirms the flag
    actually reaches the real spawned process, not just that the
    constructor accepts the argument.

    Checks it via `_ensure_daemon` directly rather than a full `process()`
    call: `process()` correctly auto-shuts-down the daemon once no tasks
    remain (spec'd behavior — see `_maybe_shutdown_daemon`), which would
    tear down the very daemon this test needs to inspect before the
    assertion ever ran.
    """
    engine = Aria2Engine(aria2c_path="aria2c", max_concurrent_downloads=2)
    try:
        client = await engine._ensure_daemon(None)
        assert engine._process is not None, "expected this test to spawn its own daemon"
        result = await client.call("getGlobalOption")
        assert result["max-concurrent-downloads"] == "2"
    finally:
        await engine.shutdown()


@pytest.mark.network
async def test_real_end_to_end_download(require_aria2c, require_network, download_dir):
    """Spawns a real aria2c daemon and downloads a real (small) file over RPC."""
    engine = Aria2Engine(aria2c_path="aria2c")
    config = types.SimpleNamespace(download_dir=str(download_dir), aria2_session=None)
    log_lines = []

    async def on_progress(update):
        pass

    async def on_log(line):
        log_lines.append(line)

    url = "https://raw.githubusercontent.com/git/git/master/README.md"
    try:
        result = await engine.process(url, config, on_progress, on_log)
        assert result.status == TaskStatus.COMPLETE
        assert (download_dir / "README.md").exists()
        assert any("Download complete" in line for line in log_lines)
        assert not any("\x1b" in line for line in log_lines)  # ANSI codes must be stripped
    finally:
        await engine.shutdown()
