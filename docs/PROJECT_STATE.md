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
- The actual shortcut was written and verified on the current user's desktop,
  targeting Windows PowerShell with the project launcher and working directory.
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

## Upload and ingest chain stabilization (2026-10-29)

- Checkpoint commit `df491b3` committed the earlier deletion and skipped-file UI
  work before this wave.
- `保存并入库` now honors the `ingest` flag. Editing content reruns the quality
  gate, and a passing review is submitted through the existing knowledge gateway
  in the same request. Submission failures return `saved=true` with a visible
  `ingest_error` instead of silently returning an unchanged review card.
- The ingestion status loop now queries `/ingest/knowledge-status/{file_id}`
  while a job is `ingesting`. The card moves from `ingesting` to `done` or back
  to `review` with the knowledge error, instead of staying at `ingesting` for ten
  minutes. Manual `刷新状态` uses the same knowledge-status refresh.
- `/ingest/pending` now separates `pending` (review/needs_ocr),
  `actionable` (blocked/failed), recent `skipped`, and `active`
  (processing/parsing/checking/ocr/ingesting) jobs. Page restore recreates the
  real state for all four groups; skipped jobs from the last 24 hours are
  restored in chat, while
  older skipped jobs remain available on the files page.
- Skipped jobs now expose `仍要入库` in chat as well as `继续入库` on the files
  page. Editing is allowed for skipped jobs; editing returns the job to the
  normal quality gate. Manual `force` submission is implemented for
  blocked/needs_ocr jobs and records a `force` timeline entry.
- Image jobs now return the edit contract the page expects (`is_image`,
  `doc_name_stem`, `doc_id`, `body_chars`, `caption_text`). Caption saves append
  a marked block to OCR text and replace the previous caption on repeat saves;
  pure-image captions remain the whole body. Image submissions use
  `<name>_<short-id>.md` to avoid filename collisions.
- Edits keep one `content.before-edit.md` backup and expose the edit count.
  Duplicate `ingest`/`resume` actions are idempotent while submission is active.
- The session `本次运行内全部允许` switch now actually releases existing review
  jobs and auto-submits new chat uploads for that process. Inbox scanning passes
  `allow_session_auto=False` and still never submits automatically.
- The files page local table now shows all non-terminal jobs, not only skipped
  ones: state label, failure reason or knowledge error, `去处理` for actionable
  jobs, `入库中…` for active jobs, and `继续入库` for skipped jobs. The page
  refreshes ingesting rows from the knowledge service for a bounded period.
- The companion task detail now includes the knowledge or quality failure reason.
- Settings copy no longer claims an automatic 24-hour skip. The field is shown
  as a fixed skipped-restore window; `auto` and `off` confirmation modes are
  marked unimplemented. The unsupported-setting validation is unchanged.
- Stale local `processing/parsing/checking/ocr` jobs older than 120 minutes are
  marked failed on restore so a crashed parse can be retried. An `ingesting` job
  whose knowledge track returns no documents after 60 minutes is moved back to
  `review` with `track_not_found`, making it resubmittable instead of stuck.

### Verification

- Full suite: 85 tests passed in the project virtual environment.
- Isolated app-only HTTP smoke on `127.0.0.1:19480` with temporary data,
  upload, parsed, runtime, memory, knowledge, and log directories. Upload →
  review → save → save-and-ingest failure → skip → restore → resume failure →
  blocked → force audit all returned the expected contract. `/web`, `/files`,
  `/settings`, and `/memory` returned HTTP 200.
- The files list payload included `state`, `local_state_label`, `local_detail`,
  `knowledge_message`, and the knowledge failure reason for the failed job.
- Session allow-all released the existing review jobs through the knowledge
  gateway path and reported the released count.
- Success-path smoke used a local LightRAG-compatible fake shell on
  `127.0.0.1:19999`: save-and-ingest returned `ingested=true`, the job entered
  `ingesting`, the first knowledge-status refresh stayed `ingesting`, the second
  moved it to `done` with progress `1/1`, pending groups cleared, and the
  overview reported the job in the knowledge base.
- Both isolated processes were stopped; artifacts remain under
  `.integration-run/wave1-app` only.
- The default lifecycle group was restarted on the committed source. Its
  `/health` reported LightRAG, Ollama, and local vector storage ready; `/web`,
  `/files`, `/memory`, and `/settings` returned HTTP 200.
- Three previously stuck `ingesting` jobs in the formal data directory were
  status-refreshed from the live engine: two had failed chunks and moved to
  `review` with `APIConnectionError` reasons; one track returned no documents
  and moved to `review` with `track_not_found`. No file, content, or knowledge
  record was deleted. The formal overview then reported `ingesting: 0` and
  `waiting_review: 4`.
- Headless Chrome rendered the live `/web` page from the committed source:
  review cards, restored skipped cards, `仍要入库`, `去确认`, and the companion
  action were present in the DOM. It also rendered `/files` with eight local
  rows, state labels, `继续入库`/`去处理` actions, and the surfaced
  `APIConnectionError` and `track_not_found` reasons. No upload or action was
  clicked during the browser render.

### Remaining risks

- The success smoke used a compatible fake shell, not the real LightRAG engine.
  Real engine indexing, chat retrieval, and the three-service cold-start gate
  remain release-gate checks.
- Local parsing and OCR still run inside the `/upload` request. Large files keep
  one long request; the page switches to an explicit "server is parsing" state
  after 1.5 seconds, and a refresh restores the active job, but full async upload
  is deferred.
- Knowledge-status polling runs every 6 seconds while a job is ingesting. If the
  engine remains unavailable, the card keeps retrying until the ten-minute poll
  cap and then shows an unknown state; the manual refresh still works.
- `auto` and `off` ingest confirmation modes remain unimplemented and are now
  marked as such in the settings UI.
