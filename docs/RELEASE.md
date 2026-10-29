# Release Guide

The supported user artifact is the Windows Release ZIP (`local-rag-kb-<version>-win64.zip`)
plus its `.sha256` file. The Python wheel is not a standalone web-UI distribution.

## 1. Freeze and version

- Finish the release notes in `CHANGELOG.md`.
- Keep `pyproject.toml` `version` equal to the tag without the leading `v`.
- Do not add features, authentication changes, or performance limits during the
  release gate.

## 2. Run the release gate

```powershell
uv sync --frozen --no-dev
uv run --frozen python -m unittest discover -s tests -v
```

Then check static JavaScript and PowerShell scripts, and build the package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\package_release.ps1"
```

The GitHub Actions `CI` workflow repeats these checks on Windows. The tag
`Release` workflow builds and validates the exact artifact before publishing it.

Before tagging, verify in an isolated directory:

1. Extract the Release ZIP into an empty folder.
2. Run `deploy.cmd`; it must create project-local environments and pass doctor.
3. Run `start.cmd`; all three services must become healthy.
4. Upload a small text file, confirm review, and confirm ingestion.
5. Restart and confirm the document/history remains.
6. Run one upgrade and one intentional rollback test with `update.cmd` using
   temporary data only.

Do not test with a real user database or upload directory.

## 3. Publish

First-time repository setup:

```powershell
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

After CI passes, create and push the tag:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The release workflow:

- checks that the tag matches `pyproject.toml`
- runs the tests and static/package checks
- verifies the ZIP SHA256 and package contents
- uploads the tested ZIP/checksum artifact
- publishes the release only after the test job succeeds

## 4. Verify the published release

Download the ZIP and `.sha256` from the release page again, verify the checksum,
extract into a clean folder, and repeat the first-run checklist in
`README.md`. This catches packaging mistakes that a developer checkout hides.

## 5. Rollback

If an update fails, the updater restores the previous code and runtime backup.
Backups remain under `.updates/backups/`; do not delete them while diagnosing a
failed release. For a release-level rollback, publish a new patch tag with the
previous code and a higher version rather than rewriting a published tag.

## Release rules

- Never include `.env`, API keys, databases, uploads, parsed content, logs,
  virtual environments, or user configuration in the ZIP.
- Never change user data during packaging.
- Prefer a small patch release over a risky last-minute change.
- Keep LAN support framed as trusted-private-network use; it has no TLS or
  internet-grade multi-user authentication.
