# Security Policy

## Supported versions

Security fixes currently target the `0.1.x` MVP line.

## Intended deployment

This project is designed for local, single-user use and binds to `127.0.0.1` by
default. Administrative and write APIs are restricted to the local machine.

Do not expose the application directly to the public internet or an untrusted
LAN. Remote deployment requires, at minimum:

- authentication and per-user authorization
- CSRF protection for state-changing browser requests
- TLS termination
- request-size and rate limits
- secure secret storage
- a review of file-open, download, deletion, memory, and settings permissions

## Secrets and data

- Keep `.env` and `services/lightrag_engine/.env` private.
- Never commit API keys, tokens, databases, uploads, parsed documents, logs, or
  setting backups.
- Do not include production data in bug reports.
- Rotate any credential that may have been exposed.

## Reporting a vulnerability

Use the repository host's private security-advisory feature when available.
Include a minimal reproduction, affected version, impact, and any suggested
mitigation. Do not publish working exploit details before a fix is available.

If private reporting is unavailable, contact the repository maintainer through
the private contact method listed on the repository profile.
