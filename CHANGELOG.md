# Changelog

## 0.1.2 - 2026-10-30

- ingest: submit reviewed parsed text through LightRAG's native
  `/documents/text` path instead of treating it as a source file; fixes
  `legacy source file not found` failures for normal documents and non-ASCII
  filenames
- ingest: keep quality results advisory in the UI wording; users can still
  choose "仍要入库" and the system records the decision
- ingest: show the exact LightRAG failure receipt on the upload card instead of
  only a generic failure sentence
- ingest: accept common plain-text formats (`.log`, `.xml`, `.yaml`, `.yml`,
  `.ini`, `.cfg`, `.toml`) and stop advertising unsupported `.xls`
- lifecycle: resolve a relative engine data directory against the project root,
  not the caller's current working directory
- docs: add a complete Chinese installation, first-run, usage, backup, upgrade,
  and troubleshooting guide
- mobile UI: remove duplicate file-management entry from the three-dot menu;
  keep the clear-conversation action only when a conversation has messages
- desktop UI: align the three sidebar status rows, showing local vector storage
  with a green dot and `正常`

## 0.1.1 - 2026-10-30

- deployment: pin fresh Windows installs to managed Python 3.12 through
  `.python-version`, `UV_PYTHON_PREFERENCE=only-managed`, and explicit
  `uv sync --python 3.12`; fail setup if the app environment is not 3.12
- deployment: install PyYAML 6.0.3 from the existing cp312 Windows wheel instead
  of selecting Python 3.15 and attempting a source build requiring Visual C++
  Build Tools
- ci: verify the deployment environment is Python 3.12 and include
  `.python-version` in release/upgrade entries

## 0.1.0 - 2026-10-30

- upload chain: `保存并入库` now submits, image caption/document naming works,
  and edits keep a previous-content backup
- upload chain: live knowledge-index status, duplicate-submit protection, and
  explicit failure reasons on the card and companion
- upload chain: restore pending, blocked, failed, skipped, and in-flight jobs
  after a page refresh; recently skipped jobs can be resumed from chat
- upload chain: the session allow-all switch and manual force submission now
  perform the actions the UI describes
- upload chain: stale `ingesting` jobs with no knowledge document after 60
  minutes return to review with an explicit `track_not_found` reason
- files page: show state, failure reason, and recovery actions for all
  non-terminal local jobs, not only skipped files
- files page: keep the delete action bar visible while selecting across pages,
  submit with Enter after typing `Yes`, and repeat the original-file boundary
- batch upload: group more than five files into one collapsible batch card with
  aggregate counts, a single poller, companion queue state, page-restore
  grouping, and a files-page batch filter with bulk retry for failed items
- upload queue: `/upload` returns 202 and enqueues a durable job; a bounded
  background worker survives restarts, retries unexpected failures with
  backoff, supports pause/resume and queued-job cancellation, and deduplicates
  uploads by content hash
- ingest UX: labeled processing timeline, automatic knowledge-index status
  updates without manual refresh, compact success cards with next actions,
  actionable failure guidance, and a retry-safe atomic result writer
- chat: reference chips resolve knowledge document names back to local
  originals and support double-click/Enter opening; query failures preserve the
  upstream reason and offer retry, no-database continuation, and upload actions;
  image original links use the supported origin endpoint
- memory: default to Balanced Local Memory — session history, context injection,
  fast memory, cross-session recall, and rule-based background extraction are on
  by default, with 12 recent messages / 2,000 tokens, 1,200 memory tokens, and
  600 recall tokens
- memory: direct personal-fact questions are answered from high-confidence local
  memories without calling the generation model, with a UI note and protection
  against treating knowledge-base lookups as personal memory
- deployment: one-click `deploy.cmd`, auto-preparing `start.cmd`, project-local
  uv/Python/cache handling, local Ollama detection and configuration, a
  no-write `-CheckOnly` mode, a stronger doctor, and a project-local launcher
  shortcut; no system installs, desktop writes, or model downloads
- upgrades: user-confirmed `update.cmd` with Git fast-forward, GitHub Release,
  and offline-archive modes; SHA256 verification, unsafe-path rejection,
  project-local runtime/code backups, code-only replacement, health checks, and
  rollback on failure; `package_release.ps1` and a tagged release workflow build
  code-only Windows ZIPs without user data, configuration, caches, or venvs
- setup/upgrade: run `uv` through redirected child-process streams so its
  progress output is no longer rendered as a PowerShell `NativeCommandError`
  during dependency sync
- settings: mark unimplemented confirmation modes and remove the false
  automatic-skip claim
- release hygiene: correct the documented memory defaults, document the current
  LAN boundary and release-gate gaps, add project metadata, contributor
  templates, `.gitattributes`, `.editorconfig`, and a code of conduct; remove
  local paths, the reference-application name, and the personal author email from
  Git history
- LAN access: paired devices can use chat, upload/ingest, TTS, and read-only
  document/topic/graph/memory/settings views; settings writes, deletion, memory
  mutations, queue controls, and device management remain local-only. Add
  double-submit CSRF protection, per-device upload quotas, and a read-only
  settings UI for LAN devices
- mobile UI: hide the duplicate desktop icon rail on phones, keep the hamburger
  drawer as the single entry point, fix hidden items still showing in the
  three-dot menu, and give the knowledge graph canvas the full remaining height
- release: add Windows CI and make tag releases depend on tests, JavaScript/
  PowerShell checks, checksum verification, and release-package content scans
- docs: add a Windows quick start, first-run checklist, troubleshooting guide,
  release checklist, and trusted-LAN guidance for new users

## 0.0.0 - Initial local MVP

- local FastAPI application and static web UI
- document upload, parsing, quality review, and manual knowledge submission
- document search, topic filtering, safe original open/download, and deletion
- chat, streaming responses, session persistence, and history search
- long-term memory, management UI, advisory supervision, and runtime settings
- OpenAI-compatible generation configuration and local Ollama embeddings
- LightRAG HTTP shell and isolated LightRAG 1.5.6 engine integration
- unified service lifecycle with deterministic port fallback
