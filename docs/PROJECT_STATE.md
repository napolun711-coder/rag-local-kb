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
- Qdrant switching and advanced topic management are not part of the current MVP.
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

## Inbox folder scanning (2026-10-29)

- Added `app.features.inbox` with opt-in configuration, a persistent scan state,
  a background worker, read-only status, and a local-only manual scan API.
- Inbox scanning is disabled by default. Enabling it requires an existing
  directory and an application restart.
- The scanner accepts only top-level regular files with allowed extensions,
  skips hidden/temporary files and symlinks, enforces the upload size limit, and
  requires a stable size plus modification time before reading content.
- Filesystem overlap with the project root, knowledge, upload, parsed, or log
  directories is rejected.
- Accepted files reuse the existing upload, local parsing, quality, and review
  pipeline. They remain in the inbox directory; the scanner never moves,
  overwrites, or deletes source files, and never submits content to the knowledge
  service automatically.
- SHA-256 content fingerprints are persisted so identical content is accepted
  only once. Failures are recorded without retrying unchanged files on every
  poll.
- Scanner metadata is stored under the application data directory in
  `inbox-state.json`. It contains no file contents or credentials.
- The settings page now exposes enable, poll interval, settle interval, and
  directory fields. The legacy confirmation choice is fixed to `review`; the
  existing card remains present.
- The document page displays actual inbox state and enables manual scan only
  when the feature is enabled.
- Added a local-only, control-token-protected folder-open action for the
  configured inbox path. It accepts no client-supplied path; invalid tokens
  returned HTTP 403 during verification.
- Added `tests/test_inbox_scanner.py` covering disabled behavior, settling,
  one-time acceptance, content deduplication, failure retention, and ignored
  files. All 5 inbox tests passed; the combined focused suite passed 15/15.
- Isolated end-to-end acceptance enabled scanning through temporary environment
  overrides only. One Markdown file was accepted into `review` with
  `knowledge.state=not_submitted`, the source file remained present, and a
  manual scan returned success.
- The isolated service group was restarted afterward without overrides;
  `/api/inbox/status` reported `enabled=false` and `running=false`.
- The settings API returned the new inbox fields and effective paths without a
  configuration save. The document overview used the same disabled scanner
  state.
- The test source and generated review job remain under `.integration-run` only.
- No formal configuration, knowledge submission, source move, or source deletion
  was performed.

## Local OCR integration (2026-10-29)

- Added `app.features.ocr` and a Windows built-in OCR helper at
  `scripts/windows_ocr.ps1`. Recognition runs locally through
  `Windows.Media.Ocr`; no cloud OCR request or API key is used.
- Supported settings are mode (`off`, `manual`, `auto`), local provider,
  embedded-image inclusion, language tag, per-file size limit, and per-image
  timeout. Cloud OCR fields remain visible but read-only.
- The settings page reports the requested language, installed OCR language
  packs, engine availability, and provides a local test action. It does not
  claim OCR is enabled when the Windows OCR engine or language pack is missing.
- The parser now retains original bytes for standalone images. Existing PDF,
  Word, Excel, and PowerPoint image extraction feeds the same local OCR path.
- Auto mode runs OCR during local parsing; manual mode uses the existing
  “识别图片” action. OCR text is merged into the parsed content and then passes
  through the unchanged quality gate and review queue.
- OCR failures, unsupported images, timeouts, size violations, and empty output
  are recorded explicitly and do not become successful empty content.
- Added `tests/test_ocr.py` covering language selection, image-byte retention,
  text merging, disabled behavior, and truthful unavailable state. All 6 OCR
  tests passed; the combined focused suite passed 21/21.
- Real Windows OCR detected `zh-Hans-CN`. A generated isolated image was
  uploaded with temporary OCR environment overrides, recognized as 111 valid
  characters, entered `review`, preserved marker `OCR-LOCAL-7419`, and remained
  `knowledge.state=not_submitted`.
- The isolated service group was restarted afterward without overrides;
  `/api/ocr/status` reported `enabled=false`, `mode=off`, and the settings API
  returned the expected OCR fields. No formal configuration or knowledge
  submission was performed.
- Current limitation: arbitrary scanned PDF pages are supported only when the
  existing parser can extract their embedded images; no separate PDF renderer is
  bundled.

## OCR provider routing (2026-10-29)

- Added `auto`, `windows`, `local_model`, `cloud`, and `none` OCR provider modes.
  Auto priority is: configured cloud API, configured private local model,
  Windows built-in OCR, then disabled.
- Explicit provider selection is strict and does not silently switch to another
  provider.
- Added a shared OCR HTTP contract for custom local and cloud endpoints:
  `{model, language, mime_type, image_base64}` with response text at `text`,
  `content`, or a nested `data/result/output.text`.