- Headless DOM rendering passed for the new card states, the files-page local
  table, and the companion error detail. Delete-mode interaction passed through
  the Chrome DevTools Protocol. Click-through for upload, save-and-ingest, and
  resume, plus visual layout checks at desktop/mobile breakpoints, were not run
  in a real browser session.
- The default lifecycle group was restarted with the committed source and
  checked healthy. Any further source change still requires another restart
  before manual acceptance.

## Delete mode follow-up (2026-10-29)

- In delete mode the page subbar now sticks below the 56px header. The
  `删除所选（N）` and `取消删除` actions remain reachable while selecting rows
  on lower pages; the selected count is kept across pagination because selection
  is keyed by document/file id.
- Delete mode replaces the subbar description with a short selection hint and
  keeps the original-file boundary visible; mobile hides the longer description
  to keep the sticky bar compact.
- Pressing Enter in the `Yes` confirmation input now submits when the button is
  enabled. IME composition is ignored, and Enter on other dialog controls keeps
  its normal button behavior.
- The delete dialog now repeats the boundary: only app records, parsed data, and
  the optional knowledge upload copy are removed; the original file and its
  folder are never touched.
- Isolated browser interaction via Chrome DevTools Protocol: entered delete mode,
  confirmed sticky positioning (`top: 56px` after scrolling 762px), selected a
  local row, confirmed the button counted one selection, opened the dialog,
  typed `yes`, and saw Enter invoke the delete handler exactly once through a
  test stub. No real deletion or formal data write was performed.

## Batch upload aggregation (2026-10-29)

- Uploads carry an optional `batch_id`. The server validates it or generates one,
  stores it in `result.json`, and returns it from `/upload`. Existing jobs without
  a batch id remain valid and are treated as legacy single uploads.
- Added read-only `GET /ingest/batch/{batch_id}` with per-state counts, grouped
  counts, capped item details, failure reasons, progress, and a `complete` flag.
  It refreshes a limited number of ingesting tracks through the knowledge
  service so a batch can reach `done` without one status request per file.
- Chat groups more than five unified-file uploads into one batch card. The card
  shows aggregate state counts, a progress bar, a file-management deep link, and
  expandable per-file rows. Rows open the existing single-file card on demand,
  so confirm/edit/skip/retry behavior is unchanged.
- One batch poller replaces per-file polling for grouped uploads. The companion
  now shows an aggregate queue state such as
  `资料队列需要处理 · 共 30 份 · 待处理 30`, not one animated task per file.
- Page restore groups pending/actionable/skipped/active jobs by `batch_id`.
  Groups larger than five are restored as a batch card; smaller groups keep the
  existing individual cards.
- The files page exposes a batch filter (`/files?batch=<id>`), shows only that
  batch when selected, and adds `重试失败（N）` for failed/blocked rows in the
  current filter. Retry runs sequentially through the existing per-file action
  and is capped at 20 items per click.
- Small batches (five or fewer) and the image/scan upload entry keep the
  previous per-file card flow. This avoids changing the OCR convenience path.

### Verification

- Full suite: 96 tests passed.
- Isolated HTTP smoke created two six-file batches with distinct batch ids:
  the normal batch reported `review: 6`, the low-quality batch reported
  `actionable: 6`; `/ingest/pending` returned the same six items per batch; an
  invalid batch id returned HTTP 400.
- Headless Chrome restored the batches as four batch cards (including the two
  from the first smoke attempt), each with six rows and correct
  `待确认 6` / `需修复 6` summaries. The companion rendered
  `资料队列需要处理 · 共 24 份 · 待处理 24`.
- Browser CDP uploaded six files through the real file input, observed the new
  batch card, six rows, server-side `review: 6`, automatic expansion, and the
  companion aggregate `共 30 份 · 待处理 30`. One row expanded into the normal
  per-file card with `待确认入库`.
- Batch knowledge refresh smoke: six reviewed files were submitted; the first
  batch status read reported `active: 1, done: 5`; after the refresh interval it
  reported `active: 0, done: 6, complete: true`.
- Files page filtered to the failed batch showed six rows, the
  `当前只显示该批次` hint, and `重试失败（6）`; the review batch showed six rows
  and the retry button hidden.
- All isolated app, fake shell, and Chrome processes were stopped; no formal
  data was written.
- The default lifecycle group was restarted with the batch routes. `/health` was
  healthy, `/web`, `/files`, `/memory`, and `/settings` returned HTTP 200, an
  unknown valid batch id returned an empty aggregate, and the chat/files assets
  were served with the new `20261029-batch` cache version.

### Remaining risks

- The browser upload loop is still client-side and sequential. Wave B makes
  accepted jobs durable, but files that have not yet been sent to `/upload`
  before a refresh or page close are still lost. A resumable client upload
  session would be a further step.
- Wave B moved parsing out of the upload request into a bounded background
  worker pool; see the durable queue section below.
- Batch grouping applies to unified uploads only. Image/scan uploads keep
  individual cards so the existing auto-OCR policy still runs.
- The batch poller stops when there are no active items and waits for user
  action, even if other pages change those items. Reloading restores the true
  state.
- The batch endpoint refreshes at most six ingesting tracks per poll, throttled
  to one refresh per track every six seconds.
- Files-page bulk retry is sequential and capped at 20 per click; the retry
  click itself was not exercised in the browser (the button and row filter were
  rendered and verified).
- The default lifecycle group was restarted for the new backend batch routes;
  any subsequent backend change still requires another restart.

## Durable ingestion queue (2026-10-29)

- `/upload` now returns HTTP 202 after validation, content hashing, archive, and
  a durable `queued` result write. Local parsing, quality, OCR, and optional
  knowledge submission run in a background worker.
- Added `app/features/ingest/worker.py` with a bounded worker pool (default 2,
  `RAGKB_INGEST_WORKERS`), retry limit (`RAGKB_INGEST_MAX_ATTEMPTS`, default 3),
  and exponential backoff (`RAGKB_INGEST_RETRY_BACKOFF_SECONDS`, default 5).
