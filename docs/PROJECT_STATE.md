# Project State

Updated: 2026-10-27

## Scope

- Local, self-hosted RAG knowledge base implemented as a modular monolith.
- Runtime data is owned by this application under `data/`, `knowledge/`, and
  `logs/`.
- Any external application used as reference material is read-only, is not a
  runtime dependency, and its business data must not be read or copied.
- Product-facing names, documentation, configuration, and examples must remain
  generic and suitable for open-source distribution.

## Architecture

- `main.py`: resilient application launcher and port fallback.
- `app/main.py`: sole application composition root.
- `app/platform/`: configuration, paths, logging, errors, and ports.
- `app/api/`: shared system and page routes.
- `app/features/`: chat, documents, ingest, knowledge, memory, and settings.
- `app/adapters/lightrag/`: stable HTTP client contract.
- `services/lightrag_shell/`: independent HTTP compatibility shell.
- `services/lightrag_engine/`: isolated local LightRAG environment.
- `scripts/lifecycle.py`: coordinated start/stop/status for all three services.

## Run and stop

For normal Windows use, double-click `start.cmd` or run:

```powershell
.\start.cmd
.\start.ps1
.\status.ps1
.\stop.ps1
```

`start.cmd` is the user-facing daily entry point. It opens the existing web UI
when all three services are already running. `start.ps1` remains the lower-level
PowerShell entry point for development and automation.

Runtime service state and process logs are under `logs/runtime/`.

## Implemented

- Static UI for chat, files, settings, memory, and supporting assets.
- Settings page connect URL auto-detects the LAN IP at request time
  (no hardcoded host; falls back gracefully when offline).
- Chat page sidebar auto-collapses when clicking anywhere outside it (desktop
  collapses to icon rail; mobile closes the drawer). Pure frontend behavior.
- Upload, local parsing, quality gate, review, correction, and explicit manual
  submission to the knowledge service.
- Document catalog, detail, controlled deletion, name/summary search, topics,
  inline safe originals, and download.
- Chat and streaming chat with server-side session persistence.
- Read-only history search across persisted chat messages.
- Memory core, session storage, long-term memory, retrieval, context assembly,
  extraction jobs, management API/UI, and advisory supervision.
- Memory runtime configuration in the local settings page.
- OpenAI-compatible LLM configuration, Ollama embedding configuration, service
  ports, runtime status, and masked secret handling.
- Deterministic port fallback and unified service lifecycle.
- Open-source release metadata, MIT license, contribution guide, security policy,
  changelog, generic product wording, and accurate setup documentation.

## History search contract

`GET /api/history/search?q=<text>&topic_id=<id>&limit=<1-20>`

- Local-only and read-only.
- Requires `q` with length 2-200; `limit` defaults to 10 and is capped at 20.
- Filters by session topic when `topic_id` is supplied.
- Searches complete `user` and `assistant` messages in non-deleted sessions.
- Excludes suppressed messages and returns a snippet plus session/topic metadata.
- Uses SQLite FTS5 when available and falls back to `LIKE`.
- Returns `{"enabled": false, "count": 0, "items": []}` when persistence is
  unavailable.

## Data and safety rules

- Tests must use `TemporaryDirectory` or another isolated app-owned directory.
- Never point automated tests at `data/memory/ragkb.db`.
- Secret values are never returned to the browser; only masked hints are shown.
- Original files are resolved by server-side job id, never by a client-supplied
  arbitrary path. Resolved paths must stay under configured data roots.
- HTML, SVG, Office files, archives, and unknown formats are never rendered
  inline under the application origin.
- Settings writes are atomic and create backups outside version control.
- Runtime setting backups may contain credentials. Never package
  `config/settings-backups/`; remove or relocate it only with explicit approval.

## Known limitations

- History search is lexical phrase matching, not semantic retrieval.
- Persisted history and history search require the memory-backed session store.
- Document topic controls require the memory database.
- Document search reads up to 20 remote pages when a filter is active.
- Local document listing does not yet degrade to a fully local-only response when
  the knowledge service is unavailable.
