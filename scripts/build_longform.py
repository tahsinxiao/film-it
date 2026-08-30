#!/usr/bin/env python3
"""Build a graphics-first long-form science video from project.yml.

The script is deliberately dependency-light and fails over to deterministic output:
OpenRouter -> local outline, Gemini TTS -> Edge TTS -> silent audio, AI images -> procedural cards.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import textwrap
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
import yaml
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
DEFAULT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
VOICE_MAP = {
    "female_documentary": "en-US-Neural2-F",
    "male_documentary": "en-US-Neural2-D",
    "female_warm": "en-US-Neural2-C",
    "male_warm": "en-US-Neural2-J",
}
EDGE_VOICE_MAP = {
    "female_documentary": "en-US-AriaNeural",
    "male_documentary": "en-US-GuyNeural",
    "female_warm": "en-US-JennyNeural",
    "male_warm": "en-US-ChristopherNeural",
}
STYLE_PALETTES = {
    "auto": ((18, 25, 48), (38, 111, 180), (240, 180, 72)),
    "classic": ((12, 25, 43), (25, 91, 130), (239, 190, 88)),
    "whiteboard": ((247, 245, 238), (215, 225, 230), (25, 45, 55)),
    "anime": ((28, 20, 65), (130, 60, 175), (255, 205, 100)),
    "kawaii": ((255, 236, 244), (175, 222, 239), (126, 85, 160)),
    "watercolor": ((30, 62, 75), (82, 140, 130), (235, 190, 128)),
    "retroprint": ((48, 34, 30), (168, 89, 54), (232, 190, 109)),
}


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def load_project(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("project", {})
    data.setdefault("sources", [])
    data.setdefault("narration", {})
    data.setdefault("visuals", {})
    data.setdefault("sound", {})
    data.setdefault("subtitles", {})
    data.setdefault("output", {})
    return data


def apply_workflow_inputs(project: dict[str, Any]) -> dict[str, Any]:
    """Apply optional GitHub Actions form values without requiring YAML edits."""
    p = project["project"]
    text = os.getenv("FILM_SCRIPT_OR_TOPIC", "").strip()
    if text:
        p["custom_script"] = text
        p["topic"] = text if len(text) <= 500 else p.get("topic", p.get("title", "Science topic"))
    if os.getenv("FILM_TITLE", "").strip():
        p["title"] = os.environ["FILM_TITLE"].strip()
    if os.getenv("FILM_DURATION", "").strip():
        p["target_duration_minutes"] = float(os.environ["FILM_DURATION"])
    if os.getenv("FILM_STYLE", "").strip():
        p["visual_style"] = os.environ["FILM_STYLE"].strip().lower()
    if os.getenv("FILM_VOICE", "").strip():
        project["narration"]["voice"] = os.environ["FILM_VOICE"].strip()
    if os.getenv("FILM_SUBTITLES", "").strip():
        project["subtitles"]["enabled"] = os.environ["FILM_SUBTITLES"].lower() == "true"
    source_url = os.getenv("FILM_SOURCE_URL", "").strip()
    if source_url:
        project["sources"] = [{"url": source_url, "role": "reference"}]
    return project


def fetch_sources(project: dict[str, Any], work: Path) -> list[dict[str, Any]]:
    records = []
    for source in project.get("sources", []):
        url = source.get("url", "") if isinstance(source, dict) else str(source)
        if not url or "REPLACE_WITH" in url:
            continue
        record = {"url": url, "title": "", "description": "", "transcript": ""}
        try:
            meta = run(["yt-dlp", "--dump-single-json", "--skip-download", url]).stdout
            parsed = json.loads(meta)
            record["title"] = parsed.get("title", "")
            record["description"] = parsed.get("description", "")[:5000]
            record["channel"] = parsed.get("channel", parsed.get("uploader", ""))
            # yt-dlp's subtitle download is intentionally best-effort.
            subdir = work / "transcripts"
            subdir.mkdir(parents=True, exist_ok=True)
            run(["yt-dlp", "--skip-download", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt", "-o", str(subdir / "source.%(ext)s"), url], check=False)
            vtts = list(subdir.glob("*.vtt"))
            if vtts:
                text = vtts[0].read_text(encoding="utf-8", errors="ignore")
                text = re.sub(r"WEBVTT.*?\n", "", text, flags=re.S)
                text = re.sub(r"\d{2}:\d{2}[:\.]\d{2,3} --> .*\n", "", text)
                text = re.sub(r"<[^>]+>", "", text)
                record["transcript"] = re.sub(r"\n{2,}", "\n", text).strip()[:30000]
        except Exception as exc:
            record["error"] = f"source fetch failed: {exc}"
        records.append(record)
    (work / "sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records


def call_openrouter(project: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        return None
    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1:free")
    topic = project["project"].get("topic", project["project"].get("title", "Science topic"))
    duration = project["project"].get("target_duration_minutes", 8)
    style = project["project"].get("visual_style", "classic")
    source_text = "\n\n".join(f"SOURCE {i+1}: {s.get('title','')}\n{s.get('transcript','')[:6000]}" for i, s in enumerate(sources))
    custom_script = project["project"].get("custom_script", "")
    custom_note = f"CUSTOM SCRIPT OR STORY NOTES:\n{custom_script[:30000]}\nPreserve the user’s factual intent while improving structure and visual pacing." if custom_script else "No custom script was supplied; write the narration from the topic and references."
    prompt = f"""Create a fact-conscious English science explainer video plan.