- On startup, jobs left in `processing/parsing/checking/ocr` are requeued.
  Queued jobs survive a process restart and are processed by the next worker.
  Unexpected pipeline exceptions requeue with backoff until the attempt limit,
  then become `failed` with the exception type in the detail.
- Uploads are content-hash deduplicated. A duplicate returns `duplicate=true`
  with the existing file id and state `duplicate`; no second archive or job is
  created. Failed/cancelled jobs do not block a re-upload.
- Added queue controls: `GET /ingest/queue/status`, `POST /ingest/queue`
  (`paused`), and the queued-job `cancel` action. Pausing is a runtime control
  (not persisted across restart); cancelling is allowed only while `queued`,
  preserves originals and archives, and ends in the terminal `cancelled` state.
- Retry now re-enqueues the job instead of parsing synchronously inside the
  action request. The existing `retry` action remains available for
  failed/blocked/needs_ocr jobs.
- Chat single-file cards now show `排队中`, `已取消`, and `已存在`, offer
  `取消排队`, and keep polling queued jobs. Batch cards count queued,
  cancelled, and duplicate rows; queued rows expose a `取消` action. The
  companion treats queued as busy.
- The files page shows `排队中` rows with a `取消排队` action, a batch filter,
  a queue status line, and a global pause/resume control. Bulk retry remains a
  sequential client loop capped at 20 items.
- Inbox scanning still passes `allow_session_auto=False`, so queued inbox jobs
  keep the no-auto-submit guarantee when they are later parsed.

### Verification

- Full suite: 103 tests passed, including worker enqueue, dedup, claim/process,
  backoff, max-attempt failure, restart recovery, pause, and status tests.
- Isolated HTTP smoke: `/upload` returned HTTP 202 `queued` in 0.461 seconds;
  the worker moved the job to `review`; pausing kept the next upload queued and
  the queue status reported `paused=true, queued=1`; cancel returned
  `cancelled`; duplicate content returned the same file id with
  `duplicate=true`; a queued job survived an app process restart and was
  processed to `review` on the next start with `attempts=1`.
- A 12-file batch smoke uploaded six normal and six low-quality files as
  `queued`; an immediate batch read showed `review: 5, queued: 1`; after the
  worker drained, the normal batch reported `review: 6` and the low-quality
  batch reported `actionable: 6`.
- Headless Chrome rendered the restored chat with five batch cards and the
  companion aggregate `资料队列需要处理 · 共 30 份 · 待处理 30`; the files page
  rendered six filtered rows, `当前只显示该批次`, and the queue status
  `队列：空闲`.
- All isolated app and fake-shell processes were stopped; no formal data was
  written.

### Remaining risks

- Client uploads are still sent one file at a time by the browser; accepted jobs
  are durable, but a page refresh before later files are sent still loses those
  files.
- Pause state is in memory and resets to running on restart. Cancellation is
  limited to queued jobs; a running parse is not interrupted.
- Content dedup only applies to new uploads that carry `content_hash`; legacy
  jobs from before this change are not indexed by hash.
- Batch duplicate rows are tracked by the browser (the server batch endpoint
  only knows queued jobs); a refreshed page does not restore duplicate rows.
- The worker parses at most two files concurrently by default. OCR-heavy
  batches can still take time; the queue UI now shows that state.
- The worker pool runs inside the application process. A multi-process
  deployment would need a shared queue; the lifecycle is single-process.
- Real LightRAG indexing and chat retrieval were not re-run in this wave; the
  existing fake-shell success path and the live default environment remain the
  surrounding evidence.
- The default lifecycle group must be restarted to load the worker and the new
  upload contract.

## Ingest status and timeline UX follow-up (2026-10-29)

- The timeline renderer now normalizes backend rows (`step/detail/state`) into
  clear labels and hints: upload, queue, worker start, local parse, quality,
  knowledge submission, and knowledge indexing. A terminal index step is added
  when indexing completes or fails. Unknown internal steps no longer render as
  the literal text `步骤`.
- `/ingest/status` now refreshes an ingesting knowledge track when due, using the
  same six-second throttle as the batch endpoint. Single-file cards progress to
  `已入库` without the user clicking `刷新状态`; the frontend still keeps an
  explicit refresh fallback.
- On success the card stays in the conversation as a compact confirmation. The
  timeline auto-collapses, a success note explains that the file is searchable,
  and the actions are `继续提问` and `继续上传`. The card does not disappear, so
  the user keeps a visible confirmation.
- On failure or a knowledge-index failure the card keeps the reason in the
  subtitle/timeline and shows a concise next-step note. Actions include retry,
  edit, skip/force where applicable, and `文件管理`.
- The poll cap is now roughly 30 minutes. On timeout the card keeps its last
  state and says to click `刷新状态`; it no longer silently changes to an
  unactionable unknown state.
- Fixed a Windows read/write race in `storage.write_result`/`read_result`:
  unique temp names, bounded retries for `PermissionError`, and read retries.
  This prevents a concurrent status poll from turning a successful parse into a
  spurious worker retry.
- Worker timeline rows use `后台开始处理`, `等待重试`, and `后台处理失败` where
  appropriate, so retries are distinguishable from a duplicate queue step.

### Verification

- Focused tests: 21 ingest worker/upload-chain tests passed, plus the existing
  batch/UI contract tests.
- Targeted isolated browser retest (app + compatible knowledge shell):
  - upload reached `待确认入库`;
  - clicking `入库` produced `✓ 已入库` **without clicking 刷新状态**;
  - success note, `继续提问` and `继续上传` actions were present; the timeline
    was collapsed by default;
  - expanding the timeline showed seven labeled rows:
    `上传文件 / 进入解析队列 / 后台开始处理 / 本地解析 / 质量校验 /
     提交知识库 / 知识库索引`, with hints and no `步骤` placeholder;
  - the failure card showed a quality-gate failure note and actions
    `跳过 / 仍要入库 / 重试 / 修改正文 / 文件管理`;
  - the final app log contained no `PermissionError` or `Ingestion job failed`.
