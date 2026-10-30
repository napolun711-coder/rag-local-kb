# LightRAG HTTP Shell Contract

The shell is a stable boundary between the application and a future LightRAG
engine. With no engine configured it must return explicit errors and must never
return fabricated query results or mark documents as ingested.

## Endpoints

- `GET /health`
- `POST /query`
- `POST /query/stream`
- `POST /documents/upload`
- `POST /documents/text`
- `GET /documents`
- `GET /documents/track_status/{track_id}`
- `DELETE /documents/delete_document`
- `GET /graph/global?max_nodes=400`
- `GET /graph/subgraph?label=&depth=2&max_nodes=60`
- `GET /graph/popular?limit=16`
- `GET /graph/search?q=&limit=20`

The graph endpoints are a deliberately read-only allowlist. They proxy
LightRAG's `/graphs` and `/graph/label/*` queries and never expose entity or
relation create, edit, merge, or delete operations.

## Unconfigured engine

`GET /health` returns HTTP 200 with `"engine_installed": false`.

Operations that require the engine return HTTP 503 with:

```json
{"error":{"code":"engine_not_installed","message":"The LightRAG engine is not configured or installed."}}
```