- OCR, inbox polling, cloud TTS, Qdrant switching, and
  advanced topic management are not part of the current MVP.
- Formal LightRAG indexing still requires the engine and shell to be healthy.
- This directory is not yet a Git repository, so release history and rollback
  currently depend on filesystem backups.
- The running services may use an older process image than the current source.
  Restart through the lifecycle after accepting all release changes.

## MVP acceptance

The current MVP passed an isolated three-service cold start using temporary
application, memory, upload, and LightRAG storage.

- Real document upload and indexing completed.
- Non-stream chat answered from the indexed document.
- Streaming chat returned valid NDJSON with references, chunks, done, and end.
- Persisted history search found the new conversation.
- Document search, topic filtering, inline original, and download worked.
- Memory persistence was ready and stored the test session.
- `/web`, `/files`, `/memory`, and `/settings` returned HTTP 200.
- The formal three-service lifecycle was restarted successfully afterward.

The MVP feature scope is complete. Remaining release actions are operational:

1. Initialize Git and create a reviewed baseline commit.
2. Exclude or relocate runtime credential backups before packaging.

No new large feature should be added during release preparation.

## Verification snapshot

- Python syntax checks for the history-search path pass.
- `tests/test_chat_history_search.py` passes 3/3 using a temporary SQLite database.
- Chat-page JavaScript syntax passes after generic wording cleanup.
- `uv lock --check` resolves successfully.
- Release documentation and UI no longer contain the removed source-specific names,
  concrete case examples, or stale stage descriptions.
- A fresh application import exposes `GET /api/history/search`; a process started
  before the code change must be restarted to see it.
- The accepted formal runtime is healthy on the default lifecycle ports with the
  current source loaded.

## UI sidebar fix (2026-10-27)

Scope: `static/index.html` only (sidebar CSS + sidebar HTML + sidebar toggle init).

- The sidebar now scrolls vertically (`overflow-y:auto`): on short windows the
  bottom "系统状态" rows and version line are reachable instead of clipped.
- The sidebar toggle button moved onto the brand row; it no longer occupies
  its own header line.
- Fixed the desktop toggle initialization: it previously force-collapsed the
  sidebar on every load (wrote "1" to localStorage then read it back) instead
  of restoring the saved state. It now restores the persisted state.
- Not yet browser-verified; needs a visual pass at 1366x768 and below-900px
  drawer widths.

## Daily launch entry (2026-10-27)

Scope: `start.cmd`, `scripts/lifecycle.py`, and `README.md`.

- Added a root `start.cmd` that checks the environment, calls the existing
  lifecycle through PowerShell, keeps failures visible, and closes after a
  successful start.
- Added `start --reuse-running` so repeated daily launches open the existing
  healthy web UI instead of reporting all services as an error.
- `open` now verifies application health before opening the browser.
- Python syntax compilation passed.
- `start.cmd --help` passed through the full CMD, PowerShell, and lifecycle
  entry chain and exposed `--reuse-running`.
- A live three-service start, browser opening, and regression suite were not run
  in this wave.

## Local deployment settings guidance (2026-10-28)

Scope: `static/settings.html`, `README.md`, and new `部署阶段.md`.

- The top local/cloud/hybrid control is now explicitly a configuration view, not
  a deployment or installation action.
- Added a local deployment readiness card derived from effective settings,
  runtime service status, and the latest supported connection checks.
- Added service-level checks for the generation model, embedding model, and
  knowledge service using the existing `/api/settings/test` response.
- Added local examples and explicit unavailable-state guidance for local
  reranking, Qdrant/cloud vector storage, OCR, and TTS.
- Documented the suggested local deployment stages and user operation flow.
- Existing settings cards and field IDs were preserved.
- Inline settings JavaScript passed `node --check`.
- No browser visual pass, live service test, or regression suite was run in this
  wave.
- Rerank, vector storage, OCR, and TTS still lack independent backend connection
  checks; their UI must not claim per-service test success.

