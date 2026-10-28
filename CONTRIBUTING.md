# Contributing

Thanks for improving Local RAG Knowledge Base.

## Before contributing

- Do not commit API keys, `.env` files, databases, uploads, parsed documents,
  runtime logs, or files from `config/settings-backups/`.
- Use only synthetic or temporary data for tests and examples.
- Keep the application local-only unless a separately reviewed authentication
  and authorization design is included.
- Preserve the modular-monolith structure and existing public API contracts.

## Development setup

```powershell
Copy-Item .env.example .env
uv sync
uv run python main.py
```

The full three-process environment can be started with:

```powershell
.\start.ps1
```

## Tests

```powershell
uv run python -m unittest discover -s tests -v
```

Tests must use `TemporaryDirectory` or another isolated app-owned directory.
Never point tests at `data/memory/ragkb.db` or the configured upload directory.

## Pull requests

Keep each pull request focused on one feature or defect. Include:

- the user-visible outcome
- affected files or modules
- focused validation performed
- data, security, migration, or rollback considerations

Avoid broad rewrites when a bounded change is sufficient. Do not mix unrelated
formatting, refactoring, and behavior changes in one pull request.

## Architecture expectations

- Keep `app/main.py` as the only application composition root.
- Keep feature boundaries under `app/features/`.
- Keep engine-specific HTTP details in the LightRAG adapter or shell.
- Reuse existing storage and configuration services instead of duplicating
  database or environment-file logic.

## Reporting security issues

Do not disclose vulnerabilities in a public issue. Follow `SECURITY.md`.
