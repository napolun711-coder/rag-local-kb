# Security Policy

## Supported versions

Security fixes currently target the `0.1.x` MVP line.

## Intended deployment

This project is designed for local, single-user use and binds to `127.0.0.1` by
default. Loopback requests have full local access. Settings and administrative
writes are host-only.

Do not expose the application directly to the public internet or an untrusted
LAN. Remote deployment requires, at minimum:

- authentication and per-user authorization
- CSRF protection for state-changing browser requests
- TLS termination
- request-size and rate limits
- secure secret storage
- a review of file-open, download, deletion, memory, and settings permissions

## Same-LAN device access

The host can opt in to a same-LAN pairing surface for personal devices.
Pairing requests are approved on the host computer; approved devices receive an
HttpOnly cookie and can be revoked from the host settings page. Device
management and settings writes remain host-only.

LAN chat/upload support is a release target and is being hardened. Until that
work is complete, do not treat the LAN surface as internet-grade authentication:
there is no TLS termination, no per-user account model, and no public-network
hardening. Use it only on a trusted private network.

The current implementation is intentionally conservative: chat, document,
memory-admin, settings, and other sensitive routes still check for a loopback
client, while the middleware may allow a paired device through to other routes.
This boundary is under review and has dedicated tests tracked for the release
gate.

Known release-gate gaps for the LAN surface:

- consistent authorization across chat, document, ingest, memory, OCR, and TTS
- CSRF/origin validation for paired state-changing requests
- per-device upload rate and quota limits
- automated pairing, revocation, and access-boundary tests

Do not treat the current LAN surface as a supported internet-facing deployment.

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
