#!/usr/bin/env python3
"""Create the single beginner-friendly Film It download ZIP."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def make_metadata(output: Path) -> str:
    publishing = read_json(output / "youtube_metadata.json")
    run = read_json(output / "run_metadata.json")
    enhance = read_json(output / "enhancement_metadata.json")
    sources = read_json(output / "work" / "sources.json")
    lines = [
        "FILM IT — VIDEO PUBLISHING METADATA",
        "=" * 42,
        "",
        "TITLE",
        publishing.get("title", run.get("title", "")),
        "",
        "DESCRIPTION",
        publishing.get("description", ""),
        "",
        "HASHTAGS",
        " ".join(publishing.get("hashtags", [])),
        "",
        "KEYWORDS",
        ", ".join(publishing.get("keywords", [])),
        "",
        "VIDEO DETAILS",
        f"Duration (seconds): {run.get('duration_seconds', '')}",
        f"Base resolution: {run.get('resolution', '')}",
        f"Aspect ratio: {run.get('aspect_ratio', '16:9')}",
        f"Visual style: {run.get('style', '')}",
        f"Voice: {run.get('voice', '')}",
        f"Reference sources: {len(sources) if isinstance(sources, list) else 0}",
        "",
        "ENHANCEMENT",
        f"Enhanced resolution: {enhance.get('width', '')}x{enhance.get('height', '')}",
        f"Enhanced frame rate: {enhance.get('fps', '')} fps",
        f"Enhancement method: {enhance.get('method', '')}",
        "",
        "REFERENCE SOURCES",
    ]
    if isinstance(sources, list) and sources:
        for i, source in enumerate(sources, 1):
            lines.append(f"{i}. {source.get('title', '') or 'Untitled source'}")
            lines.append(f"   URL: {source.get('url', '')}")
            if source.get("channel"):
                lines.append(f"   Channel: {source['channel']}")
    else:
        lines.append("No source URL was supplied.")
    lines += [
        "",
        "NOTES",
        "This package contains original Film It output. Reference links are listed for research context; the original source footage is not included.",
    ]
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    archive = Path(args.output)
    archive.parent.mkdir(parents=True, exist_ok=True)
    metadata = source / "metadata.txt"
    metadata.write_text(make_metadata(source), encoding="utf-8")
    selected = [source / "final.mp4", source / "final_2k60.mp4", metadata]
    subtitles = source / "narration.srt"
    if subtitles.exists() and subtitles.stat().st_size:
        selected.append(subtitles)
    missing = [str(p) for p in selected if not p.exists() or p.stat().st_size == 0]
    if missing:
        raise SystemExit("Missing required package files: " + ", ".join(missing))
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in selected:
            zf.write(path, arcname=path.name)
    print(f"Created {archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