- No full regression was rerun, by the requested test boundary.

### Remaining risks

- A genuine transient processing error still produces a `等待重试` row; that is
  intentional and now distinguishable.
- The success card remains in the conversation as a compact record; it is not
  auto-deleted. Users can start a new conversation from the sidebar.
- The status GET now performs a throttled knowledge query while ingesting; if
  the engine is unreachable, the poll may wait for the engine connect timeout.
- The default lifecycle group needs a restart to load the backend status and
  storage changes; static chat assets use the `20261029-status-ux` version.

## Reference opening and query-error guidance (2026-10-29)

- Local job summaries now retain the knowledge document name. The local
  reference resolver indexes that name, so a chat reference such as
  `document.md` resolves to the original/archive file even when the submitted
  knowledge name differs from the uploaded filename.
- Reference chips are keyboard-accessible; double-click or Enter opens the
  original through `/api/files/origin/{file_id}`. Missing originals show a
  localized reason instead of an English backend message.
- Image reference opening now uses the existing origin endpoint instead of the
  nonexistent `/api/files/raw` route.
- The LightRAG shell now preserves upstream query error status and message
  (`HTTP 422: ...`) instead of returning only a generic `KnowledgeHTTPError`.
  The chat service maps no-context/4xx/balance/5xx failures to actionable
  Chinese messages. The frontend adds `重试`, `继续对话（不查资料）`, and
  `上传资料` actions to error and no-context states.
- A model answer that clearly says no relevant material is treated as a
  no-context answer, so the same retry/upload/bypass guidance is shown even if
  the model cites an unrelated document.

### Verification

- Focused tests passed for document origin resolution and chat error mapping.
- Isolated image upload: PNG reached `需要 OCR`, accepted a written
  description, passed quality at 100, ingested to `已入库`, and produced
  `knowledge.document_name = image-e2e_4089.md`.
- `/api/files/resolve` resolved that knowledge document name to the original
  PNG (`available=true`, kind `source`); the origin URL returned HTTP 200
  `image/png`; a synthetic reference chip resolved and a simulated double-click
  opened the same origin URL.
- The isolated shell preserved a real upstream `HTTP 422` validation detail in
  its 502 response.
- The frontend error path was exercised with a failing knowledge endpoint: the
  answer showed the specific failure message plus `重试 / 继续对话（不查资料） /
  上传资料`.
- A real isolated-engine no-info question returned a clear refusal answer and
  the no-citation note. The final refusal-action condition was not re-run after
  the last one-line frontend change.

### Remaining risks

- The engine currently emits text references for this deployment. The frontend
  image gallery still points at `/api/images/asset`, which is not implemented;
  embedded-image reference previews would not load. Original image files still
  open through `/api/files/origin/{file_id}`.
- Knowledge-document matching now works for jobs submitted after this change;
  references to external documents not uploaded through this app remain
  unmatched and show a localized reason.
- The actual upstream error text is truncated to 300–800 characters for display;
  full details remain in the service logs.

## Balanced Local Memory default (2026-10-29)

- Default memory configuration is now enabled instead of off: local session
  storage, context injection, Tier 1 fast memory, recent history, cross-session
  history recall, rule-based background extraction, and the extraction worker.
- Code and settings-schema defaults:
  - recent history: 12 messages / 2,000 tokens
  - long-term memory: 1,200 tokens
  - history recall: 600 tokens / 6 items
  - upstream history and memory: on
  - extraction: every 6 turns, 20 messages / 4,000 tokens per batch,
    300-second normal debounce
  - extraction backend: local rules (`off` LLM backend), no cloud extraction
- The formal runtime configuration was updated through the local settings API,
  which created its normal configuration backup. The service was restarted.
- The settings page now describes Balanced Local Memory and marks the empty
  option as “跟随默认：开启”; budget placeholders match the new defaults.
- Current memory status after restart: DB ready, context injection enabled,
  fast memory enabled, upstream history on, auto extraction on, worker running,
  6 sessions / 25 messages, 0 long-term memories and 0 extraction jobs. The zero
  memory count is expected until new conversations trigger extraction.
- No formal chat message or memory content was created during this change.

### Verification

- Added four M1 tests covering code defaults, settings-schema defaults, context
  assembly with temporary SQLite data, and upstream option injection. All four
  passed, along with the three existing history-search tests.
- Runtime after restart: `/api/memory/status` reports `ready=true`,
  `context_enabled=true`, `fast_memory=true`, `upstream_history=on`,
  `auto_extract=true`, `worker_running=true`; `/web`, `/memory`, and `/settings`
  return HTTP 200.
- No full regression or real multi-turn memory chat was run in this wave.

### Remaining risks

- Rule-based extraction is conservative; it will not produce web-model-style
  semantic summaries. LLM extraction remains an opt-in backend outside the
  current settings page.
- The new default injects up to roughly 3,800 tokens (2,000 recent history +
  1,200 memory + 600 recall). This is still far below a 128k–1M-token web
  context window; it targets selective personal continuity, not raw capacity.
- Memory count stays 0 until new conversations trigger extraction. A real
  three-turn pronoun test, cross-session recall test, and memory edit/delete
  test are still required before describing the system as web-grade.
- The external reference application remains read-only and was not used as a
  data/config source; no private reference data was read.

## M1 real memory acceptance (2026-10-29)

- Isolated three-service environment with a fresh temporary memory DB
  (`m1-accept-20261029.db`), Balanced Local Memory settings, and no formal data.
- Same-session three-turn test:
  - turn 1 stored the marker `ORBIT-LANTERN-7421` and the preference
    “先给结论”;
  - turn 2 answered the marker correctly;
  - turn 3 answered the preference correctly;
  - context status was `ok` for all three turns.
- After session A: 3 local memories (1 conflicted/pending), 1 session,
  6 messages; extraction jobs were queued/done.
