"""yt-dlp-backed engine: media extraction, 8 quality presets, and Manual Mode.

`yt_dlp.YoutubeDL` is synchronous, so every call into it runs in the
default executor. Its `progress_hooks` fire on that *executor thread*, not
the event loop — `asyncio.run_coroutine_threadsafe` is what safely gets a
progress update back onto the loop (and, from there, onto the TUI).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import DownloadResult, Engine, LogCallback, ProgressCallback, ProgressUpdate, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class YtdlpChoice:
    """What the yt-dlp Selection screen (spec 4.4) resolved to for one input."""

    preset: int = 1  # 1-8, see _PRESET_LABELS below
    clip_start: Optional[float] = None  # seconds, preset 8 only
    clip_end: Optional[float] = None
    manual_format: Optional[str] = None  # explicit yt-dlp format string from Manual Mode
    playlist_items: Optional[str] = None  # e.g. "1,3,5-7" from the playlist checkbox list


PRESET_LABELS: Dict[int, str] = {
    1: "Original Quality (best video+audio, no conversion)",
    2: "PC/TV — up to 4K MP4 (H.264/H.265)",
    3: "Smartphone — 720p/1080p MP4",
    4: "Feature Phone — 320x240 3GP (H.263/AAC)",
    5: "Audio Best — 320kbps MP3 + ID3 tags",
    6: "Lossless Audio — FLAC",
    7: "Compact Archive — HEVC 1080p + Opus MKV",
    8: "Clip — download only a start/end portion",
}


def list_formats(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten yt-dlp's raw format list into simple dicts for the Manual Mode screen."""
    formats = info.get("formats") or []
    return [
        {
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "resolution": f.get("resolution") or f.get("format_note") or "audio only",
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "tbr": f.get("tbr"),
        }
        for f in formats
    ]


def _preset_opts(preset: int, out_template: str, ffmpeg_path: str) -> Dict[str, Any]:
    common: Dict[str, Any] = {"outtmpl": out_template, "ffmpeg_location": ffmpeg_path}

    if preset == 1:
        return {**common, "format": "bestvideo+bestaudio/best", "merge_output_format": "mkv"}
    if preset == 2:
        return {
            **common,
            "format": "bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        }
    if preset == 3:
        return {
            **common,
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best",
            "merge_output_format": "mp4",
        }
    if preset == 4:
        # yt-dlp/ffmpeg have no native H.263 postprocessor, so this only
        # fetches a small source; the real 3GP/H.263 transcode is an
        # explicit ffmpeg pass afterwards (_transcode_feature_phone).
        return {**common, "format": "worst[height>=240]/worst"}
    if preset == 5:
        return {
            **common,
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
        }
    if preset == 6:
        return {
            **common,
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "flac"},
                {"key": "FFmpegMetadata"},
            ],
        }
    if preset == 7:
        # Source fetch; HEVC/Opus re-encode is a separate ffmpeg pass
        # (_transcode_compact_archive) so CRF/bitrate stay under our control.
        return {
            **common,
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mkv",
        }
    if preset == 8:
        return {**common, "format": "bestvideo+bestaudio/best", "merge_output_format": "mp4"}
    raise ValueError(f"Unknown preset: {preset}")


