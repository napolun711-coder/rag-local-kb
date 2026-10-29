# Changelog

## Unreleased

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
- settings: mark unimplemented confirmation modes and remove the false
  automatic-skip claim

## 0.1.0

Initial local MVP:

- local FastAPI application and static web UI
- document upload, parsing, quality review, and manual knowledge submission
- document search, topic filtering, safe original open/download, and deletion
- chat, streaming responses, session persistence, and history search
- long-term memory, management UI, advisory supervision, and runtime settings
- OpenAI-compatible generation configuration and local Ollama embeddings
- LightRAG HTTP shell and isolated LightRAG 1.5.6 engine integration
- unified service lifecycle with deterministic port fallback
