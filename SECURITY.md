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

The host can opt in to a same-LAN pairing surface for personal devices. Pairing
requests are approved on the host computer; approved devices receive an
HttpOnly access cookie and a readable CSRF cookie, and can be revoked from the
host settings page.

Paired LAN devices may use:

- chat and chat-session APIs
- upload, review, and ingest APIs
- TTS synthesis
- read-only document, topic, graph, memory, and settings views

Host-only operations include settings writes and connection tests, document and
topic changes, deletion, memory mutations, ingest queue/session controls, device
approval/revocation, index-acceptance, and service stop/control.

LAN state-changing requests use a double-submit CSRF token: the browser reads
the `ragkb_csrf` cookie and sends `X-RAGKB-CSRF`; the server requires both to
match. Uploads are additionally limited per device by files/bytes within a
moving time window. The limits are configured in `.env` and are process-local.

There is no TLS termination and no per-user account model. Use this only on a
trusted private LAN. Do not expose the application directly to the public
internet or an untrusted LAN. Internet-facing deployment requires per-user
authentication, TLS, CSRF review, rate limiting, and a full data-permission
review.

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
