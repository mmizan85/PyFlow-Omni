"""SmartRouter: offline classification of magnet/torrent/HTTP/media-URL/batch inputs."""
from __future__ import annotations

import pytest

from pyflow_omni.router import InputKind, SmartRouter


@pytest.fixture(scope="module")
def router():
    return SmartRouter()  # extractor table is loaded once and reused (module-scoped)


@pytest.mark.parametrize(
    "text,expected_kind,expected_engine",
    [
        ("magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12&dn=x", InputKind.MAGNET, "aria2"),
        ("MAGNET:?xt=urn:btih:AAAA", InputKind.MAGNET, "aria2"),  # case-insensitive
        ("https://example.com/some/file.zip", InputKind.HTTP_URL, "aria2"),
        ("ftp://files.example.com/archive.tar.gz", InputKind.HTTP_URL, "aria2"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", InputKind.MEDIA_URL, "ytdlp"),
        ("https://vimeo.com/76979871", InputKind.MEDIA_URL, "ytdlp"),
        ("https://soundcloud.com/artist/track", InputKind.MEDIA_URL, "ytdlp"),
        ("not a url at all", InputKind.UNKNOWN, None),
        ("", InputKind.UNKNOWN, None),
    ],
)
def test_classify(router, text, expected_kind, expected_engine):
    decision = router.classify(text)
    assert decision.kind == expected_kind
    assert decision.engine == expected_engine


def test_local_torrent_path_routes_to_aria2(router):
    decision = router.classify("/home/user/downloads/ubuntu-24.04.torrent")
    assert decision.kind == InputKind.TORRENT_FILE
    assert decision.engine == "aria2"


def test_nonexistent_txt_path_is_not_treated_as_batch(router):
    # Only *existing* .txt files count as batch files — a bare string that
    # happens to end in .txt but isn't a real path should fall through
    # rather than being silently swallowed.
    decision = router.classify("some-video-called-notes.txt")
    assert decision.kind != InputKind.BATCH_FILE


def test_existing_txt_file_routes_to_batch(router, tmp_path):
    batch_file = tmp_path / "links.txt"
    batch_file.write_text("https://example.com/a.zip\n")
    decision = router.classify(str(batch_file))
    assert decision.kind == InputKind.BATCH_FILE


async def test_read_batch_file_skips_comments_and_blanks(router, tmp_path):
    batch_file = tmp_path / "batch.txt"
    batch_file.write_text(
        "# a comment\n"
        "\n"
        "https://example.com/a.zip\n"
        "   \n"
        "# another comment\n"
        "magnet:?xt=urn:btih:AAAA\n"
    )
    entries = await router.read_batch_file(str(batch_file))
    assert entries == ["https://example.com/a.zip", "magnet:?xt=urn:btih:AAAA"]


async def test_classify_batch_routes_each_entry(router, tmp_path):
    batch_file = tmp_path / "batch.txt"
    batch_file.write_text(
        "https://example.com/a.zip\n"
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
        "magnet:?xt=urn:btih:AAAA\n"
    )
    decisions = await router.classify_batch(str(batch_file))
    assert [d.engine for d in decisions] == ["aria2", "ytdlp", "aria2"]


async def test_classify_async_matches_sync_result(router):
    sync_result = router.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    async_result = await router.classify_async("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert sync_result.kind == async_result.kind
    assert sync_result.engine == async_result.engine