Topic: {topic}
Target duration: {duration} minutes
Visual style: {style}
Use source material only as reference. Do not copy sentences or source footage. Clearly mark uncertain claims.
Return JSON only with this shape:
{{"title":"strong clickable but accurate YouTube title","hook":"...","description":"ready-to-publish YouTube description with a short hook, clear explanation, source note, and subscribe call-to-action","hashtags":["#Science","#Explained"],"keywords":["science","education"],"narration":"full narration around {duration*150} words","scenes":[{{"id":"S1","visual":"...","on_screen_text":"...","sfx":"none|whoosh|impact|spark","duration":8,"beats":[{{"label":"...","visual_change":"..."}},{{"label":"...","visual_change":"..."}},{{"label":"...","visual_change":"..."}}]}}],"claims":[{{"claim":"...","source":"..."}}]}}
Use a retention grammar: 0-5s high-curiosity hook, 5-15s paradigm flip, then mechanism, evidence, scale, surprise, and payoff. Every scene must contain 2-4 visual beats, each changing within about 3 seconds through a zoom, label, color shift, diagram step, new icon, camera move, or composition change. Make visuals graphics-first: diagrams, maps, timelines, charts, particles, symbols, textures, and abstract scientific imagery. Avoid humans and faces.
Reference notes:
{source_text[:24000]}