- Cross-session test used a new session and asked for the project marker:
  - context assembly injected the correct active memory
    (`mem_694f...`, reason `relevant`) and 2–3 history-recalled messages
    (291–331 tokens total);
  - the model answered `星辰项目` / `SkyProject` and did **not** contain
    `ORBIT-LANTERN-7421`.
  - the memory prompt was strengthened to tell the model to answer personal
    facts directly from memory and not invent project names; a retest still
    produced `SkyProject`.
- Verdict: persistence, fast memory, extraction, retrieval, and context
  assembly work; cross-session product behavior fails on the installed
  `qwen2.5:3b` model because it ignores the injected memory. Only
  `qwen2.5:3b` is installed locally; no larger chat model is available.
- Product-grade cross-session memory requires a larger/stronger generation
  model, an upstream model with valid credentials, or a deterministic
  memory-answer path for direct personal-fact questions.
- Isolated services were stopped; formal memory status remains enabled with
  0 memories and 6 sessions / 25 messages; no formal test chat was written.

## Direct personal-fact memory answers (2026-10-29)

- Added a deterministic chat path for direct personal-fact questions. When a
  question explicitly asks about the user's own remembered information and a
  high-confidence (`>= 0.8`) active, normal-sensitivity memory is retrieved with
  reason `relevant`, the answer is built directly from up to three memory
  contents. The generation model is not called.
- Knowledge-base/document lookups are excluded from this path, as are ordinary
  questions and memories with lower confidence, non-active status, sensitive
  classification, or no relevant match. Those continue through the normal model.
- Non-stream `/chat` returns `memory_direct=true` and `memory_ids`; streamed
  chat emits a `memory` event before the answer chunk. The frontend adds the
  note “本轮回答直接来自本地长期记忆，未调用生成模型。”
- This closes the observed cross-session failure where retrieval and context
  injection were correct but `qwen2.5:3b` answered `SkyProject` instead of the
  stored marker.

### Verification

- Unit tests: 5 memory tests passed, including direct-fact triggering and
  knowledge-base lookup exclusion.
- Isolated real-engine cross-session retest: the new-session question
  “我的项目代号是什么？只回答代号。” returned
  `我的项目代号是 ORBIT-LANTERN-7421` in 0.150 seconds with
  `memory_direct=true` and memory id `mem_694f...`.
- Browser test showed the answer plus
  “本轮回答直接来自本地长期记忆，未调用生成模型。”
- Isolated services and Chrome were stopped; formal data was not written.

### Remaining risks

- Direct answers require a clear personal-fact question pattern and a
  high-confidence relevant memory. No/pending/conflicted memory still falls
  back to the model and can hallucinate.
- The path returns stored content rather than generating a polished sentence;
  up to three memories are joined with separators.
- The formal runtime has 0 long-term memories until new conversations trigger
  extraction; existing 6 sessions / 25 messages were not backfilled.
- Full web-grade memory still needs larger-model evaluation for non-direct
  questions, plus memory conflict/decay and broader acceptance tests.

## One-click deploy and start (R1, 2026-10-30)

- Added `deploy.cmd` and `scripts/deploy.ps1`: project-local dependency
  preparation, local Ollama detection/configuration, doctor verification,
  project-local launcher shortcut, and optional service start.
- `start.cmd` now auto-runs deployment preparation once when the app or engine
  virtual environment is missing, instead of stopping with a setup error.
- `scripts/setup.ps1` is now project-local and boundary-safe:
  - uv downloads go to `.tools/downloads`, uv/Python/tool caches to `.tools/`
  - no winget/system install and no `%TEMP%` download
  - `-CheckOnly` performs a no-write environment check
  - `-ConfigureLocal` fills local Ollama host/model/embedding values only when
    the engine config is new or still a placeholder
- `.env.example` now reflects Balanced Local Memory; the engine example no
  longer contains absolute project paths.
- `scripts/doctor.ps1` now checks Python version, engine/model configuration,
  Ollama reachability, embedding/chat models, data directories, lifecycle state,
  ports, and disk space. It fixed the lifecycle state shape (`services` map).
- `scripts/install_desktop_shortcut.ps1` defaults to a project-local shortcut;
  writing to the desktop is an explicit `-Desktop` opt-in.
- `.tools/`, `.updates/`, and project-local `.lnk` files are ignored by Git.
- R1 did not implement upgrades; R2 below adds the user-confirmed updater.

### Verification

- PowerShell parser checks passed for setup, deploy, doctor, and shortcut
  scripts.
- `setup.ps1 -CheckOnly -ConfigureLocal` on the real project reported uv,
  app/engine imports, existing env files, Ollama models, and the proposed local
  model pair without writing files.
- `deploy.ps1 -CheckOnly` ran setup check + doctor on the real project; doctor
  reported all checks passed after the lifecycle-state fix.
- A fresh temporary root under `.integration-run/r1-check` reported missing
  venvs and env files as expected, proposed local Ollama configuration, and
  created no `.tools` or `.env` files.
- No services were started or data changed during these checks.
- Full fresh-machine installation, actual model download, and R2 upgrade were
  not run.

### Remaining risks

- A missing Ollama/model stops deployment with instructions; it is not
  auto-downloaded.
- Network failures during uv/Python provisioning are reported but not retried
  beyond the existing download/uv behavior.
- At R1 time the updater and rollback flow were not implemented; see R2 below.
- The project-local launcher shortcut is not placed on the desktop unless the
  user explicitly opts in.

## Natural upgrade (R2, 2026-10-30)

- Added `update.cmd` and `scripts/update.ps1` with three user-confirmed modes:
  Git fast-forward, GitHub Releases asset download, and offline local archive.
- Before changing code, the updater stops lifecycle-owned services and backs up
  `data/memory`, `data/index-compatibility.json`, `.env`, `config/app.env`,
  `config/engine.env`, and `services/lightrag_engine/.env` under
  `.updates/backups/<timestamp>/runtime`. Release/archive mode also backs up
  the code entries for rollback. Git mode rolls back with `git reset --hard`.
