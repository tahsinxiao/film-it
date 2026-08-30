#!/usr/bin/env python3
"""Enhance a finished Film It master to a higher-resolution, higher-frame-rate MP4.

The motion mode uses FFmpeg's motion-compensated minterpolate filter. The fast
mode uses Lanczos scaling and CFR conversion without inventing intermediate
motion. Both preserve the original audio stream through AAC re-encoding.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)


def enhance(src: Path, dst: Path, width: int, height: int, fps: int, mode: str) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={width}:{height}:flags=lanczos"
    if mode == "ffmpeg_motion":
        vf = f"{scale},minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,format=yuv420p"
        method = "Lanczos super-resolution resize + motion-compensated interpolation"
    else:
        vf = f"{scale},fps={fps},format=yuv420p"
        method = "Lanczos resize + constant-frame-rate conversion"
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst),
    ]
    try:
        run(cmd, timeout=300 if mode == "ffmpeg_motion" else None)
        return method
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if mode == "ffmpeg_motion":
            print("Motion interpolation failed or exceeded five minutes; retrying with the reliable fast 2K60 mode.")
            fallback = f"{scale},fps={fps},format=yuv420p"
            run(["ffmpeg", "-y", "-i", str(src), "-vf", fallback, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)])
            return "Lanczos resize + constant-frame-rate fallback after interpolation failure or timeout"
        raise RuntimeError(exc.stderr[-2000:]) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--mode", choices=["ffmpeg_motion", "ffmpeg_fast"], default="ffmpeg_motion")
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    src, dst = Path(args.input), Path(args.output)
    if not src.exists() or src.stat().st_size == 0:
        raise SystemExit(f"Input video is missing or empty: {src}")
    method = enhance(src, dst, args.width, args.height, args.fps, args.mode)
    metadata = {"input": str(src), "output": str(dst), "width": args.width, "height": args.height, "fps": args.fps, "mode": args.mode, "method": method}
    Path(args.metadata).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
