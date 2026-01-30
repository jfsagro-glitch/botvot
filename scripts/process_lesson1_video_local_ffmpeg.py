"""
Process lesson 1 video for Telegram using a local ffmpeg.exe.

Why this script exists:
- On Windows, ffmpeg may not be in PATH.
- PowerShell quoting breaks complex filter strings easily.
- We run ffmpeg via subprocess args list (no shell quoting issues).

Input:
  VIDEO/1/document_5461089794907998389.mp4
Output:
  VIDEO/1/lesson1_telegram_optimized.mp4

Behavior:
- If the source is vertical, we produce a 1280x720 horizontal video using a blurred background + centered foreground.
- If the source is horizontal, we scale to fit 1280x720 (keeping aspect ratio) and pad if needed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_VIDEO = PROJECT_ROOT / "VIDEO" / "1" / "document_5461089794907998389.mp4"
OUTPUT_VIDEO = PROJECT_ROOT / "VIDEO" / "1" / "lesson1_telegram_optimized.mp4"

# User-provided local ffmpeg folder (contains ffmpeg.exe + ffprobe.exe)
LOCAL_FFMPEG_DIR = PROJECT_ROOT / "ffmpeg-8.0.1 (1).tar" / "ffmpeg-8.0.1"
FFMPEG_EXE = LOCAL_FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_EXE = LOCAL_FFMPEG_DIR / "ffprobe.exe"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def _find_exe(preferred: Path, fallback_name: str) -> str:
    if preferred.exists():
        return str(preferred)
    # fallback to PATH
    return fallback_name


def get_video_info(ffprobe: str, video_path: Path) -> tuple[int, int, float, int] | None:
    """
    Returns (width, height, duration_sec, bitrate_bps) or None.
    """
    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration,bit_rate",
            "-of",
            "json",
            str(video_path),
        ]
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        w = int(stream.get("width") or 0)
        h = int(stream.get("height") or 0)
        dur = float(fmt.get("duration") or 0.0)
        br = int(fmt.get("bit_rate") or 0)
        if w > 0 and h > 0:
            return (w, h, dur, br)
        return None
    except Exception:
        return None


def main() -> int:
    ffmpeg = _find_exe(FFMPEG_EXE, "ffmpeg")
    ffprobe = _find_exe(FFPROBE_EXE, "ffprobe")

    if not INPUT_VIDEO.exists():
        print(f"ERROR: input video not found: {INPUT_VIDEO}")
        return 2

    OUTPUT_VIDEO.parent.mkdir(parents=True, exist_ok=True)

    info = get_video_info(ffprobe, INPUT_VIDEO)
    if not info:
        print("WARN: could not read video info, proceeding with generic filter")
        src_w = 0
        src_h = 0
    else:
        src_w, src_h, dur, br = info
        print(f"Source: {src_w}x{src_h}, duration={dur:.1f}s, bitrate={br/1000:.0f}kbps")

    is_vertical = bool(src_w and src_h and src_h > src_w)

    # Telegram-friendly output: H.264 + AAC, yuv420p, faststart.
    # Target a clean horizontal 1280x720 (16:9) for consistent previews.
    if is_vertical:
        # Create a blurred background cropped to 1280x720, then overlay the scaled foreground.
        vf = (
            "[0:v]"
            "scale=1280:720:force_original_aspect_ratio=increase,"
            "crop=1280:720,"
            "gblur=sigma=30"
            "[bg];"
            "[0:v]"
            "scale=1280:720:force_original_aspect_ratio=decrease"
            "[fg];"
            "[bg][fg]"
            "overlay=(W-w)/2:(H-h)/2,"
            "format=yuv420p"
            "[v]"
        )
        map_v = ["-map", "[v]"]
        filters = ["-filter_complex", vf]
    else:
        # Scale to fit, then pad to 1280x720 (keeps aspect ratio).
        vf = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        map_v = []
        filters = ["-vf", vf]

    args = [
        ffmpeg,
        "-y",
        "-i",
        str(INPUT_VIDEO),
        *filters,
        *map_v,
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-maxrate",
        "2500k",
        "-bufsize",
        "5000k",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(OUTPUT_VIDEO),
    ]

    print("Running ffmpeg…")
    proc = subprocess.run(args)
    if proc.returncode != 0:
        print(f"ERROR: ffmpeg failed with exit code {proc.returncode}")
        return proc.returncode

    if not OUTPUT_VIDEO.exists():
        print("ERROR: output file not created")
        return 3

    out_info = get_video_info(ffprobe, OUTPUT_VIDEO)
    if out_info:
        ow, oh, odur, obr = out_info
        print(f"Output: {ow}x{oh}, duration={odur:.1f}s, bitrate={obr/1000:.0f}kbps")
    size_mb = OUTPUT_VIDEO.stat().st_size / (1024 * 1024)
    print(f"Output size: {size_mb:.2f} MB -> {OUTPUT_VIDEO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

