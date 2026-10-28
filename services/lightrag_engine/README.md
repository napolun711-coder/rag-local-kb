# LightRAG 1.5.6 Engine

This directory contains an isolated LightRAG 1.5.6 environment.

- Python: 3.12
- Engine port: 10262
- App shell: 10261
- LLM: OpenAI-compatible, manually configured in `.env`
- Embeddings: local Ollama `bge-m3:latest`
- Vector store: NanoVectorDBStorage
- Graph store: NetworkXStorage
- KV store: JsonKVStorage

Fill `.env` before starting:

```env
LLM_BINDING_HOST=...
LLM_BINDING_API_KEY=...
LLM_MODEL=...
```

Run from the project root:

```powershell
.\services\lightrag_engine\run.ps1
```

The engine is not started automatically by the application. The shell must be pointed at it with `RAGKB_LIGHTRAG_BACKEND_URL=http://127.0.0.1:10262`.