- Code replacement is limited to declared code entries. It never replaces
  `data/`, `knowledge/`, `logs/`, `config/`, virtual environments, `.tools/`, or
  model storage.
- The updater verifies local/archive SHA256, rejects unsafe archive paths before
  extraction, refuses dirty tracked Git worktrees, and requires an explicit
  `UPGRADE` confirmation unless `-Yes` is supplied.
- Added `scripts/package_release.ps1`: builds a code-only Windows ZIP and SHA256
  under `.updates/release/`, excluding runtime data, user configuration, caches,
  `__pycache__`, virtual environments, and integration-test roots.
- Added `.github/workflows/release.yml`: a matching `v*` tag builds the ZIP and
  publishes it with the checksum; the workflow rejects a tag/version mismatch.
- Added `config/update.example.json` and documented Git, Release, and offline
  upgrade paths in `README.md` and `部署阶段.md`.
- `scripts/setup.ps1` now runs `uv` through `Start-Process` with redirected
  stdout/stderr. This prevents successful `uv` progress from being rendered as
  a terminating PowerShell 5.1 `NativeCommandError` during upgrade dependency
  sync, while still using the real process exit code for failure handling.

### Verification

- PowerShell parser checks passed for `update.ps1` and `package_release.ps1`;
  both retain UTF-8 BOM for Windows PowerShell 5.1.
- `package_release.ps1` produced a 179-entry ZIP and checksum with no
  `data/`, `knowledge/`, `logs/`, `.env`, user `config/`, `.venv/`, `.tools/`,
  `.updates/`, `.integration-run/`, or `__pycache__` entries; `Expand-Archive`
  recovered `app/main.py` and `scripts/update.ps1`, and applying the ZIP to a
  fake root installed the root launcher and deployment document.
- Local archive apply in an isolated fake root changed code from 0.1.0 to
  0.2.0, preserved the SQLite DB and `.env`/config files, removed stale code,
  and created a code backup.
- A wrong SHA256 exited 1 before any backup or code change. `-CheckOnly`
  against the built package exited 0 without creating `.updates` or changing
  code/data. A failed dependency sync after apply rolled back code and runtime
  data to 0.1.0 with data intact.
- Git mode against a local bare remote fast-forwarded 0.1.0 to 0.2.0, preserved
  untracked data/config, and created a runtime backup. A dirty tracked worktree
  was refused before fetching/applying.
- Self-update replacing the running `update.cmd` and `scripts/update.ps1`
  completed successfully in a fake root.
- An archive containing `../evil.txt` was rejected before backup/apply; no file
  was written outside the extract directory.
- All upgrade tests used `.integration-run/r2-*`; formal data and the external
  reference application were not touched.

### Isolated upgrade/rollback drill (2026-10-30)

- Created `.integration-run/r2-drill/base` from the code-only 0.1.0 ZIP with
  copied app/engine virtual environments, independent data/logs, ports
  19080/19261/19262, local Ollama models, and no formal data.
- Started the real engine, shell, and app; all three were healthy, while the
  formal 0.1.0 services stayed healthy and untouched.
- Full archive upgrade to 0.1.1 without `-SkipSetup` or `-NoRestart`: stopped
  the drill services, backed up runtime/code, replaced code, ran `uv sync` and
  the engine dependency check, restarted all three services, and passed
  `/health`. Version, code marker, data, config, and isolated `.env` were as
  expected.
- Injected a 0.1.2 archive whose `start.ps1` exits 1. The updater applied code
  and synced dependencies, startup failed, and rollback restored the 0.1.1
  code and runtime backup, then restarted all three services healthy. The bad
  marker was gone and SQLite `PRAGMA integrity_check` returned `ok`.
- Re-ran a full 0.1.3 upgrade containing the `uv` stream fix; dependency sync
  produced no `NativeCommandError`, and all services passed health checks.
- Stopped the drill services afterward; isolated ports are free and the formal
  engine/shell/app remain running healthy at 0.1.0. Drill logs and backups remain
  under `.integration-run/r2-drill` (ignored by Git).

### Remaining risks

- A real GitHub Release download, GitHub Actions run, and upgrade of the running
  formal three-service installation have not been executed. The isolated drill
  covered archive apply, setup, service restart, health, and rollback; live
  Release/GitHub paths still need a release-gate run.
- The drill used copied virtual environments and local Ollama; it validated
  health and upgrade mechanics but did not send a generation/chat request.
- There is no database or index migration framework. A future release that
  changes persisted schemas needs an explicit migration step before code-only
  replacement is safe.
- Rollback restores code and runtime data, but if dependency sync or service
  startup also fails, the installation may still be down; the updater reports
  this separately.
- GitHub API checks are unauthenticated and can hit rate limits; offline archive
  updates require the user to supply a SHA256.
- Updates are Windows-only, without delta packages, code signing, or
  stable/beta channels. Backups under `.updates/backups` currently have no
  automatic retention policy.
- The updater replaces a declared code-entry list. New top-level code paths
  must be added to that list in a release that is applied before the path is
  required; code added under existing entries (`app/`, `static/`, `scripts/`,
  etc.) is handled automatically.

## Open-source release audit and Wave A (2026-10-30)

Read-only audit findings that drove this wave:

- The current tree and Git history contained no common API-key, token, GitHub
  PAT, Slack-token, or private-key patterns. `.env`, databases, uploads, logs,
  virtual environments, and settings backups are not tracked.
- Local user paths and a reference-application name were present in the internal
  development log and in history. The development log is also shipped inside the
  release ZIP.
- The README said memory was disabled by default, while the runtime, examples,
  and tests enable Balanced Local Memory.
- LAN pairing existed, but chat/document/memory/settings were loopback-only
  while some ingest/TTS routes were reachable by paired devices. The middleware,
  UI copy, and security policy did not describe the same boundary.
- There was no push/PR CI workflow and no test gate before the tag release.
- Project metadata lacked authors, a license file reference, and Windows/version
  classifiers.

