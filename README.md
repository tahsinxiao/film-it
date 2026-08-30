<div align="center">

![Forge Banner](assets/banner.webp)

# 🎬 Forge

**One story, multiple AI models, zero manual stitching.**

[![CI](https://github.com/F-R-L/forge-film/actions/workflows/ci.yml/badge.svg)](https://github.com/F-R-L/forge-film/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-green)](https://github.com/F-R-L/forge-film)

[中文文档](README.zh.md) | [Quick Start](#-quickstart)

</div>

---

Making a multi-scene AI film means logging into Kling, CogVideoX, Seedance separately — downloading frames, color-correcting between models, stitching manually. An 8-scene short can eat half a day.

**Forge automates the entire pipeline.** You write a story. Forge compiles it into a scene graph, routes each scene to the right model, runs them in parallel, keeps visual continuity across model boundaries, and outputs a single `final.mp4`.

---

## What Forge does

🧭 **Story → DAG** — GPT-4o (or Claude / DeepSeek) compiles your story into a dependency graph. Scenes with no dependencies run in parallel.

⚡ **CPM parallel scheduling** — Critical Path Method finds the longest dependency chain and prioritizes it. N workers run simultaneously, not one by one.

🎯 **Scene-type routing** — dialogue → Kling, landscapes → CogVideoX (free, local), action → Kling. Fully configurable in `forge.yaml`.

🎨 **Cross-model continuity** — when scene B (CogVideoX) follows scene A (Kling), Forge extracts A's last frame, applies histogram color matching, and passes it as the i2v seed. No jarring cuts.

🎬 **Final assembly** — once all scenes are generated, clips are concatenated in a single ffmpeg pass. Normalized resolution and frame rate. Outputs `final.mp4`.

---

## End-to-end walkthrough

Given this story (`examples/detective.txt`):

```
A weary private detective takes on a missing-person case. His client is an anxious middle-aged woman whose husband vanished without a trace three days ago.
The detective searches the husband's office and finds an unsent letter and a basement key.
Meanwhile, a mysterious man begins tailing the detective through the streets.
The detective locates the basement and discovers a secret ledger the missing husband had hidden away.
The mysterious man suddenly appears — a confrontation erupts.
The detective delivers the secret ledger to the police, and the truth finally comes to light.
```

Forge compiles it into a DAG and schedules:

```
forge plan examples/detective.txt --scenes 6

Plan: 6 scenes
DAG: {'S1': ['S2', 'S3'], 'S2': ['S4'], 'S3': ['S5'], 'S4': ['S6'], 'S5': ['S6'], 'S6': []}

Routing:
  S1  dialogue    → kling_light
  S2  action      → kling_heavy
  S3  landscape   → cogvideo
  S4  action      → kling_heavy
  S5  dialogue    → kling_light
  S6  dialogue    → kling_light

Critical path: S1 → S2 → S4 → S6  (longest chain)
Estimated time: 20 min parallel  vs  30 min serial
```

Then runs:

```
forge run examples/detective.txt --workers 4

[00:00] S1 started  (kling_light)
[00:00] S3 started  (cogvideo)       ← parallel
[05:00] S1 done  → S2 unlocked
[05:00] S2 started  (kling_heavy)
[07:00] S3 done  → S3→S2 color calibration applied
[10:00] S2 done  → S4 unlocked
[10:00] S4 started  (kling_heavy)
...
[20:00] S6 done  → assembling final.mp4

Done. Output: ./output/final.mp4
```

---

## Parallel scheduling

Forge uses the **Critical Path Method (CPM)** to find the longest dependency chain in your scene DAG and prioritizes those scenes first. Scenes with no blocking dependencies start immediately.

In the example above: S1→S2→S4→S6 is the critical path. With 4 workers, S1 and S3 launch simultaneously at t=0. The total wall time drops from 30 min (serial) to 20 min.

Speedup scales with scene independence — a story where half the scenes are parallel will run roughly 2× faster.

```mermaid
gantt
    title Without Forge — 6 scenes × 5 min = 30 min
    dateFormat mm
    axisFormat %M min
    section Sequential
    S1 :s1, 00, 5m
    S2 :s2, after s1, 5m
    S3 :s3, after s2, 5m
    S4 :s4, after s3, 5m
    S5 :s5, after s4, 5m
    S6 :s6, after s5, 5m
```

```mermaid
gantt
    title With Forge (CPM) — critical path = 15 min
    dateFormat mm
    axisFormat %M min
    section Worker 1
    S1 (Kling)     :s1, 00, 5m
    S3 (CogVideoX) :s3, after s1, 5m
    S5 (Seedance)  :s5, after s3, 5m
    section Worker 2
    S2 (Kling)     :s2, 00, 5m
    S4 (CogVideoX) :s4, after s2, 5m
    S6 (Seedance)  :s6, after s4, 5m
```

---

## Cross-model continuity

Kling, CogVideoX, and Seedance have different color profiles, exposure levels, and visual styles. Cutting directly between them produces jarring transitions.

When scene B depends on scene A and they use different backends, Forge automatically:
1. Extracts the last frame of scene A
2. Applies histogram color matching to align the color distribution
3. Passes the corrected frame as the i2v (image-to-video) seed for scene B

The result: visual continuity across model boundaries without manual color grading.

```mermaid
flowchart LR
    A["Scene A\nKling"] -->|"last frame"| B["ColorCalibrator\nhistogram match"]
    B -->|"seed image"| C["Scene B\nCogVideoX"]
    C -->|"last frame"| D["ColorCalibrator\nhistogram match"]
    D -->|"seed image"| E["Scene C\nSeedance"]
    style B fill:#f0a500,color:#000
    style D fill:#f0a500,color:#000
```

---

## 🚀 Quickstart

> [!NOTE]
> No API keys? Use `--backend mock` for a full end-to-end run with zero external dependencies.

**Requirements:** Python 3.11+ · ffmpeg · GPU optional (CogVideoX local needs CUDA 12+)

```bash
git clone https://github.com/F-R-L/forge-film
cd forge-film
pip install -e .
cp .env.example .env
```

```bash
# Run with mock backend — no API keys needed
forge run examples/detective.txt --backend mock --workers 4

# Multi-model orchestration
forge run examples/multi_backend_demo.txt --workers 4

# Inspect DAG and routing without generating video
forge plan examples/detective.txt --scenes 6

# Launch Web UI
forge webui
```

**Web UI** — Run `forge webui` to launch the Gradio interface locally.

![Forge Web UI](assets/webui_screenshot.png)

### As a library

```python
from forge.config import ForgeConfig
from forge.compiler.vision_compiler import VisionCompiler
from forge.scheduler.scheduler import ForgeScheduler

cfg = ForgeConfig("forge.yaml")
compiler = VisionCompiler(cfg.build_llm_provider())
plan = await compiler.compile(story_text, num_scenes=6)

scheduler = ForgeScheduler(plan, generate_fn, num_workers=cfg.workers)
results, failed = await scheduler.run(asset_map, output_dir="./output")
```

---

## ⚙️ Configuration

`forge.yaml` — all fields optional, falls back to environment variables and defaults.

```yaml
llm:
  provider: openai      # openai | anthropic | deepseek
  model: gpt-4o

imagegen:
  provider: mock        # mock | openai | flux

routing:
  dialogue: kling_light     # Kling v1 — lip sync & character consistency
  action: kling_heavy       # Kling v1.5 Pro — motion quality
  landscape: cogvideo       # CogVideoX local — free
  default: mock

scheduler:
  workers: 4
```

```bash
# .env
OPENAI_API_KEY=sk-...
KLING_API_KEY=...
KLING_API_SECRET=...
```

| Key | Options | Default |
|---|---|---|
| `llm.provider` | `openai` \| `anthropic` \| `deepseek` | `openai` |
| `imagegen.provider` | `openai` \| `flux` \| `mock` | `mock` |
| `validator.provider` | `openai` \| `anthropic` \| `mock` | `mock` |
| `routing.dialogue` | any backend name | `kling_light` |
| `routing.landscape` | any backend name | `cogvideo` |
| `scheduler.workers` | int | `4` |

---

## 🆚 How Forge compares

| | Forge | OpusClip Agent | Seedance Multi-shot | FilmAgent |
|---|---|---|---|---|
| Open source | ✅ MIT | ❌ Closed SaaS | ❌ | ✅ Research prototype |
| Local deployment | ✅ | ❌ | ❌ | Partial |
| Multi-model mixing | ✅ | ✅ not configurable | ❌ single model | ❌ 3D virtual space |
| Cross-model color calibration | ✅ | Unknown | N/A | N/A |
| Pluggable backends | ✅ | ❌ | ❌ | ❌ |
| Data privacy | ✅ stays local | ❌ third-party | ❌ | Partial |

---

## 🎬 Supported backends

| Backend | Type | Best for | Cost |
|---|---|---|---|
| `kling_light` | API (Kling v1) | Dialogue, character consistency, lip sync | Per-second |
| `kling_heavy` | API (Kling v1.5 Pro) | Action, complex motion, longer clips | Per-second (higher) |
| `cogvideo` | Local (CogVideoX-2b) | Landscapes, transitions, atmospheric shots | Free (GPU) |
| `seedance` | API (Seedance) | Fast motion, sports, dynamic scenes | Per-second |
| `wan` | Local (Wan 2.x) | General purpose, good quality/cost ratio | Free (GPU) |
| `mock` | Local (no-op) | Testing, CI, development | Free |

All backends implement the same `BasePipeline` interface — adding a new one takes ~50 lines.

---

## Long-form zero-budget workflow

The repository now includes a graphics-first long-form builder driven by [`project.yml`](project.yml) and a manually triggered GitHub Actions workflow at [`.github/workflows/generate-longform.yml`](.github/workflows/generate-longform.yml). It is designed for English science explainers and educational stories in **16:9 at 1920×1080 or higher**, using procedural diagrams, animated cards, charts, particles, typography, and optional AI-generated inserts instead of depending on human footage.

Open the **Actions** tab, select **Generate long-form video**, and choose **Run workflow**. The form lets you enter the script or topic, YouTube reference URL, duration, visual style, narration voice, and subtitle preference directly. Film It generates the title automatically, so you do not need to enter one. You do not need to edit `project.yml`, Python, or workflow files. The finished MP4, optional enhanced 2K60 MP4, narration, subtitles when enabled, source report, claims list, shot data, and publishing package are assembled into the `film-it-output.zip` download and uploaded as the `film-it-video-package` artifact. Artifact retention is configured for seven days.

The `script_or_topic` field accepts either a topic such as `Why do black holes bend time?` or your own narration/story notes. When a custom script is supplied, Film It preserves it as the narration foundation and uses OpenRouter to organize the visual beats, claims, transitions, and SFX. Film It also generates a clickable-but-accurate title, a ready-to-publish YouTube description, hashtags, and keywords. The `source_url` field accepts one authorized YouTube reference URL; the workflow extracts available public metadata and captions as research context rather than downloading source footage.

The workflow uploads one artifact named `film-it-video-package`. Download `film-it-output.zip`; inside it are `final.mp4`, `final_2k60.mp4`, optional `narration.srt`, and one clearly labeled `metadata.txt` containing the generated title, description, hashtags, keywords, video details, enhancement details, and reference-source list. You can copy the generated publishing text into YouTube Studio without manually writing it.

### Optional 2K60 enhancement

The workflow preserves the base `final.mp4` master and, by default, creates `final_2k60.mp4` at **2560×1440 and 60 fps**. The enhancement stage uses FFmpeg Lanczos scaling for spatial enlargement and motion-compensated `minterpolate` for frame-rate conversion. This produces a valid higher-resolution, smoother deliverable without paid software; it cannot recover detail that was never present in the source, and motion interpolation may occasionally create artifacts around fast-moving elements. If motion interpolation fails, the workflow automatically retries with a safer Lanczos-plus-constant-frame-rate conversion.

The behavior is controlled under `enhancement` in `project.yml`. Set `mode: ffmpeg_fast` when reliability and speed matter more than interpolated motion, or set `enabled: false` and keep the 1080p24 master only.

### Repository secrets

Add these under **Settings → Secrets and variables → Actions**. The workflow can operate with only some of them because every network provider has a deterministic fallback.

| Secret | Purpose | Required |
|---|---|---|
| `OPENROUTER_API_KEY` | Script, scene plan, and visual direction using a configured free model | Optional; without it a deterministic fallback script is used |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Expressive Gemini TTS narration | Optional; Edge TTS is attempted next |

The default narration configuration supports selectable `female_documentary` and `male_documentary` profiles, with additional warm variants. Gemini TTS accepts natural-language delivery direction, so `expressive_prompt` can request documentary pacing, suspense, emphasis, pauses, or a controlled whisper. Hosted free-model availability and quotas can change, so the workflow never treats one provider as the only path.

### Source handling

When using the Actions form, paste an authorized YouTube reference URL into `source_url`. The workflow uses `yt-dlp` to retrieve public metadata and an available English transcript when possible. It stores source information for research context and claims reporting; it does **not** reuse the source video’s footage. The committed `project.yml` remains as a safe default configuration, but normal users do not need to edit it.

### Visual and audio fallback design

A full 5–10 minute render is assembled from many short 16:9 visual beats so that the final video remains long-form without requiring a hosted AI video model to generate hundreds of minutes of footage. Film It uses a retention grammar of **hook → paradigm flip → mechanism → evidence → scale → surprise → payoff**. Each scene is divided into short beat cards, normally no longer than about three seconds, with changes in labels, diagrams, particles, color, framing, or composition. The output combines narration, low-volume procedural ambient music, and **scene-aware procedural SFX**.
 The generated shot plan can mark moments as `whoosh`, `impact`, or `spark`; Film It places those effects at the matching scene time, keeps them below the narration, normalizes the mix, and exports optional `.srt` subtitles. Short AI image or video inserts can be added later behind the provider interfaces without making them a hard dependency.

### Important zero-budget limitation

A public GitHub repository makes the workflow convenient to inspect and rerun, but free hosted AI services can still impose quotas, rate limits, preview-model changes, or temporary failures. GitHub Actions also provides finite runner time and artifact retention. The design therefore prioritizes reproducible media generation and transparent logs, while treating OpenRouter, Gemini, and other hosted services as replaceable enhancements rather than guaranteed infrastructure.

---

## ❓ FAQ

**Do I need a GPU?**
No. All cloud backends (Kling, Seedance) are API-based. A GPU is only needed if you use the local CogVideoX or Wan backends.

**Can I use only one video model?**
Yes. Set all routing keys to the same backend in `forge.yaml`, or pass `--backend kling_light` on the CLI.

**How does Forge handle API failures?**
Each scene retries up to `scheduler.max_retries` times (default: 2) with exponential backoff. Failed scenes are returned in the `failed` dict so you can inspect or re-run them.

**What video formats does the assembler output?**
H.264 MP4 by default via ffmpeg. Resolution and frame rate are normalized across all input clips before concatenation.

**Can I plug in my own video model?**
Yes — subclass `BasePipeline` in `forge/generation/base.py`, implement `generate()`, and register it in the router. No changes needed elsewhere.

---

## 🏗️ Architecture

![Forge Architecture](assets/architecture.webp)

```
forge.yaml
    │
    ├── VisionCompiler   story → ProductionPlan (scenes + DAG)
    ├── AssetFoundry     reference images per character / location
    ├── ForgeScheduler   CPM critical path · N workers · retries
    │       ├── PipelineRouter    scene_type → kling / cogvideo / seedance
    │       └── ColorCalibrator   last-frame histogram match for i2v
    ├── VLM Validator    optional frame consistency check
    └── StreamAssembler  ffmpeg concat → final.mp4
```

---

## 📁 Project structure

```
forge/
  compiler/      # Story → DAG (LLM-driven)
  providers/     # LLM / ImageGen / VLM abstractions
  scheduler/     # DAG topology + CPM scheduling
  generation/    # Video backend pipelines
  continuity/    # Cross-model color calibration
  assets/        # Reference image generation + cache
  validation/    # VLM frame consistency check
  assembler/     # Streaming video concatenation
  cli.py
  webui/
forge.yaml
examples/
tests/         # 20 tests, no API keys needed
benchmarks/
```

---

## 🗺️ Roadmap

- [x] Multi-model semantic routing by scene type
- [x] Cross-model color calibration (histogram matching)
- [x] Pluggable LLM / ImageGen / VLM providers
- [x] CPM scheduling with backend-aware duration estimates
- [x] forge.yaml unified config
- [x] Gradio Web UI
- [x] CogVideoX local backend
- [ ] Seedance backend
- [ ] Wan 2.x backend
- [ ] GPU-accelerated local video assembly
- [ ] Story template library
- [ ] Real benchmark results with Kling API

---

## 🤝 Contributing

PRs and issues welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 License

MIT — see [LICENSE](LICENSE)