async def _ffmpeg_transcode(ffmpeg_path: str, src: Path, dst: Path, args: List[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_path, "-y", "-i", str(src), *args, str(dst),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg transcode failed: {stderr.decode(errors='replace')[-500:]}")


async def _transcode_feature_phone(ffmpeg_path: str, src: Path) -> Path:
    """Preset 4: downscale to 320x240, H.263 video, low-bitrate AAC, 3GP container."""
    dst = src.with_suffix(".3gp")
    await _ffmpeg_transcode(ffmpeg_path, src, dst, [
        "-vf", "scale=320:240", "-c:v", "h263", "-b:v", "128k",
        "-c:a", "aac", "-b:a", "32k", "-ar", "8000",
        "-threads", "0",  # explicit multi-core; H.263 has no x264-style -preset knob
    ])
    return dst


async def _transcode_compact_archive(ffmpeg_path: str, src: Path) -> Path:
    """Preset 7: re-encode to HEVC/Opus for a minimal-size MKV archive copy.

    Deliberately not `-preset ultrafast`: this preset's entire purpose is a
    small archive copy, and ultrafast trades meaningfully larger output for
    speed at the same CRF — directly working against that goal. `faster`
    is a genuine speed win over the previous `medium` without gutting it.
    """
    dst = src.with_suffix(".hevc.mkv")
    await _ffmpeg_transcode(ffmpeg_path, src, dst, [
        "-c:v", "libx265", "-crf", "28", "-preset", "faster", "-threads", "0",
        "-c:a", "libopus", "-b:a", "128k",
    ])
    return dst


class YtdlpEngine(Engine):
    """Downloads and post-processes media via the `yt_dlp` library."""

    name = "ytdlp"

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        # yt-dlp's own ffmpeg-exists check does a literal path check rather
        # than searching PATH, so a bare command name (the config default)
        # would otherwise trip a spurious "ffmpeg-location does not exist"
        # warning even when ffmpeg is perfectly reachable.
        import shutil

        resolved = shutil.which(ffmpeg_path)
        self._ffmpeg_path = resolved or ffmpeg_path
        self._cancel_flags: Dict[str, bool] = {}
        self._active_ffmpegs: Dict[str, asyncio.subprocess.Process] = {}

    async def probe(self, url: str) -> Dict[str, Any]:
        """Fetch metadata (title/duration/thumbnail/formats/playlist entries), no download."""
        import yt_dlp

        loop = asyncio.get_running_loop()

        def _extract() -> Dict[str, Any]:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                return ydl.extract_info(url, download=False)

        return await loop.run_in_executor(None, _extract)

    async def process(
        self,
        input_value: str,
        config: Any,
        progress_callback: ProgressCallback,
        log_callback: Optional[LogCallback] = None,
    ) -> DownloadResult:
        import yt_dlp

        choice: YtdlpChoice = getattr(config, "ytdlp_choice", None) or YtdlpChoice()
        out_dir = Path(getattr(config, "download_dir", str(Path.home())))
        out_template = str(out_dir / getattr(config, "ytdlp_output_template", "%(title)s [%(id)s].%(ext)s"))
        task_id = f"ytdlp-{abs(hash((input_value, choice.preset))) % 10**8}"
        self._cancel_flags[task_id] = False

        loop = asyncio.get_running_loop()
        started = time.monotonic()
        downloaded_paths: List[str] = []
        last_name = {"value": input_value}

        def _hook(d: Dict[str, Any]) -> None:
            if self._cancel_flags.get(task_id):
                raise yt_dlp.utils.DownloadError("Cancelled by user")
            status_map = {
                "downloading": TaskStatus.ACTIVE,
                "finished": TaskStatus.COMPLETE,
                "error": TaskStatus.ERROR,
            }
            info = d.get("info_dict") or {}
            name = info.get("title") or Path(d.get("filename", input_value)).name
            last_name["value"] = name
            update = ProgressUpdate(
                task_id=task_id,
                name=name,
                status=status_map.get(d.get("status"), TaskStatus.ACTIVE),
                downloaded_bytes=d.get("downloaded_bytes") or 0,
                total_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
                speed_bytes_per_sec=d.get("speed") or 0.0,
                eta_seconds=d.get("eta"),
                message=d.get("status", ""),
                extra={"fragment_index": d.get("fragment_index"), "fragment_count": d.get("fragment_count")},
            )
            if log_callback is not None and d.get("status") == "downloading" and d.get("_percent_str"):
                asyncio.run_coroutine_threadsafe(log_callback(f"{name}: {d['_percent_str'].strip()}"), loop)
            asyncio.run_coroutine_threadsafe(progress_callback(update), loop)
            if d.get("status") == "finished" and d.get("filename"):
                downloaded_paths.append(d["filename"])

        if choice.manual_format:
            opts: Dict[str, Any] = {
                "outtmpl": out_template, "format": choice.manual_format, "ffmpeg_location": self._ffmpeg_path,
            }
        else:
            opts = _preset_opts(choice.preset, out_template, self._ffmpeg_path)

        opts["progress_hooks"] = [_hook]
        opts["quiet"] = True
        opts["no_warnings"] = True
        opts["noplaylist"] = choice.playlist_items is None
        if choice.playlist_items:
            opts["playlist_items"] = choice.playlist_items
        if choice.preset == 8 and choice.clip_start is not None and choice.clip_end is not None:
            from yt_dlp.utils import download_range_func

            opts["download_ranges"] = download_range_func(None, [(choice.clip_start, choice.clip_end)])
            opts["force_keyframes_at_cuts"] = True

        error: Optional[str] = None
        status = TaskStatus.COMPLETE

        def _download() -> None:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([input_value])

        try:
            await loop.run_in_executor(None, _download)
        except Exception as exc:
            status = TaskStatus.CANCELLED if self._cancel_flags.get(task_id) else TaskStatus.ERROR
            error = str(exc)

        # Explicit post-passes yt-dlp has no native postprocessor for.
        if status == TaskStatus.COMPLETE and downloaded_paths:
            try:
                if choice.preset == 4:
                    new_path = await _transcode_feature_phone(self._ffmpeg_path, Path(downloaded_paths[-1]))
                    downloaded_paths[-1] = str(new_path)
                elif choice.preset == 7:
                    new_path = await _transcode_compact_archive(self._ffmpeg_path, Path(downloaded_paths[-1]))
                    downloaded_paths[-1] = str(new_path)
            except Exception as exc:
                status = TaskStatus.ERROR
                error = f"Downloaded but post-processing failed: {exc}"

        self._cancel_flags.pop(task_id, None)
        elapsed = time.monotonic() - started
        total = sum(Path(p).stat().st_size for p in downloaded_paths if Path(p).exists())
        await progress_callback(ProgressUpdate(
            task_id=task_id, name=last_name["value"], status=status,
            downloaded_bytes=total, total_bytes=total or None,
        ))
        return DownloadResult(
            task_id=task_id, name=last_name["value"], status=status,
            output_paths=downloaded_paths, total_bytes=total, elapsed_seconds=elapsed,
            average_speed=(total / elapsed) if elapsed > 0 else 0.0, error=error,
        )

    async def pause(self, task_id: str) -> None:
        # yt-dlp has no native mid-fragment pause; treated as cancel, and the
        # caller (queue manager) is expected to re-queue the input to resume.
        await self.cancel(task_id)

    async def resume(self, task_id: str) -> None:
        return None  # handled by re-queueing at the router/queue level

    async def cancel(self, task_id: str) -> None:
        self._cancel_flags[task_id] = True
        if task_id in self._active_ffmpegs:
            try:
                self._active_ffmpegs[task_id].terminate()
            except ProcessLookupError:
                pass
