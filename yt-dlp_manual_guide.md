<div align="center">
  <h1>🎛️ PyFlow Omni: The Ultimate Manual Mode Guide</h1>
  <p><b>Unlock the full power of <code>yt-dlp</code> parameters to customize your downloads exactly the way you want!</b></p>
  
  [![yt-dlp](https://img.shields.io/badge/Powered_by-yt--dlp-FF0000?style=for-the-badge&logo=youtube)](https://github.com/yt-dlp/yt-dlp)
</div>

---

## 🌟 What is Manual Mode & Why Use It?

While PyFlow Omni comes pre-equipped with 8 smart presets, **Manual Mode** grants you total freedom. It allows you to fine-tune your downloads by specifying custom resolutions, embedding subtitles, selecting specific audio codecs, or tailoring metadata parameters to match your exact requirements.

This guide will walk you through building precise parameter strings so you always get the exact output file you desire.

---

## 🛠️ The Basics: Parameter Structure

Any custom download command typically consists of two main components: **Format Selection** (`-f`) and **Extra Options** (`--write-subs`, `--embed-metadata`, etc.).

### 1. Format Selection Cheat Sheet (`-f` or `--format`)
Use the following values to define your preferred video and audio quality:

| Parameter / Value | Output Result | Recommended Use Case |
|:---|:---|:---|
| `-f best` | Highest quality pre-merged single file (Video + Audio). | Fast downloads, though it might cap at 720p or 1080p depending on the platform. |
| `-f "bestvideo+bestaudio"` | Downloads the absolute highest resolution video and audio separately, then merges them (requires FFmpeg). | When you want pristine 4K, 8K, or maximum available quality. |
| `-f "bestvideo[height<=1080]+bestaudio"` | Best available video up to 1080p paired with best audio. | Ideal for saving disk space while maintaining crisp Full HD clarity. |
| `-f bestaudio` | Extracts the best available audio stream only (no video). | Perfect for downloading podcasts, audiobooks, or music. |
| `-f "best[ext=mp4]"` | Best single-file quality specifically in MP4 format. | Best for maximum hardware compatibility across older devices. |

---

## 🎯 Professional Use-Cases & Ready-to-Use Commands

Here are several advanced, production-ready command strings. You can directly copy and paste these into the **Manual Mode** input field in PyFlow Omni.

<details>
<summary><b>🎬 1. Ultimate 4K/1080p Quality (Click to expand)</b></summary>
<br>

Fetch the top-tier video and audio stream available, merging them cleanly into an MP4 container:

```bash
-f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" --merge-output-format mp4

```

Older feature phones have limited processing power and often only support low-resolution MP4 or legacy 3GP formats. This string ensures the video is compatible with basic mobile devices:

```bash
-f "best[ext=mp4][height<=240]/best[ext=3gp]" --recode-video 3gp

```

Extract audio tracks in premium listening formats:

**For 320kbps MP3:**

```bash
-f bestaudio --extract-audio --audio-format mp3 --audio-quality 0

```

**For Lossless FLAC:**

```bash
-f bestaudio --extract-audio --audio-format flac

```

Download a specific range from a playlist along with embedded English subtitles:

```bash
--playlist-start 1 --playlist-end 5 --write-subs --sub-langs "en.*" --embed-subs

```

*(This extracts items 1 through 5 from a playlist and embeds English subtitles directly into the video container.)*

---

## 🎨 Essential Modifiers & Extra Options

Enhance your download workflows by appending these utility flags to your custom parameter string:

* 🏷️ `--add-metadata` : Embeds artist names, album details, and release dates directly into the media file.
* 🖼️ `--embed-thumbnail` : Sets the video thumbnail or cover artwork as the file icon (excellent for music tracks).
* ⏱️ `--download-sections "*00:01:00-00:02:30"` : Clips and downloads only the specified timeframe instead of the full video.
* 📁 `--restrict-filenames` : Sanitizes file names by removing spaces, special characters, and emojis for clean cross-platform handling.

---

## 🚑 Troubleshooting Common Errors

If you run into issues while testing custom parameters, check these quick fixes:

> **🔴 Problem:** The video downloads successfully, but there is no sound!
> **✅ Cause & Solution:** You likely used `-f bestvideo` without specifying an audio track. Always request both streams using `-f "bestvideo+bestaudio"` and ensure `FFmpeg` is installed on your system to handle the merging process.

> **🔴 Problem:** Error stating `Requested format is not available`.
> **✅ Cause & Solution:** You requested a resolution or format that the platform doesn't offer for that specific video (e.g., requesting `height>=1080` on a 720p source). Use fallback formats separated by a slash `/`: `-f "bestvideo[height=1080]+bestaudio/best"`.

> **🔴 Problem:** Downloaded file sizes are unexpectedly massive.
> **✅ Cause & Solution:** `-f bestvideo` defaults to the absolute highest bitrate available (e.g., uncompressed 4K/8K WebM). Restrict the height and container format: `-f "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"`.

---
