#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
state="$HOME/.hermes/live-clipper-v2"
plist="$HOME/Library/LaunchAgents/com.techfren.live-clipper-v2.plist"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)" >&2; exit 1; }
command -v yt-dlp >/dev/null || { echo "yt-dlp is required (brew install yt-dlp)" >&2; exit 1; }
command -v transcribe-anything >/dev/null || command -v tr >/dev/null || { echo "transcribe-anything is required" >&2; exit 1; }

mkdir -p "$state/logs" "$state/run" "$HOME/Library/LaunchAgents"
python3 -m venv "$repo/.venv"
"$repo/.venv/bin/pip" install --upgrade pip
"$repo/.venv/bin/pip" install -e "$repo"
sed -e "s|__REPO__|$repo|g" -e "s|__HOME__|$HOME|g" "$repo/scripts/com.techfren.live-clipper-v2.plist" > "$plist"
rm -f "$state/run/worker.lock"
launchctl bootout "gui/$(id -u)/com.techfren.live-clipper-v2" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl kickstart -k "gui/$(id -u)/com.techfren.live-clipper-v2"
echo "Installed com.techfren.live-clipper-v2 from $repo"

