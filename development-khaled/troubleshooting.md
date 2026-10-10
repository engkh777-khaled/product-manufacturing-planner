# Troubleshooting

Problems hit during development and how they were solved. Add new entries at the bottom.

---

## `git push` / `git ls-remote` hangs with no output
_Seen 2026-10-10 (M02)_

**Symptom.** Push to `planner` hangs indefinitely; `GIT_CURL_VERBOSE=1` shows the schannel TLS
handshake stuck at `step 2/3 … need more data`. `gh api` calls work fine.

**Cause.** Unstable network path to github.com interacting badly with Git's default Windows TLS
backend (schannel). Credentials were fine (`gh auth git-credential get` returns instantly).

**Fix.** Use OpenSSL and HTTP/1.1 for the push:
```powershell
git -c http.sslbackend=openssl -c http.version=HTTP/1.1 push
```
To make it permanent for this repo: `git config http.sslbackend openssl` and `git config http.version HTTP/1.1`.

## `gh auth login` via `! gh auth login` in the IDE chat did nothing
_Seen 2026-10-10 (M02)_

**Fix.** Run the device flow non-interactively and complete it in the browser:
```bash
echo | gh auth login --hostname github.com --git-protocol https --web
```
It prints a one-time code; enter it at https://github.com/login/device.

## `pip install` times out
_Seen 2026-10-10 (M01)_

**Fix.** Retry with a longer timeout: `pip install --timeout 60 -r requirements.txt`.

## A Tailwind color class has no effect
_Seen 2026-10-10 (M01)_

**Symptom.** Gantt bars for SO-1020 had invisible text.

**Cause.** `text-on-tertiary-fixed` was used but the token was missing from `tailwind.config.js`,
so Tailwind silently generated nothing.

**Fix.** Add the token, rebuild CSS. To check whether a class exists in the build:
```bash
grep -o 'text-on-tertiary-fixed{[^}]*}' app/static/css/app.css
```

## Headless Chrome screenshots look clipped at phone width
_Seen 2026-10-10 (M01)_

**Cause.** `chrome --headless --window-size=390,…` lays the page out wider than 390 px, then crops.
It is not a real overflow.

**How to really check overflow.** Load the page in a 375 px same-origin iframe and compare
`document.documentElement.scrollWidth` to `clientWidth` (both were 375 for `/dashboard`).

## Git commits show author "Copilot"
_Seen 2026-10-10 (M02)_

**Cause.** This working copy's Git identity is set to "Copilot". Set your own before committing:
```powershell
git config user.name "Your Name"
git config user.email "you@example.com"
```
