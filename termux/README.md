# Film It native Termux worker

This guide turns a Xiaomi 15 into a small local Film It worker. It uses **native Termux**, not an emulated Ubuntu virtual machine. The worker is useful for persistent storage, local FFmpeg processing, cached assets, lightweight helper tasks, and future remote-job integration. It is not a promise that Wan or LTX video diffusion will run efficiently on the phone.

## What you need

You need a Xiaomi 15 with free storage, a stable Wi-Fi connection, and approximately 8–15 GB of free space for the Film It repository, Python packages, generated assets, and output files. Keep the phone connected to power for long renders. The installer uses the official Termux distribution from [F-Droid](https://f-droid.org/packages/com.termux/) or the official [Termux GitHub project](https://github.com/termux/termux-app). Do not mix Termux packages from different installation sources.

Install **Termux** first. The optional `Termux:API` add-on is not required by the current worker, but it can be installed later if we add battery, notification, or wake-lock automation.

## One-time installation

Open Termux and paste this single command:

```sh
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/tahsinxiao/film-it/main/termux/install_film_it.sh | bash
```

The installer will update Termux, install Python, FFmpeg, Git, `yt-dlp`, Edge TTS, service support, create the Film It directory, create a private worker token, and start the local service.

When it finishes, run:

```sh
film-it-status
```

A healthy result contains an HTTP response similar to:

```json
{"ok": true, "service": "film-it-worker", "status": "idle"}
```

## Basic controls

Use these commands in Termux:

```sh
film-it-start
film-it-stop
film-it-status
tail -f ~/film-it-worker/logs/worker.log
```

The worker listens only on the phone itself at `http://127.0.0.1:8787`. The `/health` endpoint is intentionally public only to the local device. Job and status endpoints require the private Bearer token stored at:

```text
~/film-it-worker/.worker_token
```

Do not post that token in chat or commit it to GitHub.

## Test a local render

The installer clones the Film It repository into:

```text
~/film-it-worker
```

To run the checked-in default project directly from Termux:

```sh
cd ~/film-it-worker
python scripts/build_longform.py --project project.yml
```

The generated files are stored under:

```text
~/film-it-worker/output
```

A local API job can be submitted from Termux like this:

```sh
TOKEN="$(cat ~/film-it-worker/.worker_token)"
curl -fsS -X POST http://127.0.0.1:8787/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_path":"/data/data/com.termux/files/home/film-it-worker/project.yml"}'
```

Check the job with:

```sh
curl -fsS http://127.0.0.1:8787/status \
  -H "Authorization: Bearer $(cat ~/film-it-worker/.worker_token)"
```

## Xiaomi battery settings

For long jobs, open Android settings and set Termux to **No restrictions** or the least restrictive battery mode available. Allow Termux to run in the background, keep Wi-Fi active during sleep if your Android version exposes that option, and keep the phone connected to power. You can also run this inside Termux before a long local job if the Termux:API package is installed:

```sh
termux-wake-lock
```

Release it when finished:

```sh
termux-wake-unlock
```

The phone may become warm during FFmpeg work. Do not place it under clothing or in a closed space, and stop the job if the device becomes excessively hot.

## What this worker does and does not do

The current worker is deliberately conservative. It runs the checked-in Film It builder from a local project path and does not execute arbitrary shell commands received over HTTP. It binds to localhost by default, requires a private token for job requests, rejects project paths outside the worker directory, and writes logs under `~/film-it-worker/logs`.

This worker can later be connected to GitHub Actions through a private tunnel or an explicit pull-based job protocol. Do not expose port 8787 directly to the public internet. If remote access is needed, use an authenticated private network such as Tailscale or a carefully configured Cloudflare Tunnel, and add request signing before enabling GitHub-triggered jobs.

The phone is a good place for Film It’s cache, FFmpeg, asset preparation, local service, and lightweight model tasks. Full Wan or LTX inference remains experimental on Android because desktop model repositories generally expect CUDA-compatible GPUs and desktop Linux runtimes. We should add a mobile model only after validating its Android runtime, memory use, thermal behavior, and output quality.

## Updating Film It

To update the worker checkout later:

```sh
cd ~/film-it-worker
git pull --ff-only origin main
film-it-start
```

If a future update changes Python dependencies, rerun:

```sh
python -m pip install --upgrade pyyaml requests pillow yt-dlp edge-tts
```

## Troubleshooting

If `film-it-status` says the service is down, run `film-it-start` and then inspect:

```sh
cat ~/film-it-worker/logs/worker.log
tail -n 100 ~/film-it-worker/logs/worker-access.log
```

If the installer reports that a package cannot be found, confirm that Termux came from F-Droid or the official GitHub release rather than an incompatible source, then run:

```sh
pkg update -y
pkg upgrade -y
```

If a render stops when the screen is off, enable the Termux wake lock and remove Android battery restrictions for Termux. If storage becomes tight, inspect generated files with:

```sh
du -sh ~/film-it-worker/*
```

Delete only old files in `output`, `assets`, or `jobs` that you no longer need.
