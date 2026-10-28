# LightRAG HTTP Shell Contract

The shell is a stable boundary between the application and a future LightRAG
engine. With no engine configured it must return explicit errors and must never
return fabricated query results or mark documents as ingested.

## Endpoints

- `GET /health`
- `POST /query`
- `POST /query/stream`
- `POST /documents/upload`
- `GET /documents`
- `GET /documents/track_status/{track_id}`
- `DELETE /documents/delete_document`

## Unconfigured engine

`GET /health` returns HTTP 200 with `"engine_installed": false`.

Operations that require the engine return HTTP 503 with:

```json
{"error":{"code":"engine_not_installed","message":"The LightRAG engine is not configured or installed."}}
```