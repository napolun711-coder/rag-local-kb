# Agent Instructions

## Project scope

This repository is a modular monolith for a local RAG knowledge base. Preserve
the existing user-visible behavior and runtime configuration. Prefer focused
changes over broad rewrites.

## Hard boundaries

- Treat any external or original application used as reference material as
  read-only. Never modify it, read its business data, or copy its private names,
  prompts, documentation, configuration, sessions, uploads, or databases.
- Never write test data to the formal application data directories. Tests and
  verification runs must use an isolated temporary database and temporary
  upload/parsed directories.
- Never print, expose, commit, or log API keys, tokens, .env contents, or other
  secrets.
- Do not delete, migrate, move, or rewrite user data without explicit approval.
- Keep local-only APIs local. Do not add remote exposure without a separately
  approved authentication and authorization design.
- Only one agent/chat may write to this checkout at a time.

## Change workflow

1. Inspect the relevant code and runtime path before editing.
2. For multi-file or architectural work, provide a short scope, risk, and test
   plan first, then wait for approval.
3. Work in bounded waves. Do not automatically continue into the next wave.
4. Do not add a new abstraction, directory, or configuration layer unless it is
   needed by an existing behavior or an approved requirement.
5. After a wave, update `docs/PROJECT_STATE.md` with verified changes and remaining
   risks.
6. Stop after the approved wave and wait for review.

## Validation

- Run focused checks appropriate to the changed area.
- Do not run the full regression suite after every small edit.
- Use a final cold-start regression only at the release gate.
- Never claim a check passed unless it was actually run.

## Reporting

Keep reports concise:

- outcome
- files changed
- validation performed
- remaining risks or blockers

Do not paste complete source files, complete HTML pages, or large raw logs unless
the user explicitly asks for them.
