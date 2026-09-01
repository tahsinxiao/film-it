#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

APP_DIR="${HOME}/film-it-worker"
REPO_URL="https://github.com/tahsinxiao/film-it.git"
PORT="${FILM_IT_PORT:-8787}"

printf '\nFilm It native Termux installer\n'
printf 'Target directory: %s\n' "$APP_DIR"
printf 'Worker port: %s (localhost only by default)\n\n' "$PORT"

pkg update -y
pkg upgrade -y
pkg install -y git python ffmpeg curl jq openssh termux-services

termux-setup-storage || true

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only origin main
fi

mkdir -p "$APP_DIR/jobs" "$APP_DIR/assets" "$APP_DIR/logs" "$APP_DIR/output"

# Termux owns the pip package; do not self-upgrade pip because Termux blocks it.
python -m pip install --no-cache-dir pyyaml requests pillow yt-dlp edge-tts

cat > "$APP_DIR/.env" <<EOF
FILM_IT_PORT=$PORT
FILM_IT_BIND=127.0.0.1
FILM_IT_ROOT=$APP_DIR
EOF
chmod 600 "$APP_DIR/.env"

mkdir -p "$PREFIX/var/service/film-it-worker"
cat > "$PREFIX/var/service/film-it-worker/run" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "$APP_DIR"
exec 2>>"$APP_DIR/logs/worker.log"
exec python "$APP_DIR/termux/worker.py"
EOF
chmod +x "$PREFIX/var/service/film-it-worker/run"

# Register the runit service when available, but use a direct launcher as the
# primary path. This also works before the termux-services daemon is reloaded.
mkdir -p "$PREFIX/var/service" "$PREFIX/var/service/film-it-worker"
sv-enable film-it-worker >/dev/null 2>&1 || true

cat > "$PREFIX/bin/film-it-start" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="${HOME}/film-it-worker"
PORT="${FILM_IT_PORT:-8787}"
PID_FILE="$APP_DIR/.worker.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  printf 'Film It worker is already running.\n'
  exit 0
fi
rm -f "$PID_FILE"
cd "$APP_DIR"
nohup env FILM_IT_ROOT="$APP_DIR" FILM_IT_PORT="$PORT" FILM_IT_BIND=127.0.0.1 python "$APP_DIR/termux/worker.py" >> "$APP_DIR/logs/worker.log" 2>&1 &
echo $! > "$PID_FILE"
sleep 1
printf 'Film It worker started on http://127.0.0.1:%s\n' "$PORT"
EOF
cat > "$PREFIX/bin/film-it-stop" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
APP_DIR="${HOME}/film-it-worker"
PID_FILE="$APP_DIR/.worker.pid"
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  rm -f "$PID_FILE"
fi
printf 'Film It worker stopped.\n'
EOF
cat > "$PREFIX/bin/film-it-status" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
APP_DIR="${HOME}/film-it-worker"
PORT="${FILM_IT_PORT:-8787}"
PID_FILE="$APP_DIR/.worker.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  printf 'Process: running (PID %s)\n' "$(cat "$PID_FILE")"
else
  printf 'Process: stopped\n'
fi
curl -fsS "http://127.0.0.1:$PORT/health" || true
printf '\n'
EOF
chmod +x "$PREFIX/bin/film-it-start" "$PREFIX/bin/film-it-stop" "$PREFIX/bin/film-it-status"

# Start directly so installation succeeds even if runit has not reloaded yet.
"$PREFIX/bin/film-it-start" || true

printf '\nInstallation complete.\n'
printf 'Worker directory: %s\n' "$APP_DIR"
printf 'Health check:     film-it-status\n'
printf 'Start:             film-it-start\n'
printf 'Stop:              film-it-stop\n'
printf 'Logs:              tail -f %s/logs/worker.log\n' "$APP_DIR"
printf '\nThe worker binds to localhost only. Do not expose it to the internet until authentication and a private tunnel are configured.\n'