{custom_note}"""
    body = {"model": model, "messages": [{"role": "system", "content": "You are an expert science script editor and visual director."}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": "https://github.com/F-R-L/forge-film", "X-Title": "Forge Film"}, json=body, timeout=180)
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:
        print(f"OpenRouter unavailable; using deterministic fallback: {exc}")
        return None


def fallback_plan(project: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    p = project["project"]
    topic = str(p.get("topic", "science explained")).strip()
    custom = str(p.get("custom_script", "")).strip()
    if (not topic or topic.lower() in {"science topic", "science explained"}) and custom:
        topic = " ".join(custom.split()[:10]).rstrip(".,!?;:")
    title = str(p.get("title", "")).strip()
    if not title or title.lower() in {"science explained", "science explainer"}:
        title = f"The Hidden Science Behind {topic}".strip()
    n = int(project.get("visuals", {}).get("scene_count", 24))
    chunks = ["The question", "What we observe", "The hidden mechanism", "The evidence", "The surprising consequence", "What remains unknown"]
    narration = custom if custom else (f"Today we are exploring {topic}. " + " ".join([f"This chapter examines {c.lower()} and connects it to the larger scientific picture." for c in chunks]))
    per_scene = float(p.get("target_duration_minutes", 8)) * 60 / max(1, n)
    scenes = []
    for i in range(n):
        label = chunks[i % len(chunks)]
        scenes.append({"id": f"S{i+1}", "visual": f"Abstract scientific diagram about {topic}; chapter: {label}", "on_screen_text": label, "sfx": "impact" if i == 0 else ("whoosh" if i % 5 == 0 else "none"), "duration": per_scene, "beats": [{"label": label, "visual_change": "establish the central concept"}, {"label": "Mechanism", "visual_change": "zoom in and add directional arrows"}, {"label": "Why it matters", "visual_change": "expand into a connected system"}]})
    claims = [{"claim": f"Topic supplied by project.yml: {topic}", "source": "project.yml"}]
    return {"title": title, "hook": f"What if the familiar story about {topic} is incomplete?", "narration": narration, "scenes": scenes, "claims": claims}


def make_publishing_metadata(plan: dict[str, Any], project: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    p = project["project"]
    topic = str(p.get("topic", "science explained")).strip()
    custom = str(p.get("custom_script", "")).strip()
    if (not topic or topic.lower() in {"science topic", "science explained"}) and custom:
        topic = " ".join(custom.split()[:10]).rstrip(".,!?;:")
    title = str(plan.get("title", "")).strip() or f"The Surprising Science of {topic}"
    clean_words = re.findall(r"[A-Za-z0-9]+", topic.lower())
    keyword_defaults = ["science", "science explained", "educational video", "documentary", *clean_words[:8]]
    keywords = list(dict.fromkeys(str(x).strip() for x in (plan.get("keywords") or keyword_defaults) if str(x).strip()))[:20]
    hashtags = [str(x).strip() for x in (plan.get("hashtags") or []) if str(x).strip().startswith("#")]
    if not hashtags:
        hashtags = ["#Science", "#ScienceExplained", "#Education", "#Documentary"]
    description = str(plan.get("description", "")).strip()
    if not description:
        source_note = " This video was created using original graphics and narration, with linked sources used as reference." if sources else " This video uses original graphics and narration."
        hashtag_text = " ".join(hashtags)
        description = f"What is really happening with {topic}? In this episode, we break down the mechanism, evidence, and surprising consequences in a clear visual explanation.{source_note}\n\nSubscribe for more science explainers and curious discoveries.\n\n{hashtag_text}"
    return {"title": title, "description": description, "hashtags": hashtags, "keywords": keywords, "hook": plan.get("hook", ""), "source_count": len(sources)}


def write_publishing_package(metadata: dict[str, Any], outdir: Path) -> None:
    (outdir / "youtube_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    (outdir / "youtube_description.txt").write_text(metadata["description"] + "\n", encoding="utf-8")
    (outdir / "hashtags.txt").write_text(" ".join(metadata["hashtags"]) + "\n", encoding="utf-8")
    (outdir / "keywords.txt").write_text(", ".join(metadata["keywords"]) + "\n", encoding="utf-8")
    package = f"# YouTube publishing package\n\n## Title\n{metadata['title']}\n\n## Description\n{metadata['description']}\n\n## Hashtags\n{' '.join(metadata['hashtags'])}\n\n## Keywords\n{', '.join(metadata['keywords'])}\n"
    (outdir / "youtube_publishing_package.md").write_text(package, encoding="utf-8")


def write_script(plan: dict[str, Any], work: Path) -> Path:
    (work / "script.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    (work / "narration.txt").write_text(plan.get("narration", ""), encoding="utf-8")
    return work / "narration.txt"


def pcm_to_wav(raw: bytes, path: Path, rate: int = 24000) -> None:
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate); wf.writeframes(raw)


def gemini_tts(text: str, cfg: dict[str, Any], out: Path) -> bool:
    key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    if not key:
        return False
    model = cfg.get("model", "gemini-2.5-flash-tts")
    voice = "Kore" if cfg.get("voice", "").startswith("female") else "Puck"
    prompt = f"{cfg.get('expressive_prompt','Read clearly and naturally.') }\n\n{text}"
    payload = {"model": model, "input": prompt, "response_format": {"type": "audio"}, "generation_config": {"speech_config": [{"voice": voice}]}}
    try:
        r = requests.post("https://generativelanguage.googleapis.com/v1beta/interactions", headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=payload, timeout=240)
        r.raise_for_status()
        data = r.json().get("output_audio", {}).get("data")
        if data:
            pcm_to_wav(base64.b64decode(data), out)
            return True
    except Exception as exc:
        print(f"Gemini TTS unavailable: {exc}")
    return False


def edge_tts(text: str, cfg: dict[str, Any], out: Path) -> bool:
    if not shutil.which("edge-tts"):
        return False
    voice = EDGE_VOICE_MAP.get(cfg.get("voice", "female_documentary"), EDGE_VOICE_MAP["female_documentary"])
    try:
        # Edge markup uses SSML-like expressive text only through its CLI options;
        # the expressive prompt is applied as narration direction during scripting.
        run(["edge-tts", "--voice", voice, "--text", text, "--write-media", str(out)], check=True)
        return out.exists() and out.stat().st_size > 0
    except Exception as exc:
        print(f"Edge TTS unavailable: {exc}")
        return False


def make_silent_audio(out: Path, seconds: float) -> None:
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(max(seconds, 1)), "-c:a", "aac", str(out)])


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def fetch_scene_image(scene: dict[str, Any], cfg: dict[str, Any], out: Path) -> bool:
    """Best-effort free hosted image generation; never blocks the render indefinitely."""
    if not cfg.get("use_ai_images", True) or os.getenv("FILM_DISABLE_REMOTE_IMAGES", "").lower() == "true":
        return False
    visual = str(scene.get("visual", ""))
    label = str(scene.get("on_screen_text", ""))
    style = str(cfg.get("visual_style", "classic"))
    prompt = (f"editorial science illustration, {style} animation style, {visual}, "
              f"symbolic objects only, no people, no faces, no readable text, clean composition")
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt) + "?width=1024&height=576&nologo=true"
    try:
        r = requests.get(url, timeout=35)
        r.raise_for_status()
        if len(r.content) < 5000:
            return False
        out.write_bytes(r.content)
        return True
    except Exception as exc:
        print(f"Remote scene image unavailable for {label[:40]}: {exc}")
        return False


def make_card(scene: dict[str, Any], idx: int, total: int, cfg: dict[str, Any], out: Path) -> None:
    """Render a distinct storyboard frame with optional AI art plus animated overlays."""
    style = str(cfg.get("visual_style", "classic"))
    a, b, accent = STYLE_PALETTES.get(style, STYLE_PALETTES["classic"])
    w, h = int(cfg.get("width", 1920)), int(cfg.get("height", 1080))
    dark_text = style in ("whiteboard", "kawaii")
    ink = (24, 34, 45) if dark_text else (245, 247, 244)
    asset = scene.get("_asset")
    has_asset = False
    if asset and Path(str(asset)).exists():
        try:
            source = Image.open(asset).convert("RGB")
            source.thumbnail((w, h), Image.Resampling.LANCZOS)
            im = Image.new("RGB", (w, h), a)
            x = (w - source.width) // 2; y = (h - source.height) // 2
            im.paste(source, (x, y))
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle((0, 0, w, h), fill=(*a, 110 if style not in ("whiteboard", "kawaii") else 35))
            im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
            d = ImageDraw.Draw(im)
            has_asset = True
        except Exception:
            im = Image.new("RGB", (w, h), a); d = ImageDraw.Draw(im)
    else:
        im = Image.new("RGB", (w, h), a); d = ImageDraw.Draw(im)
    if not has_asset:
        for y in range(h):
            t = y / h
            c = tuple(int(a[i] * (1-t) + b[i] * t) for i in range(3))
            d.line((0, y, w, y), fill=c)
    if style == "whiteboard":
        for x in range(0, w, 96): d.line((x, 0, x, h), fill=(25,45,55,18), width=1)
        for y in range(0, h, 96): d.line((0, y, w, y), fill=(25,45,55,18), width=1)
    elif style == "retroprint":
        for x in range(0, w, 12): d.line((x, 0, x, h), fill=tuple(max(0, q-10) for q in a), width=1)
    elif style == "watercolor":
        for k in range(16):
            x = int((math.sin(k * 2.4 + idx) * .42 + .5) * w)
            d.ellipse((x-150, -80+k*80, x+260, 220+k*80), fill=tuple(min(255, q+18) for q in b), outline=None)
    title = str(scene.get("on_screen_text", scene.get("visual", "")))[:100]
    visual = str(scene.get("visual", "")).lower()
    subject = f"{title} {visual}".lower()
    layout = idx % 8
    left = int(w * .08); right = int(w * .92); top = int(h * .14); bottom = int(h * .84)

    # Main visual zone: each layout demonstrates a different visual language.
    if any(k in subject for k in ("black hole", "event horizon", "singularity", "gravity")) and layout in (0, 6):
        cx, cy = int(w*.70), int(h*.52); radius = int(h*.19)
        for k in range(6, 0, -1):
            rr = radius + k*30
            d.ellipse((cx-rr, cy-rr*.42, cx+rr, cy+rr*.42), outline=accent, width=max(2, 8-k))
        d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=(5, 7, 15), outline=accent, width=8)
        for k in range(7):
            sx = int(w*.48 + (k%3)*110); sy = int(h*.25 + (k*97)%420)
            d.ellipse((sx-7, sy-7, sx+7, sy+7), fill=accent)
            d.line((sx, sy, cx-int(radius*.7), cy-int(radius*.5)), fill=accent, width=3)
        d.arc((w*.45, h*.20, w*.86, h*.83), 205, 350, fill=ink, width=6)
    elif any(k in subject for k in ("clock", "time", "dilation", "before", "after")) or layout == 1:
        for cx, label in ((int(w*.62), "A"), (int(w*.82), "B")):
            cy = int(h*.48); r = int(h*.16)
            d.ellipse((cx-r, cy-r, cx+r, cy+r), outline=accent, width=7)
            d.line((cx, cy, cx-int(r*.45), cy-int(r*.35)), fill=ink, width=6)
            d.line((cx, cy, cx+int(r*.25), cy-int(r*.62)), fill=accent, width=5)
            d.text((cx-16, cy+r+20), label, fill=accent, font=font(DEFAULT_BOLD, 34))
        d.line((w*.62, h*.78, w*.82, h*.78), fill=accent, width=5)
        d.polygon([(w*.82,h*.78),(w*.78,h*.75),(w*.78,h*.81)], fill=accent)
    elif any(k in subject for k in ("mechanism", "evidence", "system", "process", "cause", "effect")) or layout == 2:
        boxes = [(w*.56,h*.28,w*.70,h*.43),(w*.77,h*.28,w*.91,h*.43),(w*.66,h*.60,w*.81,h*.75)]
        for j, box in enumerate(boxes):
            d.rounded_rectangle(box, radius=18, outline=accent, width=6, fill=tuple(int((a[q]+b[q])/2) for q in range(3)))
            d.text((box[0]+20, box[1]+35), ["CAUSE","MECHANISM","EFFECT"][j], fill=ink, font=font(DEFAULT_BOLD, 27))
        d.line((w*.70,h*.355,w*.77,h*.355), fill=accent, width=6); d.polygon([(w*.77,h*.355),(w*.74,h*.335),(w*.74,h*.375)], fill=accent)
        d.line((w*.84,h*.43,w*.74,h*.60), fill=accent, width=6); d.polygon([(w*.74,h*.60),(w*.75,h*.56),(w*.78,h*.58)], fill=accent)
    elif any(k in subject for k in ("scale", "size", "distance", "compare", "million", "percent")) or layout == 3:
        base = int(h*.70)
        for j, height in enumerate((110, 220, 360, 500)):
            x = int(w*.55 + j*82)
            d.rounded_rectangle((x, base-height, x+55, base), radius=12, fill=accent if j == 3 else tuple(int((a[q]+b[q])/2) for q in range(3)))
            d.text((x-5, base+20), str(j+1), fill=ink, font=font(DEFAULT_BOLD, 25))
        d.line((w*.52,base,w*.90,base), fill=ink, width=5)
    elif layout in (4, 5):
        # Particle field and trajectory: useful for atoms, energy, waves, and space.
        for k in range(75):
            px = int(w*.53 + (math.sin(k*1.7+idx)*.42+.42)*w*.40)
            py = int(h*.20 + (math.cos(k*2.1+idx)*.42+.45)*h*.58)
            rr = 2 + (k % 5)
            d.ellipse((px-rr,py-rr,px+rr,py+rr), fill=accent)
        d.arc((w*.48,h*.23,w*.94,h*.82), 170, 335, fill=ink, width=7)
        d.polygon([(w*.90,h*.35),(w*.86,h*.36),(w*.88,h*.40)], fill=ink)
    else:
        # Two-panel reveal for hook, surprise, unknowns, and payoff beats.
        d.rounded_rectangle((w*.54,h*.23,w*.72,h*.73), radius=28, outline=accent, width=7)
        d.rounded_rectangle((w*.76,h*.23,w*.94,h*.73), radius=28, outline=ink, width=7)
        d.line((w*.72,h*.48,w*.76,h*.48), fill=accent, width=8)
        d.polygon([(w*.76,h*.48),(w*.73,h*.45),(w*.73,h*.51)], fill=accent)
        d.text((w*.59,h*.42), "KNOWN", fill=accent, font=font(DEFAULT_BOLD, 34))
        d.text((w*.80,h*.42), "UNKNOWN", fill=ink, font=font(DEFAULT_BOLD, 30))

    # Hierarchical typography and progress marker; never let the counter dominate.
    d.text((left, top-42), f"{idx+1:02d}  /  {total:02d}", fill=accent, font=font(DEFAULT_BOLD, 28))
    wrapped = textwrap.fill(title, width=28)
    d.multiline_text((left, top+55), wrapped, fill=ink, font=font(DEFAULT_BOLD, 68), spacing=12)
    d.line((left, bottom+28, right, bottom+28), fill=accent, width=5)
    marker = left + int((right-left) * min(1, (idx+1)/max(1,total)))
    d.ellipse((marker-10,bottom+18,marker+10,bottom+38), fill=accent)
    footer = "FILM IT  /  SCIENCE EXPLAINED"
    d.text((left, h-int(h*.08)), footer, fill=accent, font=font(DEFAULT_FONT, 24))
    im.save(out, quality=95)


def make_visual_video(plan: dict[str, Any], project: dict[str, Any], work: Path, out: Path, target_duration: float) -> None:
    scenes = plan.get("scenes", []) or fallback_plan(project, []).get("scenes", [])
    cards = work / "cards"; cards.mkdir(exist_ok=True)
    visual_cfg = project.get("visuals", {})
    beat_seconds = float(visual_cfg.get("beat_seconds", 2.8))
    beat_records = []
    total_scene_duration = sum(float(s.get("duration", visual_cfg.get("hold_seconds_per_scene", 4))) for s in scenes) or 1.0
    duration_scale = target_duration / total_scene_duration
    concat = work / "visuals.txt"
    last_card = None
    with concat.open("w", encoding="utf-8") as f:
        for i, scene in enumerate(scenes):
            scene_duration = float(scene.get("duration", visual_cfg.get("hold_seconds_per_scene", 4))) * duration_scale
            raw_beats = scene.get("beats") or [{"label": scene.get("on_screen_text", ""), "visual_change": "establish"}, {"label": "Mechanism", "visual_change": "zoom and add arrows"}, {"label": "Implication", "visual_change": "expand the system"}]
            if not isinstance(raw_beats, list):
                raw_beats = [raw_beats]
            beat_count = max(1, len(raw_beats), math.ceil(scene_duration / max(0.5, beat_seconds)))
            each = scene_duration / beat_count
            for b in range(beat_count):
                beat = raw_beats[b % len(raw_beats)]
                beat = beat if isinstance(beat, dict) else {"label": str(beat), "visual_change": "composition shift"}
                beat_scene = dict(scene)
                beat_scene["on_screen_text"] = beat.get("label") or scene.get("on_screen_text") or scene.get("visual", "")
                beat_scene["visual"] = f"{scene.get('visual', '')}; {beat.get('visual_change', 'composition shift')}"
                asset = cards / f"scene_{i:03d}_asset.jpg"
                asset_limit = int(visual_cfg.get("ai_asset_scene_limit", 12))
                if i < asset_limit and not asset.exists():
                    fetch_scene_image(scene, project.get("visuals", {}) | project.get("project", {}), asset)
                if asset.exists(): beat_scene["_asset"] = str(asset)
                card = cards / f"scene_{i:03d}_beat_{b:02d}.png"
                make_card(beat_scene, i * 4 + b, len(scenes) * 4, project["project"] | visual_cfg, card)
                last_card = card
                f.write(f"file '{card.as_posix()}'\n")
                f.write(f"duration {each}\n")
                beat_records.append({"scene": scene.get("id", f"S{i+1}"), "beat": b + 1, "label": beat_scene["on_screen_text"], "visual_change": beat.get("visual_change", "composition shift"), "duration_seconds": each, "sfx": scene.get("sfx", "none") if b == 0 else "none"})
        if last_card:
            f.write(f"file '{last_card.as_posix()}'\n")
    (work / "shot_manifest.json").write_text(json.dumps(beat_records, indent=2), encoding="utf-8")
    style = str(project.get("project", {}).get("visual_style", "classic"))
    # Concat duration entries control how long each beat remains visible. Keep the
    # filter chain frame-preserving here; a single-frame zoompan would discard those
    # durations. Motion is created through frequent beat changes, while the style
    # filters provide the visual treatment.
    motion = "fps=24"
    style_filters = {
        "whiteboard": "drawgrid=w=96:h=96:t=1:c=black@0.07",
        "classic": "eq=contrast=1.05:saturation=1.08",
        "anime": "eq=saturation=1.30:contrast=1.08,unsharp=5:5:0.45:5:5:0",
        "kawaii": "eq=saturation=1.18:brightness=0.04,boxblur=1:1",
        "watercolor": "gblur=sigma=0.35,eq=saturation=0.90:contrast=0.96",
        "retroprint": "noise=alls=8:allf=t+u,vignette=PI/5,eq=saturation=0.82:contrast=1.12",
        "auto": "eq=contrast=1.05:saturation=1.08",
    }
    vf = f"{motion},{style_filters.get(style, style_filters['classic'])},format=yuv420p"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", vf, "-vsync", "cfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])


def make_music(seconds: float, out: Path) -> None:
    # Royalty-free procedural ambient bed, synthesized by ffmpeg.
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=44100", "-f", "lavfi", "-i", "sine=frequency=164.81:sample_rate=44100", "-filter_complex", "[0:a]volume=0.055[a0];[1:a]volume=0.035[a1];[a0][a1]amix=inputs=2,afade=t=in:st=0:d=3,afade=t=out:st=" + str(max(0, seconds-4)) + ":d=4", "-t", str(max(1, seconds)), "-c:a", "aac", str(out)])


def make_sfx(manifest: list[dict[str, Any]], seconds: float, out: Path) -> None:
    """Create sparse, scene-aware procedural SFX rather than continuous noise."""
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={max(1, seconds):.3f}"]
    filters = ["[0:a]volume=0.35[base]"]
    labels = ["[base]"]
    offset = 0.0
    event_index = 1
    for item in manifest:
        kind = str(item.get("sfx", "none")).lower()
        duration = float(item.get("duration_seconds", 0))
        if kind not in {"whoosh", "impact", "spark"}:
            offset += duration
            continue
        if kind == "whoosh":
            source = "anoisesrc=color=white:sample_rate=44100:duration=0.85"
            effect = "highpass=f=500,lowpass=f=6500,afade=t=in:d=0.12,afade=t=out:st=0.55:d=0.30,volume=0.30"
        elif kind == "impact":
            source = "aevalsrc=0.8*sin(2*PI*(95-45*t)*t)*exp(-4*t):s=44100:d=0.80"
            effect = "afade=t=out:st=0.45:d=0.35,volume=0.42"
        else:
            source = "sine=frequency=1100:sample_rate=44100:duration=0.28"
            effect = "afade=t=in:d=0.02,afade=t=out:st=0.12:d=0.16,volume=0.22"
        cmd += ["-f", "lavfi", "-i", source]
        delay_ms = int(offset * 1000)
        label = f"[s{event_index}]"
        filters.append(f"[{event_index}:a]{effect},adelay={delay_ms}|{delay_ms}{label}")
        labels.append(label)
        event_index += 1
        offset += duration
    if len(labels) == 1:
        run(cmd + ["-c:a", "aac", "-b:a", "96k", str(out)])
        return
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0,volume=0.8[out]")
    run(cmd + ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "aac", "-b:a", "96k", str(out)])


def make_srt(text: str, seconds: float, out: Path) -> None:
    words = text.split(); chunk = 12; lines = []
    for i in range(0, len(words), chunk):
        start = seconds * i / max(1, len(words)); end = seconds * min(i+chunk, len(words)) / max(1, len(words))
        def ts(v):
            ms = int(v*1000); return f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d},{ms%1000:03d}"
        lines.append(f"{i//chunk+1}\n{ts(start)} --> {ts(end)}\n{' '.join(words[i:i+chunk])}\n")
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--project", default="project.yml"); args = ap.parse_args()
    project = apply_workflow_inputs(load_project(Path(args.project))); p = project["project"]
    outdir = ROOT / project.get("output", {}).get("directory", "output"); work = outdir / "work"
    outdir.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    print(f"Building {p.get('title','Forge Film')} at {p.get('width',1920)}x{p.get('height',1080)} 16:9")
    sources = fetch_sources(project, work)
    plan = call_openrouter(project, sources) or fallback_plan(project, sources)
    publishing = make_publishing_metadata(plan, project, sources)
    p["title"] = publishing["title"]
    plan["title"] = publishing["title"]
    plan["description"] = publishing["description"]
    plan["hashtags"] = publishing["hashtags"]
    plan["keywords"] = publishing["keywords"]
    write_script(plan, work)
    write_publishing_package(publishing, outdir)
    audio = work / "narration.wav"
    narration = plan.get("narration", "")
    tcfg = project.get("narration", {})
    ok = gemini_tts(narration, tcfg, audio) if "google_gemini_tts" in tcfg.get("provider_order", []) else False
    if not ok and "edge_tts" in tcfg.get("provider_order", []): ok = edge_tts(narration, tcfg, audio)
    if not ok:
        print("No voice provider succeeded; creating silent placeholder audio.")
        make_silent_audio(audio, p.get("target_duration_minutes", 8) * 60)
    target_dur = max(1.0, float(p.get("target_duration_minutes", 8)) * 60.0)
    visuals = work / "visuals.mp4"; make_visual_video(plan, project, work, visuals, target_dur)
    manifest = json.loads((work / "shot_manifest.json").read_text(encoding="utf-8"))
    music = work / "music.m4a"; make_music(target_dur, music)
    sfx = work / "sfx.m4a"; make_sfx(manifest, target_dur, sfx)
    final = outdir / project.get("output", {}).get("final_filename", "final.mp4")
    mix = f"[2:a]volume=0.10[m];[3:a]volume=0.05[s];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur={target_dur},atrim=duration={target_dur}[n];[n][m][s]amix=inputs=3:duration=longest:dropout_transition=2[a]"
    run(["ffmpeg", "-y", "-i", str(visuals), "-i", str(audio), "-i", str(music), "-i", str(sfx), "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-t", str(target_dur), str(final)])
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(final)]).stdout.strip() or target_dur)
    if project.get("subtitles", {}).get("enabled", True): make_srt(narration, dur, outdir / "narration.srt")
    (outdir / "claims.json").write_text(json.dumps(plan.get("claims", []), indent=2), encoding="utf-8")
    bundle = outdir / "forge-film-project.json"
    bundle.write_text(json.dumps({"project": project, "plan": plan, "publishing": publishing, "sources": sources}, indent=2, ensure_ascii=False), encoding="utf-8")
    (outdir / "run_metadata.json").write_text(json.dumps({"title": p.get("title"), "duration_seconds": dur, "resolution": [p.get("width",1920), p.get("height",1080)], "aspect_ratio": "16:9", "style": p.get("visual_style"), "voice": tcfg.get("voice"), "sources": len(sources)}, indent=2), encoding="utf-8")
    print(f"DONE: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
