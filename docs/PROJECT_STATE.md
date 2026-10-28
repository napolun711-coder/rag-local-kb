# Project State

Updated: 2026-10-29

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
- OCR, inbox polling, cloud TTS, Qdrant switching, and
  advanced topic management are not part of the current MVP.
- Formal LightRAG indexing still requires the engine and shell to be healthy.
- Git is initialized and provides reviewed release history and rollback.
- The running services may use an older process image than the current source.
  Restart through the lifecycle after accepting all release changes.
- Index compatibility is established on the first guarded run for each working
  directory. Changes made before that baseline cannot be detected retroactively.

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

The MVP feature scope is complete. Git baseline history is initialized. Remaining
release actions are operational: exclude or relocate runtime credential backups
before packaging and run the final release checks.

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
- A real LAN device browser test and the isolated lifecycle stop test
  completed.

## Isolated full-service lifecycle (2026-10-28)

- Added optional lifecycle isolation via `RAGKB_ENV_FILE`,
  `RAGKB_LIGHTRAG_ENGINE_ENV_FILE`, `RAGKB_RUNTIME_DIR`, and
  `RAGKB_RUNTIME_STATE_FILE`; normal startup defaults are unchanged.
- Lifecycle state, service logs, engine data, RAG storage, knowledge input, app
  settings, and engine settings now resolve under the selected run directory.
- App child processes receive the resolved environment, runtime, and settings
  paths; shell port publication uses the isolated log root.
- Session and settings runtime readers now use the same runtime-state path as
  lifecycle, so lifecycle-managed isolated apps report web-stop capability
  correctly.
- Started an isolated full engine/shell/app group at `127.0.0.1` using ports
  `19162`/`19161`/`19180`.
- Engine, shell, and app health checks passed; files overview/list and
  access/session routes returned HTTP 200.
- Settings source resolved to the isolated app.env; runtime services reported
  engine/shell/app healthy, and local session mode reported stop capability.
- No save, delete, clear, purge, leave, stop, or formal data operation was run.
- A real LAN device test and the isolated lifecycle stop test completed.

## Isolated full-service ingestion smoke (2026-10-28)

- Started the isolated engine/shell/app group with a temporary engine config and
  temporary RAG/knowledge/data/log/runtime paths.
- Uploaded `integration-smoke.md` through the real `/upload` API.
- Local parsing and quality validation passed with score 100 and review state.
- The explicit ingest action was accepted and submitted to the knowledge service.
- Indexing failed because the configured Ollama chat model `qwen2.5:7b` is not
  installed; engine logs report HTTP 404 `model 'qwen2.5:7b' not found`.
- No question-answer call was attempted after the indexing failure.
- No retry loop was entered after the model error.
- Chat Q&A remains blocked until a local chat model is installed or the engine is
  configured to use an available local/cloud chat model.

### 3B local-model retry

- Switched only the isolated engine configuration from `qwen2.5:7b` to the
  installed `qwen2.5:3b`; the embedding model remained local `bge-m3`.
- Restarted the isolated engine/shell/app group successfully.
- A direct 3B OpenAI-compatible completion check passed.
- Uploaded a new unique isolated test document and passed parsing and quality
  validation.
- LightRAG indexing completed successfully with the 3B model and produced graph
  entities, relations, and embeddings.
- Refreshing the application knowledge status reported `done`.
- A single `/chat` call answered `ORBIT-LANTERN-3B-7419` and cited
  `integration-smoke-3b.md`.
- No cloud API key or cloud LLM was required for this smoke test.

### Real LAN device validation

- Temporarily bound only the app to `0.0.0.0:19180`; engine and shell remained
  loopback-only.
- A LAN client at `192.168.3.8` opened `/web`, was redirected to `/pair`,
  appeared in the local approval list, and was approved on the computer.
- The authorized client received a HttpOnly device cookie and accessed chat.
- LAN access to `/api/settings` remained HTTP 403.
- After a full isolated service-group restart, the same device record remained
  and the client resumed keepalive, confirming authorization persistence.
- Revoking the device removed the record; subsequent keepalive requests from the
  old device cookie returned HTTP 401.