Wave A changes:

- Rewrote all 41 commits to remove local user paths, the reference-application
  name, and the personal author email. Commit author and committer email are now
  `scholar@users.noreply.github.com`. A full pre-rewrite bundle remains under
  `.updates/` (ignored by Git); the old refs and reflog were removed.
- Sanitized the current development log. It no longer contains local paths or
  private reference names.
- Corrected the README to describe Balanced Local Memory as enabled by default,
  including budgets and the local-rule extractor.
- Documented the current LAN boundary honestly and recorded the release-gate
  gaps: consistent authorization, CSRF/origin checks, per-device upload limits,
  and automated access-boundary tests. Mobile chat/upload remains a product
  target; it is not canceled or claimed as complete.
- Added project authors, license-file metadata, Beta status, and Windows/Python
  classifiers to `pyproject.toml`.
- Added `.gitattributes`, `.editorconfig`, `CODE_OF_CONDUCT.md`, issue templates,
  and a pull-request template. Added the code of conduct to the updater and
  release-package code-entry lists.

Wave A verification:

- Full suite: 115 tests passed.
- All static JavaScript syntax checks passed.
- All PowerShell scripts parsed; modified scripts retain their UTF-8 BOM.
- TOML and GitHub issue/workflow YAML parsed successfully.
- Release package built with 184 entries; it contains the code of conduct and
  GitHub templates, and no `data/`, `.env`, or virtual environment.
- Public-tree and package scans found no local user paths, reference-app name,
  personal email, or placeholder path markers.

Remaining risks / next wave:

- LAN access still needs the Wave B authorization matrix, CSRF/origin
  protection, per-device upload limits, and tests. Do not expose the current LAN
  surface to an untrusted network.
- CI and a tag-release test gate are not implemented yet.
- No GitHub remote or repository URL is configured, so release-workflow and
  GitHub metadata verification still require the actual repository.
- The `pyproject.toml` wheel still packages only `app`; GitHub ZIP is the
  supported distribution unless static assets are added to the wheel.

## LAN authorization, CSRF, and upload quotas (B1, 2026-10-30)

Implemented the approved trusted-LAN policy without removing mobile core use:

- Paired LAN devices may use chat/session APIs, upload and ingest, TTS
  synthesis, and read-only document/topic/graph/memory/settings views.
- Host-only operations remain settings writes/tests, document/topic changes,
  deletion, memory mutations, ingest queue/session controls, device management,
  index acceptance, and service stop/control.
- Added shared `require_local` and `require_authorized` FastAPI dependencies and
  applied them across chat, documents, memory, ingest, knowledge, settings, and
  TTS routers.
- Added a double-submit CSRF layer: an authorized LAN browser receives a
  readable `ragkb_csrf` cookie; the page-level fetch wrapper sends
  `X-RAGKB-CSRF`; unsafe LAN requests must match both. Loopback requests and
  safe methods are exempt.
- Added per-device LAN upload quotas in
  `app/features/ingest/limits.py`, with defaults of 30 files, 500 MiB, and a
  600-second window via `RAGKB_LAN_UPLOAD_*` settings. Loopback uploads are
  exempt.
- Added a settings-page read-only mode for paired LAN devices: a banner,
  disabled controls, hidden device-management section, and no device polling.
- Updated `README.md`, `SECURITY.md`, and `.env.example` with the supported
  boundary, CSRF header, upload limits, and trusted-LAN/no-TLS warning.

Verification:

- Added `tests/test_access_boundary.py` with 12 tests covering unpaired/paired
  access, local access, CSRF success/failure, CSRF cookie issuance, the
  read-only/write matrix, document/topic reads, and upload quota enforcement.
- Tests drive the real ASGI app with loopback and LAN client addresses using a
  small in-process ASGI harness, so no `httpx`/TestClient dependency is needed.
- Full suite: 127 tests passed.
- JavaScript syntax, Python compilation, TOML/YAML parsing, PowerShell parsing,
  and package scanning passed. The package contains the limits module and
  boundary tests, with no data, `.env`, or local-path leakage.

Remaining B2 work:

- Files and memory pages still show host-only actions to LAN users; the backend
  rejects them, but the mobile UI should hide/disable those actions.
- A real browser LAN acceptance run (pairing, CSRF header, uploads, settings
  read-only) is still needed.
- CI and the tag-release test gate are not implemented yet.
- LAN remains trusted-private-network HTTP only; no TLS or per-user accounts.

## Mobile UI and release gate (R1, 2026-10-30)

Mobile UI changes:

- Hidden the desktop collapsed icon rail at ≤900px. The phone now has one
  left-side entry point: the hamburger drawer. It no longer overlaps the chat
  top bar with duplicate upload/memory/settings icons.
- Fixed the three-dot menu: menu items use `display:flex`, which was overriding
  the native `hidden` attribute. `mm-item[hidden]` now hides stop/clear/speak
  items when their real buttons are unavailable or irrelevant.
- Reflowed the mobile graph: the graph bar scrolls horizontally, the global
  graph button no longer collapses into vertical text, the rail is compact, and
  the canvas receives the remaining viewport height. The mobile graph is now
  graph-first rather than giving the upper half to controls.
- Bumped `index.css`/`index.js` query versions so browsers do not keep the old
  mobile layout from cache.

Release gate changes:

- Added `.github/workflows/ci.yml`: Windows, Python 3.12, unit tests, JS syntax,
  PowerShell parse checks, release ZIP build, SHA256 verification, required-file
  checks, forbidden-path checks, and privacy marker scanning.
- Reworked `.github/workflows/release.yml`: a test job builds and validates the
  package and uploads the exact artifact; the release job downloads that tested
  artifact and publishes it. A tag/version mismatch fails the gate.

Verification:

- Full suite: 127 tests passed.
- Fresh release ZIP extracted into an isolated empty root; no app/engine venv
  was present before deployment. One-click deploy created both venvs, passed
  doctor, started all three services on isolated ports, and served `/web`.