- Custom local model endpoints must be loopback, private LAN, or `.local`.
  Cloud mode requires endpoint, model, and API Token and is marked as external.
- Provider controls now enable/disable endpoint, model, Token, and connection
  testing based on the selected mode. Windows OCR remains the no-network default.
- Added 4 provider-routing tests. OCR now has 10 focused tests, and the combined
  focused suite passed 28/28.
- Isolated end-to-end acceptance used a local compatible OCR service on
  `127.0.0.1:19310`. `/api/ocr/test` succeeded, the request included the Token,
  and an image upload produced `review`, `ocr.provider=local_model`,
  `external=false`, 121 valid characters, and marker `OCR-MODEL-19310`.
- The review job remained `knowledge.state=not_submitted`. No cloud provider was
  contacted.
- The service group was restarted without overrides and the mock process stopped;
  port `19310` is no longer listening. Default OCR is off and provider defaults
  to auto.
- Current limitation: cloud OCR was contract-tested only, not against a real
  external OCR provider in this checkout.

## TTS integration (2026-10-29)

- Added `app.features.tts` with `/api/tts/status`, `/api/tts/test`, and
  `/api/tts`. Cloud synthesis proxies an OpenAI-compatible `/audio/speech`
  endpoint and keeps the API key server-side.
- Device/browser speech remains the default when cloud credentials are absent.
  A complete cloud host/model/API key with a cloud-enabled provider chain makes
  cloud TTS first while retaining device fallback in the existing player.
- Selecting `仅设备/浏览器语音` disables cloud even when credentials exist.
- Missing credentials return device-only status and do not make an upstream
  request.
- Cloud requests are limited to 1000 input characters and 20 MB of audio.
- The settings page now exposes the existing TTS fields, supports an explicit
  cloud connection test, and no longer treats localhost cloud endpoints as a
  disabled local speech pack.
- Added `tests/test_tts.py` covering device defaults, cloud-first selection,
  browser-only mode, URL normalization, request construction, and missing-cloud
  errors. All 6 TTS tests passed; the combined focused suite passed 34/34.
- Isolated end-to-end acceptance used an OpenAI-compatible local mock on
  `127.0.0.1:19320`. Status returned `ready=true`, active provider `cloud`, and
  chain `cloud,device`; the test and synthesis calls succeeded, requests included
  Bearer authentication, and `/api/tts` returned HTTP 200 `audio/mpeg` with 515
  bytes.
- The isolated service group was restarted without overrides and the mock was
  stopped. Default status is now `ready=false`, active provider `device`, chain
  `device`; port `19320` is no longer listening.
- Current limitation: cloud TTS was validated against the compatible contract,
  not a real external provider with production credentials.

## Rerank integration (2026-10-29)

- Replaced the ambiguous rerank binding control with explicit `off`, `local`, and
  `cloud` modes. Cloud mode exposes Cohere, Jina, and Aliyun DashScope providers.
- Local mode maps to LightRAG's native Cohere-compatible rerank adapter and
  requires an explicit model plus a loopback, private-LAN, or `.local` endpoint.
  Public endpoints are rejected in local mode.
- Cloud mode requires an API key. Provider-specific default rerank URLs are used
  when no endpoint is supplied.
- Added rerank timeout configuration and a fixed two-document connection test that
  validates returned indices and relevance scores.
- Added light-gray model guidance for `BAAI/bge-reranker-v2-m3`,
  `jina-reranker-v2-base-multilingual`, and `gte-rerank-v2`.
- No quality baseline, model comparison, or local/cloud result evaluation was
  performed, as requested.
- Added `tests/test_rerank.py` covering off/local/cloud mapping, private endpoint
  validation, and public endpoint rejection. All 3 rerank tests passed; the
  combined focused suite passed 24/24.
- Isolated integration used a local Cohere-compatible mock rerank service on
  `127.0.0.1:19300` and temporary environment overrides only. LightRAG health
  reported `enable_rerank=true`, binding `cohere`, and the mock model.
- One existing knowledge query completed successfully. The mock received a
  `/v2/rerank` request with 2 documents, engine logs reported
  `Successfully reranked: 2 chunks from 2 original chunks`, and the answer still
  contained `ORBIT-LANTERN-3B-7419`.
- The isolated service group was restarted without overrides and the mock process
  was stopped; port `19300` is no longer listening. No formal configuration was
  written.

## Desktop launcher (2026-10-29)

- Replaced `RAG启动器.ps1` with a Windows 5.1-compatible desktop launcher that
  delegates startup to the existing lifecycle with `--reuse-running`.
- Added a mutex so concurrent launches do not start duplicate service groups.
- Startup repair is bounded to one dependency/setup attempt and one lifecycle
  retry. Only verified project-owned listeners are stopped; unrelated port
  owners are never terminated.
