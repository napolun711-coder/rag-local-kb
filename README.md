# Local RAG Knowledge Base

A modular monolith for a self-hosted retrieval-augmented knowledge base.

## MVP capabilities

- FastAPI web application with chat, document, settings, and memory pages
- Upload, local parsing, quality review, content correction, and explicit manual
  submission to a knowledge engine
- Document catalog, deletion, name search, topic filtering, and safe original
  open/download
- Streaming chat with server-side session persistence and history search
- SQLite-backed long-term memory with local-only management and advisory review
- Stable LightRAG HTTP gateway with an optional LightRAG 1.5.6 process
- OpenAI-compatible generation and local Ollama embeddings
- Deterministic port fallback and unified service lifecycle
- Application-owned data, knowledge, configuration, and log directories

## Requirements

- Windows for the provided lifecycle scripts
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Ollama with an embedding model such as `bge-m3:latest` when embeddings are used
- An OpenAI-compatible chat-completions endpoint
- The isolated LightRAG engine environment under `services/lightrag_engine/`

## Run locally

```powershell
Copy-Item .env.example .env
uv sync
.\start.cmd
```

On Windows, double-click `start.cmd` for normal daily use. It starts the local
services, waits for them to become healthy, and opens `/web`. If the services
are already running, it opens the existing web UI instead of reporting an error.
Keep the command window open while startup is in progress; it closes after a
successful start.

The lifecycle starts the LightRAG engine, the HTTP shell, and the application in
dependency order. Open the URL printed by the launcher, normally:

```text
http://127.0.0.1:18080/web
```

Run the application alone for development:

```powershell
uv sync
uv run python main.py
```

`main.py` first tries `RAGKB_PORT` (default `18080`). If that port is reserved,
occupied, or otherwise not bindable, it tries `RAGKB_PORT_CANDIDATES`, then
scans forward up to `RAGKB_PORT_SCAN_LIMIT` ports. The selected port is written
to `logs/app.port`.

Direct `uvicorn --port ...` invocation is possible, but bypasses automatic port
fallback and does not start the knowledge shell or engine.

## Unified lifecycle

The source tree contains three local processes:

1. LightRAG 1.5.6 engine from its isolated virtual environment
2. the stable HTTP shell that isolates the application from engine details
3. the FastAPI application and web UI

Start them in dependency order with:

```powershell
.\start.cmd
```

The script starts the engine, waits for its health endpoint, starts the shell
with the actual engine address, and finally starts the application with the
actual shell address. It opens `/web` after all three services are healthy.
`start.ps1` remains the lower-level PowerShell entry point for development and
automation.

Inspect or stop only the processes recorded as started by this lifecycle:

```powershell
.\status.ps1
.\status.ps1 --json
.\stop.ps1
```

Runtime PID state, selected ports, and process logs are stored under
`logs/runtime/`. The lifecycle never stops an unrelated process merely because
that process occupies a configured port.

## Settings

Open `/settings` to configure the OpenAI-compatible generation model, the
embedding service, quality-gate thresholds, upload limits, and preferred ports.
The settings API is local-only.

- Secret values are never returned to the browser; only a masked hint is shown.
- Leaving a secret field empty keeps the existing value.
- Removing a secret requires the explicit clear action.
- Configuration files are updated atomically and backed up under
  `config/settings-backups/`, which is excluded from Git.
- Runtime services must be restarted after saving. The page reports whether the
  process is using older values.

Sections for features not yet implemented in this build are read-only and state
that explicitly, rather than saving configuration that would have no effect.

### Settings deployment follow-ups (optional)

The settings page currently provides the information architecture for local,
cloud, and hybrid views. The selected view is a browser-local preference; a
deployment profile and per-service routing are not yet persisted or enforced by
the server.

These are suggested follow-ups for future agents, not mandatory requirements:

- If deployment routing is added, persist the selected profile and any
  per-capability overrides only after the corresponding backend behavior exists.
  Preserve existing values and secrets during migration.