- Local upload entered the parsing queue and reached `review`; no formal data
  was used.
- Current-code upgrade drill: clean 0.1.0 -> 0.1.1 with dependency sync,
  service restart, and health checks. Data/config markers survived.
- Injected 0.1.2 startup failure: code/status rollback restored 0.1.1, all
  services healthy, markers preserved, SQLite integrity `ok`.
- Mobile Chrome at 390x844: side rail `none`; three-dot menu shows only
  auto-read/files/settings when appropriate; graph rail 68px and canvas
  667px of the 844px viewport.
- Final package scan found no `data/`, `.env`, venv, local path, reference-app
  name, personal email, or placeholder path markers.

Remaining:

- GitHub remote, Actions execution, and the actual release download still need
  the first repository push.
- Files/memory pages still expose host-only actions to LAN UI; backend rejects
  them. This remains a B2 UX item, not a release-blocking auth change.
- The isolated clean services were stopped after verification. Formal services
  remain on 0.1.0 until a deliberate restart.

## Documentation and release preparation (2026-10-30)

- Added a prominent Windows quick start for non-technical users: install Ollama,
  pull `bge-m3` and `qwen2.5:3b`, extract the Release ZIP, run `deploy.cmd`,
  then `start.cmd`.
- Added a first-run checklist covering deploy, page open, upload, review,
  ingestion, chat, and restart persistence.
- Added `docs/TROUBLESHOOTING.md` for common model, dependency, port, knowledge
  service, upload, settings, update, and phone/LAN problems.
- Added `docs/RELEASE.md` with the release gate, clean-install check, tag/publish
  flow, artifact verification, and rollback rules.
- Documented that the GitHub Release ZIP is the supported distribution; the
  Python wheel is not a standalone web-UI distribution.
- Added a small trusted-LAN quick note for phone users; no new authentication or
  performance work was added.
- Finalized `CHANGELOG.md` under `0.1.0 - 2026-10-30`.

Verification:

- README links resolve to the new troubleshooting and release documents.
- Both new documents are included in the `docs` package entry.
- Test suite remains at 127 passing tests.
- Final package scan remains clean for runtime data, secrets, local paths, and
  placeholder path markers.
- Formal services remain on 0.1.0 until a deliberate restart.

Remaining release work:

- Create the GitHub repository, set the real remote/URL, push `main`, and verify
  the CI workflow on GitHub.
- Tag `v0.1.0`, wait for the tested release artifact, download it again, and run
  the README first-run checklist from that download.
- No further feature, authentication, or performance work is planned for this
  release.

## GitHub release preparation (R3, 2026-10-30)

- Added repository-metadata instructions to `docs/RELEASE.md`: real
  `[project.urls]`, issue security-advisory URL, `origin`, and first push.
- Release publishing now generates GitHub release notes and fails if the ZIP or
  checksum asset does not match; the tested artifact remains the only published
  asset source.
- Validated the release ZIP tests under Python 3.12 in an isolated extraction:
  `uv sync --frozen --no-dev --python 3.12`, then 127/127 tests passed.
- Release target: `https://github.com/napolun711-coder/rag-local-kb`.
  Real repository URLs were written to `pyproject.toml`, README, the issue
  security link, the updater example, and release documentation.
- `origin` is configured and `main` has been pushed. The first CI run exposed a
  privacy-scan false positive from the repository owner prefix; the scan now
  matches the full email. The fixed CI run passed.
- Annotated `v0.1.0` tag created and pushed at `eff0e50`.
- Release workflow completed successfully: test job built/validated the package
  and uploaded the artifact; release job published:
  `https://github.com/napolun711-coder/rag-local-kb/releases/tag/v0.1.0`
- Published assets downloaded and verified:
  - `local-rag-kb-0.1.0-win64.zip`
  - `local-rag-kb-0.1.0-win64.zip.sha256`
  - SHA256 `6a8fb7a40d3206684f3c86048d8d75d88e2a572751f1abada819400ecec68e2a`
- Formal services were not restarted and remain healthy on 0.1.0. Clean test
  services were stopped.

## Windows fresh-install Python 3.12 fix (0.1.1, 2026-10-30)

Root cause:

- The project had no `.python-version`.
- `scripts/setup.ps1` ran `uv sync --frozen --no-dev` without a Python selector.
- uv could select Python 3.15; `uv.lock` has PyYAML 6.0.3 cp312/cp313/cp314
  Windows wheels but no cp315 wheel, so a fresh machine fell back to the
  PyYAML source distribution and required Visual C++ Build Tools.

Fix:

- Added `.python-version` with `3.12`.
- `setup.ps1` sets `UV_PYTHON_PREFERENCE=only-managed` and runs
  `uv sync --frozen --no-dev --python 3.12`.
- Setup fails clearly if the app environment is not Python 3.12.
- Engine venv creation now goes through the same redirected uv wrapper.
- `.python-version` is included in release and upgrade code-entry lists.
- CI and release workflows explicitly install Python 3.12, sync with
  `--python 3.12`, assert the runtime, and validate `.python-version` in the
  package.
- Version bumped to `0.1.1`; `uv.lock` project version updated.

Verification:

- 131 tests passed, including four new Python-runtime regression tests.
- `uv lock --check` passed.
- Fresh isolated 0.1.1 deployment from the Release ZIP: no app/engine venv
  existed; deploy created both with managed Python 3.12; PyYAML 6.0.3 imported
  from the wheel; engine, shell, and app all healthy.
- `uv sync --no-build --no-install-project` passed for the dependency set,
  confirming no source build was required.
- A no-uv/no-Python simulation attempted to download uv, but GitHub release
  downloads timed out in this environment; the failure was network-only, not a
  Python/PyYAML build failure. Managed Python 3.12 was still downloaded and used
  in the separate clean deployment.
- Formal data/services were not modified; isolated test services were stopped.
