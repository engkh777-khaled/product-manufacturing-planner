# M02 — GitHub repository & push setup

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `afb6ce1` pushed |
| **Spec sections** | — |

## Goal
Publish the code to a dedicated GitHub repository.

## What was done
- Authenticated GitHub CLI as `engkh777-khaled` (browser device flow).
- Created **private** repo https://github.com/engkh777-khaled/product-manufacturing-planner
  (`gh repo create … --private --source . --remote planner`).
- Pushed `main`; local `main` tracks `planner/main`. Verified remote head = local head `afb6ce1`.
- `origin` (`AI-AGENT`) left unchanged. Design zip not committed (contents already in `design/stitch/`).
- Added [.gitignore](../../.gitignore): `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`.

## Decisions
- D-010 Dedicated private repo as separate remote `planner`.

## How to verify
```powershell
git remote -v
git status -sb        # ## main...planner/main
gh repo view engkh777-khaled/product-manufacturing-planner
```

## Known gaps / follow-ups
- Commit author is "Copilot" (local Git identity). Set `user.name` / `user.email` before the next commit.
- No CI yet — add a GitHub Actions workflow running `pytest` on push.

## Problems hit
- Push hung on the TLS handshake (schannel); solved with
  `git -c http.sslbackend=openssl -c http.version=HTTP/1.1 push`. See [troubleshooting.md](../troubleshooting.md).