## Frontend navigation integration (2026-10-28)

Scope: `static/theme.css`, `static/index.html`, `static/files.html`,
`static/settings.html`, and `static/memory.html`.

- Added a shared desktop/mobile page navigation for secondary pages: chat,
  files, memory, and settings.
- Added `aria-current="page"` to the active secondary-page navigation item.
- Removed duplicate header links from files and settings; navigation now lives in
  the shared app navigation row.
- Added the memory entry to the collapsed desktop rail.
- Migrated memory management to the shared theme tokens and theme switcher.
- Preserved the focused authorization flow without adding primary app navigation.
- No backend endpoints or request payloads were changed.
- Static checks passed for shared navigation counts, CSS variables, duplicate IDs,
  and inline JavaScript syntax.
- A browser visual pass at desktop, tablet, and mobile breakpoints was not run.

## Frontend consolidation and code split (2026-10-28)

Scope: all HTML pages and page-level CSS/JS under `static/`.

- Added shared feedback support for text-confirmed destructive actions via
  `RAGKB.confirmText`.
- Migrated memory management to shared toast, confirmation, theme, and
  loading/empty/error state components.
- Replaced remaining static HTML `style` attributes with page CSS classes.
- Split every page's inline style and main script into external assets:
  `index.css/js`, `files.css/js`, `settings.css/js`, `memory.css/js`,
  `pair.css/js`, and `access_required.css/js`.
- Preserved script ordering and existing backend request paths; no backend code
  was changed.
- Verified six HTML pages contain no `<style>` blocks, inline `<script>` blocks,
  or static `style=""` attributes.
- Verified all local CSS/JS asset references exist.
- All JavaScript assets pass `node --check`.
- A browser visual pass, live navigation pass, and API behavior regression were
  not run.

## Isolated real-backend smoke (2026-10-28)

- Started the real FastAPI application with an isolated `.integration-run`
  directory for data, knowledge, logs, RAG storage, app settings, and engine
  settings; the runtime settings source resolved to the isolated app.env.
- `/web`, `/files`, `/settings`, and `/memory` returned HTTP 200 and rendered
  visible pages in the browser smoke.
- `/api/settings` and `/api/memory/status` returned HTTP 200.
- `/pair`, `/access_required`, `/api/app/access/status`, and `/api/app/session`
  are not registered in the current real backend and returned 404.
- `/api/files/overview` and `/api/files/list` returned 503 because the LightRAG
  engine was not started for this app-only smoke.
- No save, clear, purge, delete, or formal configuration write was performed.
- The isolated backend process and temporary files remain under
  `.integration-run/20261028-204252`.

## LAN access and session routes (2026-10-28)

- Added a dedicated `app.features.access` module for pairing requests, device
  approvals, persistent device records, hashed device tokens, and LAN status.
- Added `/pair`, `/access_required`, `/api/app/access/status`,
  `/api/app/access/requests`, `/api/app/access/devices`, approval, claim, and
  revocation routes.
- Added server-side middleware that redirects unauthorized LAN pages to `/pair`
  and blocks unauthorized LAN APIs with HTTP 401. Local loopback requests remain
  unrestricted.
- Added `app.features.session` routes for session state, keepalive, control token,
  delayed leave, and delayed stop. Actual stop delegates to the existing
  `scripts/lifecycle.py stop`; web stop is enabled only for a lifecycle-managed
  app process and can be disabled with `RAGKB_WEB_STOP_ENABLED=off`.
- Verified local access/session routes return HTTP 200.
- Verified via an in-process ASGI contract check that unauthorized LAN pages
  redirect to `/pair`, LAN APIs are blocked, pairing approval and Cookie
  authorization work, local-only APIs remain blocked for LAN clients, and session
  mode is reported as local or LAN correctly.
- No process stop, leave, clear, purge, delete, or formal configuration write was
  executed during verification.
- A real two-device LAN browser test and lifecycle stop test remain to be run.