- The isolated app was restored to `127.0.0.1` after the test.
- `/api/app/access/connect-info` now returns HTTP 200 with the actual request
  port and the primary private LAN address. The settings page does not depend
  on this compatibility endpoint; pairing, status, approval, claim,
  authorization, and revocation worked correctly.
- The lifecycle stop test stopped the active engine, shell, and app processes
  and released all related listeners.
- A prior sandboxed restart failed before it could replace the tracked service
  state and left three verified repository processes untracked. They were
  identified by PID, command line, and isolated data path, then stopped during
  the lifecycle acceptance cleanup.
- Remaining risk: after any failed start or restart, verify listener ownership
  before retrying so untracked service processes are not mistaken for stopped
  services.

### Final isolated cold-start acceptance (2026-10-29)

- Cold-started the isolated service group from
  `.integration-run/20261028-234918-full/run-env.ps1`.
- `engine`, `shell`, and `app` started on their preferred loopback ports
  `19162`, `19161`, and `19180`.
- All three health endpoints returned HTTP 200.
- The local session endpoint returned HTTP 200 with `mode=local`,
  `enabled=true`, and `can_stop=true`.
- The access connect-info endpoint returned HTTP 200 with
  `http://192.168.3.4:19180/web` and the actual request port.
- The engine reported `qwen2.5:3b` with `bge-m3:latest`; no cloud LLM was used.
- No ingestion, save, delete, purge, stop, or full regression was run during
  this acceptance check.
- The isolated service group remains running after acceptance.

## Index compatibility protection (2026-10-29)

- Added an app-side index identity manifest under the configured application
  data directory. It records a hash of the engine working directory plus the
  embedding binding, model, dimension, endpoint, vector storage type, and
  timestamps. Absolute working-directory paths are not exposed in the manifest.
- The LightRAG shell health response now exposes this non-secret runtime identity
  so the guard compares the active engine configuration rather than unloaded
  saved settings.
- A baseline is recorded on the first guarded run for each working directory.
  Existing manifests are never overwritten automatically.
- When the identity changes, chat queries and new knowledge submissions are
  rejected with `index_rebuild_required`. Read-only inspection, download,
  document status, and deletion remain available for recovery.
- The settings readiness card shows the compatibility state. After a real index
  rebuild, a local-only action with the exact confirmation `REBUILD` records the
  new identity. It never clears, rebuilds, or migrates index data.
- Added `tests/test_index_compatibility.py` covering baseline creation, mismatch
  blocking, confirmation, corrupt-manifest behavior, and secret exclusion.
- Python compilation, `settings.js` syntax checks, and all 4 focused tests passed.
- After an isolated service-group restart, the compatibility endpoint returned
  HTTP 200 with `state=compatible` for the existing isolated index.
- No chat, ingestion, deletion, configuration save, or full regression was run
  during this validation.

## Document local degradation (2026-10-29)

- `/api/files/list` and `/api/files/overview` now degrade to HTTP 200 with
  application-owned local records when the knowledge engine is unavailable or
  returns a 5xx response.
- Degraded responses set `upstream.ok=false` and include a user-facing notice.
  Remote document counts are not fabricated; the page displays them as unknown.
- The local-record table changes to “本机记录（未与知识库核对）” while degraded,
  because the application cannot determine which local jobs still exist in the
  knowledge base.
- Local search and topic filtering continue to work. Viewing, details, original
  access, and downloads remain available; selection and deletion are paused
  until knowledge state can be checked again.
- Knowledge-service 4xx request errors are not treated as availability failures
  and continue to surface as errors.
- No automatic retry loop, data migration, deletion, or configuration write was
  added.
- Added `tests/test_documents_degraded.py` covering local-record fallback,
  filtering, multi-page failure without partial remote data, overview state, 4xx
  behavior, and the API payload contract. All 6 focused tests passed in the
  project virtual environment.
- An isolated service-group restart passed. The live list endpoint returned
  HTTP 200 with `upstream=true`, 3 knowledge documents, and 1 local-only record;
  the overview also returned `upstream=true`, 3 remote documents, and 4 local
  jobs; `/files` returned HTTP 200.
- No full regression was run.