- Keep capabilities without a working integration read-only or marked as
  unavailable. Avoid exposing selectable cloud or local providers before their
  endpoints, validation, and connection tests are implemented.
- If cloud embedding, remote vector storage, local reranking, or TTS is added,
  document the data boundary and add per-service connection diagnostics.
- Consider making a local-only profile a hard privacy boundary when that
  behavior is implemented; local failures should not silently fall back to a
  cloud service.
- When changing the settings UI, prefer preserving existing field IDs and the
  current save payload unless a coordinated backend change is approved.

### Local deployment operation guide (suggested)

This is an operational recommendation, not a fixed deployment rule. See
[部署阶段.md](部署阶段.md) for the broader optional stage breakdown.

1. Start the application through the trusted local launcher or lifecycle
   command provided by the installation.
2. Start the local OpenAI-compatible generation service and, when embedding is
   enabled, Ollama or another compatible embedding service.
3. Open `/settings` and select the local configuration view. This view does not
   install or start services.
4. Fill in the local generation endpoint, model name, embedding endpoint,
   embedding model, and local data paths. Local services may not require an API
   key.
5. Use the service-level checks for the generation model, embedding model, and
   knowledge service. A filled field is not proof that the service is reachable.
6. Review the local deployment readiness card, save the configuration, and
   restart the application when requested.
7. Ingest a small document, confirm parsing and quality checks, and ask a
   question that should be answered from that document.

Operational cautions:

- Changing the embedding model can invalidate existing vectors.
- Changing the embedding dimension requires rebuilding the knowledge index.
- Qdrant and cloud vector-store options are not currently available; enabling
  them requires backend support plus a migration or rebuild plan.
- Reranking can be disabled, run against a local Cohere-compatible `/rerank`
  service, or use Cohere, Jina, or Aliyun DashScope in cloud mode. Local mode
  requires a loopback or private-network endpoint.
- TTS remains unavailable or read-only in this build. OCR is available only
  through the local Windows OCR engine and requires a matching installed language
  pack.
- Service-level tests use the existing settings test endpoint. Capabilities
  without an independent check are reported as having no independent test.

## Memory subsystem

The memory core is implemented as a feature module under `app/features/memory`.
It includes SQLite migrations, conversation storage, long-term memory, retrieval,
context assembly, background jobs, and deletion audit primitives.

Memory is disabled by default. When enabled, chat sessions are persisted and the
memory panel at `/memory` provides local-only management:

- filter, inspect, edit, confirm, reject, expire, restore, soft-delete, and
  permanently forget memories
- inspect quoted sources and the memory event history
- export and idempotently import a complete memory archive
- explicitly clear all memory data by typing `CLEAR ALL`

The browser contract lives under `/api/memory/*`. Administrative aliases remain
under `/api/memory/admin/*`. All write operations are restricted to the local
machine.

The optional extraction worker uses the existing job queue. It starts only when
memory, automatic extraction, and the worker switch are all enabled.

Memory runtime parameters can be edited from `/settings`: persistence, context
injection, Tier 1 fast memory, context budgets, recent-history limits, historical
recall, automatic extraction thresholds, and worker switches. These values are
written to the application `.env` and require a service restart. The current
extractor uses local rules; it does not make an additional model call.

Automatic supervision remains advisory. The memory page displays suggested
reviews for missing sources, expiry, sensitive content, low confidence,
duplicates, stale confirmations, and instruction-like text. Resolving or ignoring
a suggestion changes only that advisory finding; it never changes, expires, or
deletes a memory. A user must perform the corresponding memory action explicitly.
## Memory supervision

When memory is enabled, the optional supervisor scans active memories for missing
sources, expired validity windows, sensitive content, low confidence, duplicate
content, stale confirmations, and instruction-like prompt-injection text. It is
advisory only: it records findings and suggested actions, but never changes a
memory status, content, expiry, or deletion state. A user must perform any
correction explicitly from the memory page.

