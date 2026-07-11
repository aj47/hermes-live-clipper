# Hermes Live Clipper

A macOS-first Hermes plugin that monitors a public YouTube or Twitch livestream, captures it in rolling chunks, transcribes locally with Parakeet through `transcribe-anything`, asks the configured Hermes model for structured clip candidates, and renders resource-aware horizontal drafts.

## MVP behavior

- One active stream; additional jobs queue.
- Scheduled streams wait and active streams join at the live edge.
- Capture uses `yt-dlp` resolution plus FFmpeg 30–60 second transport-stream chunks.
- Transcription remains local and retains word-level timing.
- Candidate analysis begins after five minutes and runs through `ctx.llm.complete_structured()`.
- High-confidence candidates render while CPU, memory, disk, and transcript lag remain safe.
- Source media, transcripts, and drafts remain until the user deletes the job.
- The existing RTMP clipper is not read, modified, stopped, or migrated.

## Requirements

- macOS with Python 3.11+
- Hermes Agent with dashboard plugins and plugin LLM access
- `ffmpeg` and `ffprobe`
- `yt-dlp`
- `transcribe-anything` configured for local Parakeet transcription

## Install on a Hermes Mac

Clone the repository into the Hermes plugin directory and run:

```bash
cd ~/.hermes/plugins/hermes-live-clipper
chmod +x scripts/install_macos.sh
./scripts/install_macos.sh
```

Restart the Hermes dashboard after installation so it discovers `dashboard/manifest.json`. The worker runs under the separate LaunchAgent label `com.techfren.live-clipper-v2` and stores state in `~/.hermes/live-clipper-v2`.

Useful checks:

```bash
launchctl print gui/$(id -u)/com.techfren.live-clipper-v2
.venv/bin/hermes-live-clipper status
tail -f ~/.hermes/live-clipper-v2/logs/worker.stderr.log
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/pytest
```

## Security and content responsibility

Resolved media URLs may contain temporary credentials. They are held in worker memory and are not stored in the job database or exposed to the dashboard. Captured media and transcripts are ignored by Git. Users are responsible for ensuring they have authorization to copy, edit, and redistribute content.

Private, subscriber-only, unavailable, unsupported, and DRM-protected streams are rejected when the resolver cannot produce a playable public media source.

## Current exclusions

Publishing, vertical reframing, automatic captions, multi-track editing, multiple simultaneous streams, Windows/Linux packaging, and automatic retention cleanup are intentionally outside v0.1.