- The launcher never deletes knowledge, uploads, memory, indexes, or
  configuration, and it never downloads models. Missing Ollama models are
  reported with a manual `ollama pull` suggestion.
- Added `scripts/install_desktop_shortcut.ps1`. It writes only
  `RAG 本地知识库启动.lnk` on the current user's desktop and does not create an
  automatic startup item.
- The actual shortcut was written and verified at
  `<user-profile>\Desktop\RAG 本地知识库启动.lnk`, targeting Windows
  PowerShell with the project launcher and working directory.
- Launcher self-test passed dependency imports, lifecycle script discovery,
  setup script discovery, and lifecycle status invocation.
- Real desktop-shortcut reuse, cold start, duplicate launch, and window-close
  behavior were later verified in the desktop-launcher closure acceptance
  below. Deliberately broken dependencies, stale-port cleanup, and model-missing
  recovery remain separate bounded acceptance cases.

## Knowledge graph integration (2026-10-29)

- Added a read-only graph bridge through the existing architecture:
  `features/knowledge -> LightRagHttpClient -> lightrag_shell -> LightRAG`.
- Exposed only `GET /api/graph/global`, `/subgraph`, `/popular`, and `/search`.
  LightRAG entity/relation create, edit, merge, and delete routes remain
  unreachable from the application and Shell.
- Mapped the frontend's `depth` and `max` values to the official
  `max_depth` and `max_nodes` parameters. Global graphs default to 400 nodes;
  subgraphs are limited to depth 3 and 200 nodes.
- Normalized the official label endpoints from bare arrays to `{items: [...]}`.
- The existing graph UI now keeps edge type, description, keywords, weight, and
  source files. Users can click an edge or a related entity to inspect the
  relationship, click the type legend to filter visible node types, and refresh
  the current graph without leaving the view.
- Frontend errors now preserve the backend message instead of showing only an
  HTTP status, and subgraph requests use `max_nodes` consistently.
- Added eight focused graph contract tests. Adapter mapping, Shell parameter
  normalization, app error mapping, label trimming, and the read-only route
  allowlist all passed. `node --check` and targeted Python compilation also
  passed.
- An isolated restart from `.integration-run/20261028-234918-full` first failed
  because `orjson` reported an access-denied DLL load. Reinstalling only
  `orjson==3.13.0` with `uv --link-mode=copy` repaired that import.
- The next isolated restart reached a second engine import failure:
  `PermissionError` reading `numpy/__init__.py` in the engine virtual
  environment. This indicates a broader ACL problem in that environment, not a
  graph-code defect. The lifecycle reported the service group stopped.
- Port `19180` briefly returned an old HTTP 404 from an unrecorded listener.
  A later targeted check found ports `19180`, `19161`, and `19162` no longer
  listening; no process was terminated.
- Not yet verified in this wave: graph queries against real indexed data and
  browser-level interaction. This remains the release acceptance step after the
  engine virtual-environment permissions are repaired.

## Engine environment recovery and graph acceptance (2026-10-29)

- Rebuilt the isolated LightRAG engine virtual environment with Python 3.14.7.
  The previous environment had mixed Python 3.12 metadata with a 3.14
  interpreter and access-denied package files.
- Installed all 138 packages from `requirements.lock` with uv copy mode. The
  previous environment is retained at
  `services/lightrag_engine/.venv.pre-rebuild-20261029` for rollback.
- Verified `numpy`, `orjson`, FastAPI, LightRAG, uvicorn, and the
  `lightrag-server` entry point from the rebuilt environment.
- The isolated service group started successfully on fallback ports
  `19163` (engine), `19164` (shell), and `19181` (app).
- `/web` returned HTTP 200. A bounded read-only graph check returned 20 global
  nodes and 7 edges with `is_truncated=true`, five popular entities, and a
  one-hop subgraph with five nodes and four edges.
- Browser acceptance confirmed the graph view, refresh control, type legend,
  entity subgraph, source document entry, and relationship detail panel. The
  relationship panel showed type, weight, source document, and both endpoints.
- No graph mutation endpoint was called. No ingestion, configuration save,
  deletion, or formal data-directory write was performed.

## Desktop launcher closure acceptance (2026-10-29)

- Fixed the launcher's lifecycle return-value capture. Command output from the
  lifecycle was previously returned together with the exit code, so a healthy
  `already running` result was misclassified as startup failure and left the
  launcher window waiting.
- Removed the pause from the duplicate-launch mutex path. A second shortcut
  invocation now reports the existing launcher and exits without opening a
  waiting window.
- With all services running, the desktop shortcut exited automatically and left
  the engine, shell, and app PIDs unchanged.
- A cold start through the desktop shortcut reached all three healthy services
  in 15.2 seconds; the launcher exited after 15.7 seconds.
- A second shortcut launch exited automatically, reused the same three PIDs, and
  services remained healthy after the launcher window closed.