Supervision findings are visible on `/memory`, but policy editing remains a
backend-only operation under `/api/memory/admin`. Policy defaults are described
by `config/memory-supervisor.example.json`. The writable file is
`config/memory-supervisor.json`, which is ignored by Git.
## Document ingestion

The ingestion feature supports upload, local parsing, the quality gate, explainable
advice, manual review, and content correction. Uploaded sources and parsed state
are stored under the application-owned `data/` directory.

Reviewed content is submitted only after an explicit user action. The returned
track id is recorded, and indexing status is refreshed through the knowledge
gateway. Uploads are not presented as indexed until the engine confirms success.

## Inbox folder scanning

Inbox scanning is opt-in and disabled by default. Configure
`RAGKB_INBOX_ENABLED=true` and an existing `RAGKB_INBOX_DIR` in the settings page
or `.env`, then restart the application.

- Files remain in the inbox directory. The scanner never moves, overwrites, or
  deletes source files.
- Hidden files, temporary download/editor files, unsupported extensions,
  symlinks, and files above the upload limit are skipped or marked failed.
- A file is accepted only after its size and modification time remain unchanged
  for the configured settle interval.
- Accepted content is sent through the same local parsing and quality pipeline
  as a normal upload, then enters the local review queue.
- Inbox files are never submitted to the knowledge service automatically.
- SHA-256 content fingerprints prevent the same content from being accepted
  again under another filename.
- Scanner state is stored in the application data directory as
  `inbox-state.json`; it contains file metadata and hashes, never file contents.

Use “立即扫描收件箱” on the document page for a local manual scan. The API is
local-only and also requires the per-process control token. The “打开收件箱文件夹”
action uses a separate local-only endpoint with the same token and always opens
the configured inbox directory; it never accepts a client-supplied path.

## Local OCR

OCR uses the Windows built-in `Windows.Media.Ocr` engine through
`scripts/windows_ocr.ps1`. It runs locally and never sends image content to a
cloud service.

- OCR is disabled by default. Set `RAGKB_OCR_MODE=manual` or `auto`, keep
  `RAGKB_OCR_PROVIDER=local`, and restart the application.
- `manual` leaves image-based documents in `needs_ocr` until the user triggers
  “识别图片”; `auto` runs local OCR during local parsing.
- Embedded images in PDF, Word, Excel, and PowerPoint are processed only when
  `RAGKB_OCR_IMAGE_OCR=true`. Standalone image uploads always require OCR.
- The requested `RAGKB_OCR_LANG` must have a matching Windows OCR language pack.
  The settings page reports available languages and provides a local test.
- Per-image size and timeout limits are enforced. Missing OCR components,
  unsupported formats, timeouts, and empty recognition results produce explicit
  failures rather than empty successful text.
- OCR results are merged into the local parsed content, then pass through the
  existing quality gate and review queue. They are not automatically submitted
  to the knowledge service.
- Current scope uses images already extractable by the existing parsers; the
  application does not bundle a PDF page renderer.

## Document management

The document page at `/files` supports:

- name and summary search across knowledge-base documents and local jobs
- topic creation, per-document topic assignment, and topic filtering
- opening supported originals inline (PDF, images, and plain-text formats)
- downloading any original as an attachment
- controlled deletion with explicit confirmation

Originals are resolved only by the server-side ingestion job id. The API never
accepts an arbitrary filesystem path for opening or downloading. Resolved files
must exist under the configured upload, parsed, or knowledge directories.

HTML, SVG, Office files, archives, and unknown formats are never rendered inline
under the application origin; they are forced to download. Inline responses set
`nosniff`, private caching, and a restrictive CSP where applicable.

Topic tags are stored in the memory database. If memory is disabled, document
browsing and local originals still work, but topic controls remain hidden.

