# Security policy

azup installs and updates software on your machine, so security reports are taken seriously.

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report privately through GitHub: <https://github.com/saeid-adz/azup/security/advisories/new>
("Security" tab → "Report a vulnerability"). Include what you found, how to reproduce it and the
impact you see. You'll get an acknowledgement within **7 days**. azup is maintained by one person in
their spare time, so fixes are best effort, but anything that can run unintended code gets priority.

## Supported versions

Only the **latest release** receives fixes. Update with `azup self-update`.

## What azup does on your machine (trust model)

| Area | Behaviour |
|---|---|
| Installing tools | Runs your package managers (Homebrew, winget, PowerShell Gallery, `az`, `azd`). Nothing is installed without showing a plan and asking, unless you pass `-y`. |
| Elevated rights | Never runs `sudo` or trusts a Homebrew tap without asking first. Linux PowerShell is installed to `~/.local` without sudo, and its download is SHA-256 verified. |
| Files it writes | `~/.azup/` (the script, cache, config, logs). Optional, only when you opt in: a marked block in your shell profile (`azup nudge enable`) and a scheduled job (`azup schedule enable`). Both are removable with `disable`. |
| Network | Package-manager traffic, `api.github.com` / `raw.githubusercontent.com` (release notes, self-update, latest versions) and python.org (only if the Windows installer has to install Python). **No telemetry.** |
| Credentials | azup never reads or stores your tokens or passwords. It only calls `az`, `azd` and PowerShell to show *which* account you're signed in as. `GITHUB_TOKEN`, if you set it, is sent only to `api.github.com`. |

## Installing safely

The one-line installers download `azup.py` from this repository. To avoid trusting a moving branch,
pin a release tag, which is immutable:

```sh
curl -fsSL https://raw.githubusercontent.com/saeid-adz/azup/main/install.sh | AZUP_BRANCH=v2.6.2 sh
```
```powershell
$env:AZUP_BRANCH = 'v2.6.2'; irm https://raw.githubusercontent.com/saeid-adz/azup/main/install.ps1 | iex
```

Prefer to read first? Both scripts are short, and `azup.py` is a single file with no dependencies.

## How this repository is protected

`main` and release tags are protected by rulesets (no force-push, no deletion, changes via pull request
with passing CI), releases are immutable, secret scanning with push protection, Dependabot, CodeQL and
private vulnerability reporting are enabled, and workflow actions are pinned to commit hashes.
