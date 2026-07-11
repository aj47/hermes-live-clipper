# Security policy

## Supported versions

Hermes Live Clipper is an early public beta. Security fixes are applied to the latest revision on `main`.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not open a public issue for a suspected vulnerability and do not attach credentials, cookies, private media URLs, captured media, transcripts, or personal data.

Include the affected revision, a concise reproduction, impact, and any suggested mitigation. You should receive an acknowledgment within seven days.

## Local data and account access

The plugin stores captured media, transcripts, rendered clips, logs, and its SQLite database under `~/.hermes/live-clipper-v2` by default. These paths are excluded from Git, but users are responsible for local file permissions, backups, and deletion.

Optional publishing runs only after explicit confirmation and can use accounts already signed in on the local Mac. Review the generated publishing task before allowing it to continue.
