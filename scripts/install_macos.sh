#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
state="$HOME/.hermes/live-clipper-v2"
plist="$HOME/Library/LaunchAgents/com.techfren.live-clipper-v2.plist"
hermes_venv="$HOME/.hermes/hermes-agent/venv/bin"
export PATH="$repo/.venv/bin:$hermes_venv:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
python_bin="$(command -v python3.11 || true)"

test -n "$python_bin" || { echo "Python 3.11+ is required" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)" >&2; exit 1; }
command -v yt-dlp >/dev/null || { echo "yt-dlp is required (brew install yt-dlp)" >&2; exit 1; }
command -v transcribe-anything >/dev/null || command -v tr >/dev/null || { echo "transcribe-anything is required" >&2; exit 1; }

mkdir -p "$state/logs" "$state/run" "$HOME/Library/LaunchAgents"
"$python_bin" -m venv "$repo/.venv"
"$repo/.venv/bin/pip" install --upgrade pip
"$repo/.venv/bin/pip" install -e "$repo"
sed -e "s|__REPO__|$repo|g" -e "s|__HOME__|$HOME|g" -e "s|__HERMES_VENV__|$hermes_venv|g" "$repo/scripts/com.techfren.live-clipper-v2.plist" > "$plist"
rm -f "$state/run/worker.lock"
launchctl bootout "gui/$(id -u)/com.techfren.live-clipper-v2" 2>/dev/null || true
for _ in $(seq 1 20); do
  if ! launchctl print "gui/$(id -u)/com.techfren.live-clipper-v2" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
if ! launchctl bootstrap "gui/$(id -u)" "$plist"; then
  launchctl bootout "gui/$(id -u)/com.techfren.live-clipper-v2" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$plist"
fi
launchctl kickstart -k "gui/$(id -u)/com.techfren.live-clipper-v2"
echo "Installed com.techfren.live-clipper-v2 from $repo"