When the knowledge service is unavailable, `/api/files/list` and
`/api/files/overview` still return HTTP 200 with the application-owned local
records and `upstream.ok=false`. The page marks knowledge counts as unknown,
labels the local table as unverified against the knowledge base, and pauses
deletion. Viewing, filtering, details, and downloads remain available. The
application never presents the degraded local records as a complete knowledge
listing.

## Knowledge service

The application talks to a stable knowledge-gateway contract. The LightRAG HTTP
adapter lives under `app/adapters/lightrag`, while the standalone compatibility
shell lives under `services/lightrag_shell`.

Run the shell with:

```bash
uv run python -m services.lightrag_shell.main
```

Without `RAGKB_LIGHTRAG_BACKEND_URL`, the shell reports that the engine is not
installed and rejects engine-dependent operations. The lifecycle launcher passes
the actual engine address to the shell automatically.

## Reranking

The settings page exposes three modes:

- `off`: reranking is disabled.
- `local`: LightRAG uses its Cohere-compatible rerank adapter against a local or
  private-LAN `/rerank` endpoint.
- `cloud`: choose Cohere, Jina, or Aliyun DashScope and provide the API key.

Local mode requires an explicit model and endpoint and rejects public hosts.
Cloud mode uses the selected provider's default endpoint when the endpoint field
is empty. The independent “测试重排服务” action sends a fixed query with two
candidate documents and validates the returned index/score structure.

Common model choices are shown in the page as light-gray guidance:
`BAAI/bge-reranker-v2-m3` behind a local compatible service,
`jina-reranker-v2-base-multilingual`, and `gte-rerank-v2`.

Settings require a service restart before the running LightRAG engine uses the
new rerank binding.

## Knowledge submission

Reviewed parsed content can be submitted through the knowledge gateway. Submission
requires an explicit user action, records the returned track id, and can be checked
through `/ingest/knowledge-status/{file_id}`. If the engine is not installed or
unreachable, the job remains in `review` and records the failure instead of being
marked as ingested.

## Configuration

Runtime configuration is stored in `.env` and is not committed. Use
`.env.example` as the starting point. The settings page can update the supported
generation, embedding, memory, upload, quality, and port settings. Changes require
a service restart.

## Index compatibility

The application records the non-secret identity of the active vector index in
`data/index-compatibility.json`. The identity includes a hash of the engine
working directory, embedding binding, model, dimension, endpoint, and vector
storage type.

- A restart is still required after changing runtime configuration.
- If the engine restarts with a different embedding identity, chat queries and
  new knowledge submissions are rejected until the index is rebuilt.
- Document viewing, download, status inspection, and deletion remain available
  so the index can be repaired.
- After a real rebuild, the settings page can record the current index identity.
  This requires typing `REBUILD`; it never clears, migrates, or rebuilds data by
  itself.
- The first guarded run creates the baseline from the then-active index. Changes
  made before this protection existed cannot be detected retroactively.

Do not commit:

- `.env` files
- API keys or access tokens
- SQLite databases
- uploaded or parsed documents
- runtime logs
- `config/settings-backups/`

## Tests

```powershell
uv run python -m unittest discover -s tests -v
```

Tests must use temporary directories and must never use the production memory
database or uploaded documents.

## Local security model

The application binds to `127.0.0.1` by default. Administrative and write APIs
are local-only. Do not expose the service directly to an untrusted network
without adding authentication, authorization, CSRF protection, and transport
security.

## Current limitations

- Cloud TTS and Qdrant switching are not part of the current MVP.
- History search is lexical, not semantic.
- Advanced topic rename, archive, and hierarchy management are not implemented.
- Index compatibility depends on a baseline recorded by the current working
  directory; index changes made before that baseline cannot be reconstructed.
- OCR is Windows-only and processes only image files or images extractable from
  supported Office/PDF files; it does not render arbitrary PDF pages.

See `docs/PROJECT_STATE.md` for the current implementation state and remaining
release work.

## License

MIT. See `LICENSE`.