- No launcher windows remained after acceptance. The final default service group
  is running on `10262`, `10261`, and `18080`.
- The forced-failure path and unrelated-listener preservation remain
  code-reviewed but were not runtime-simulated to avoid disrupting the running
  service group.

## Dependency health summary and default deployment (2026-10-29)

- Extended `/health` without breaking the existing `status` and `version`
  fields. It now reports LightRAG reachability, Ollama model availability, and
  the active vector-storage state without returning service URLs or secrets.
- The chat sidebar now distinguishes `ready`, `unavailable`, and
  `not_in_use`. A local vector store is shown as `未启用` rather than a false
  connection failure.
- Added four offline health-summary tests. The combined health and graph suite
  passed 13/13; JavaScript syntax and Python compilation also passed.
- Stopped the isolated group, then cold-started the default service group on
  `10262` (engine), `10261` (shell), and `18080` (app).
- Default `/health` reported LightRAG `ready`, Ollama `ready`, and Qdrant
  `not_in_use` with `NanoVectorDBStorage`. `/web` returned HTTP 200.
- A bounded read-only default check returned 5 global nodes and 4 edges without
  truncation, five popular entities, and a one-hop subgraph with four nodes and
  three edges.
- Browser acceptance confirmed the default page and corrected dependency
  status labels. No ingestion, configuration save, deletion, or graph mutation
  was performed.
- The previous engine environment remains retained at
  `services/lightrag_engine/.venv.pre-rebuild-20261029` pending a later cleanup
  approval.

## LAN access boundary and release gate (2026-10-29)

- LAN boundary acceptance ran against an isolated app bound to
  `0.0.0.0:19180`; engine and shell remained on loopback ports.
- An unpaired client connecting through the host LAN adapter was redirected
  from `/web` with HTTP 307 and received HTTP 401 from
  `/api/graph/global`.
- `/health` remained public with HTTP 200 and contained no API key, token,
  secret, authorization header, or service URL.
- The isolated client created a pairing request, the local computer approved
  it, and the claim returned HTTP 200 with an HttpOnly cookie.
- After authorization, `/web` and the graph API returned HTTP 200. The access
  status reported `local=false` and `authorized=true`.
- Revoking the device restored HTTP 307 for `/web` and HTTP 401 for the graph
  API. The isolated test device was removed, and no LAN-gate test devices
  remain in the formal application data.
- The isolated group was stopped and the default group restored on
  `10262`/`10261`/`18080`.
- Final technical release gate passed: 50 tests, JavaScript syntax checks,
  default health, `/web` HTTP 200, graph global/popular/subgraph reads, and all
  three services healthy with no duplicates.
- Final release sign-off remains pending manual acceptance and commit of the
  current uncommitted UI changes (`static/index.*`, `files.html`, `memory.html`,
  `settings.html`, `theme.css`). No backend or data issue remains from this gate.

## File deletion and skipped files (2026-10-29)

- The files page now has one `删除` entry. Normal browsing hides selection
  checkboxes; clicking `删除` enters selection mode and selecting items enables
  `删除所选 (N)`.
- The delete interaction is a single dialog. It shows the selected count, offers
  `同时删除知识库上传副本` only when a selected item belongs to the knowledge
  base, and requires typing `Yes` before the delete button becomes active.
- `/api/files/delete` now uses `confirmation` and
  `delete_knowledge_upload_copy`. The server validates `confirmation` as `yes`
  (case-insensitive) and rejects missing items or malformed ids before starting
  a background job.
- A selected app file removes its app archive and parsed job directory. A
  selected knowledge document reuses LightRAG document deletion; the optional
  checkbox maps to LightRAG `delete_file=True`, otherwise `delete_file=False`.
  The user's original file and containing folder are never deletion targets.
- The lower file-management section is now `跳过的文件`. It shows only jobs whose
  state is `skipped`, no longer displays processing-status or file-source
  columns, and provides a compact green `继续入库` action inside a vertically
  scrollable area capped at 270px desktop and 220px mobile.
- The overview card is now a compact status strip. Knowledge documents remain
  the primary page content; the delete entry stays neutral and compact until
  delete mode is activated.
- `继续入库` calls the targeted `/api/ingest/action/{file_id}` action `resume`.
  It reuses retained parsed content and the existing knowledge submission path,
  records a `resume` timeline entry, rejects non-skipped jobs, and rejects empty
  parsed content.
- Focused validation passed: 14 deletion tests, 4 skipped-resume tests, and 6
  files-page contract tests, plus JavaScript syntax, Python compilation,
  HTML id-reference checks, and `git diff --check`.
- Browser visual verification and live deletion were not run. A process started
  before these changes must be restarted to load the new API and page behavior.
  `继续入库` submits retained content directly and does not rerun parsing or the
  quality gate.
