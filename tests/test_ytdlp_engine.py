"""YtdlpEngine: preset option-building and format-selector validation.

These run entirely offline — building yt-dlp option dicts and letting
yt-dlp itself validate/parse them (postprocessor keys, format selector
syntax) doesn't require reaching any video platform.
"""
from __future__ import annotations

import shutil

import pytest
import yt_dlp

from pyflow_omni.engines.ytdlp_engine import PRESET_LABELS, YtdlpEngine, _preset_opts, list_formats


@pytest.mark.parametrize("preset", sorted(PRESET_LABELS))
def test_preset_builds_and_yt_dlp_accepts_it(preset, tmp_path):
    opts = _preset_opts(preset, str(tmp_path / "%(title)s.%(ext)s"), "ffmpeg")
    opts = {**opts, "quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        selector = ydl.build_format_selector(opts["format"])
        assert selector is not None


def test_preset_5_embeds_id3_tags_and_thumbnail():
    opts = _preset_opts(5, "%(title)s.%(ext)s", "ffmpeg")
    pp_keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in pp_keys
    assert "EmbedThumbnail" in pp_keys
    assert opts["writethumbnail"] is True


def test_preset_6_is_lossless():
    opts = _preset_opts(6, "%(title)s.%(ext)s", "ffmpeg")
    codec = next(p["preferredcodec"] for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert codec == "flac"


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        _preset_opts(99, "%(title)s.%(ext)s", "ffmpeg")


def test_list_formats_flattens_and_labels_audio_only():
    info = {"formats": [
        {"format_id": "137", "ext": "mp4", "resolution": "1920x1080", "vcodec": "avc1", "acodec": "none"},
        {"format_id": "140", "ext": "m4a", "format_note": "audio only", "vcodec": "none", "acodec": "mp4a"},
    ]}
    flat = list_formats(info)
    assert flat[0]["resolution"] == "1920x1080"
    assert flat[1]["resolution"] == "audio only"


def test_list_formats_handles_missing_formats_key():
    assert list_formats({}) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_engine_resolves_bare_ffmpeg_command_to_absolute_path():
    # yt-dlp's own ffmpeg-exists check does a literal path check rather than
    # a PATH search, so a bare "ffmpeg" would otherwise trip a spurious
    # "ffmpeg-location does not exist" warning even when it's reachable.
    engine = YtdlpEngine(ffmpeg_path="ffmpeg")
    assert engine._ffmpeg_path.startswith("/") or ":" in engine._ffmpeg_path  # absolute, POSIX or Windows


def test_engine_leaves_an_already_absolute_or_unresolvable_path_alone():
    engine = YtdlpEngine(ffmpeg_path="/definitely/not/a/real/path/ffmpeg")
    assert engine._ffmpeg_path == "/definitely/not/a/real/path/ffmpeg"
