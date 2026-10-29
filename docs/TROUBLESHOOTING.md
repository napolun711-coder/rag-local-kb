# Troubleshooting

Start with the two built-in checks:

```powershell
.\status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\doctor.ps1"
```

Runtime logs are under `logs/runtime/`. Do not post `.env` contents, API keys,
databases, uploads, or full private logs in a public issue.

## `deploy.cmd` stops because Ollama or a model is missing

Install/start Ollama, then run:

```powershell
ollama pull bge-m3
ollama pull qwen2.5:3b
```

Re-run `deploy.cmd`. You can use another OpenAI-compatible endpoint by editing
`services\lightrag_engine\.env`; see `.env.example`.

## Deployment fails while installing dependencies

Check that the machine can reach the package sources, then re-run `deploy.cmd`.
All uv/Python/cache downloads stay inside `.tools/`; no system package install
is attempted. If a partial install remains, re-running the same command is the
supported repair path.

## The page does not open

Run `start.cmd` again and read the printed URL. The app normally uses port
`18080`; if it is occupied, the launcher scans forward and writes the selected
port to `logs/app.port`. Check `status.ps1` before assuming a service is down.

Do not kill unrelated processes just because they own a port.

## “知识服务不可用” / knowledge service unavailable

Run `status.ps1` and check engine, shell, and app. Then run `start.cmd` once. If
the engine failed to start, read the last lines of the engine stderr log under
`logs/runtime/`. Document viewing/downloading still works while the knowledge
service is unavailable; deletion is intentionally paused.

## Chat returns a generation error or “insufficient balance”

Open **Settings** and verify the generation endpoint/model. A local Ollama model
must be running and reachable; a cloud endpoint must have a valid key and
balance. Saving settings requires a service restart before the running process
uses the new values.

## An upload stays queued or does not finish

Open the files page and check the local state and timeline. Confirm the ingest
worker is running in `status.ps1`/logs. A large or unusual document may remain
in review, needs-OCR, or failed state instead of being silently accepted.

## Phone/LAN pairing

- Phone and computer must be on the same trusted private LAN.
- The request must be approved on the computer.
- Settings are read-only on the phone; editing stays on the computer.
- If a write returns `csrf_required`, refresh the page and retry.
- LAN access uses plain HTTP and has no internet-grade authentication. Do not
  expose it to the public internet or an untrusted network.

## Update did not finish

Run a no-write check first:

```powershell
update.cmd -CheckOnly
```

The updater keeps runtime/code backups under `.updates/backups/`. If startup or
dependency sync fails, it restores the previous code/data and attempts to start
the previous services. Read the final messages and logs before re-running.

## Reporting a bug

Use the repository issue form with:

- version or commit
- Windows version and Python version
- the failing step and exact error text
- a minimal reproduction with synthetic data
- whether the problem is local or LAN-only

Remove secrets and private data before posting.
