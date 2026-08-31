#!/usr/bin/env python3
"""Small, localhost-first Film It worker for native Termux.

This service intentionally does not execute arbitrary shell commands received over
HTTP. It accepts a local project path and runs the checked-in Film It builder.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("FILM_IT_ROOT", Path.home() / "film-it-worker")).expanduser().resolve()
HOST = os.environ.get("FILM_IT_BIND", "127.0.0.1")
PORT = int(os.environ.get("FILM_IT_PORT", "8787"))
TOKEN_FILE = ROOT / ".worker_token"
LOG_DIR = ROOT / "logs"
JOBS_DIR = ROOT / "jobs"

ROOT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

if TOKEN_FILE.exists():
    TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
else:
    TOKEN = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(TOKEN + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)

state = {"status": "idle", "job_id": None, "started_at": None, "finished_at": None, "last_error": None}
lock = threading.Lock()


def write_state() -> None:
    (JOBS_DIR / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_job(job_id: str, project_path: Path) -> None:
    with lock:
        state.update(status="running", job_id=job_id, started_at=time.time(), finished_at=None, last_error=None)
        write_state()
    log_path = LOG_DIR / f"job-{job_id}.log"
    try:
        if not project_path.is_file() or project_path.suffix.lower() not in {".yml", ".yaml"}:
            raise ValueError("project_path must point to a local .yml or .yaml file")
        builder = ROOT / "scripts" / "build_longform.py"
        if not builder.is_file():
            raise FileNotFoundError(f"Film It builder not found: {builder}")
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(["python", str(builder), "--project", str(project_path)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        with lock:
            state.update(status="completed", finished_at=time.time())
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\nERROR: {exc}\n")
        with lock:
            state.update(status="failed", finished_at=time.time(), last_error=str(exc))
    finally:
        with lock:
            write_state()


class Handler(BaseHTTPRequestHandler):
    server_version = "FilmItTermux/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        (LOG_DIR / "worker-access.log").open("a", encoding="utf-8").write((fmt % args) + "\n")

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "service": "film-it-worker", "status": state["status"]})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        if self.path == "/status":
            with lock:
                self.send_json(200, dict(state))
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        if self.path != "/jobs":
            self.send_json(404, {"error": "not found"})
            return
        if state["status"] == "running":
            self.send_json(409, {"error": "a job is already running"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            project_path = Path(str(body.get("project_path", ROOT / "project.yml"))).expanduser().resolve()
            if ROOT not in project_path.parents and project_path != ROOT / "project.yml":
                raise ValueError("project_path must be inside the Film It worker directory")
            job_id = time.strftime("%Y%m%d-%H%M%S")
            threading.Thread(target=run_job, args=(job_id, project_path), daemon=True).start()
            self.send_json(202, {"accepted": True, "job_id": job_id, "status_url": "/status"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


if __name__ == "__main__":
    write_state()
    print(f"Film It worker listening on http://{HOST}:{PORT}")
    print(f"Token file: {TOKEN_FILE}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
