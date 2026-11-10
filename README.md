# azup

One terminal console to **install, update and check your Azure toolbox** on Windows, macOS and Linux:
Azure CLI, Azure Developer CLI, Bicep, Az PowerShell, Microsoft Graph SDK, Entra PowerShell,
`az` extensions, kubectl/kubelogin, AzCopy, Terraform and more.

No dependencies besides Python 3.8+.

## Install

**macOS / Linux**
```sh
curl -fsSL https://raw.githubusercontent.com/saeid-adz/azup/main/install.sh | sh
```

**Windows (PowerShell)** – installs Python via winget if you don't have it
```powershell
irm https://raw.githubusercontent.com/saeid-adz/azup/main/install.ps1 | iex
```

**Any OS with pipx**
```sh
pipx install git+https://github.com/saeid-adz/azup
```

Then open a new terminal and run `azup`.

## Use

| Command | What it does |
|---|---|
| `azup` | Interactive menu |
| `azup select` | Checklist of every component – tick what you want, azup installs the missing and updates the outdated |
| `azup status` | What is installed / has an update |
| `azup install --profile core` | Core set (`az`, `azd`, Bicep, PowerShell, Az, Graph, resource-graph). Also `--profile full`, `--all`, or names: `azup install terraform ext-aks` |
| `azup update` | Update everything that is outdated (`azup update az Az` for specific ones) |
| `azup doctor` | Sign-in state for az / azd / Az PowerShell / Graph + health checks |
| `azup login [az\|azd\|ps\|graph\|all]` | Sign in |
| `azup self-update` | Update azup itself |

Add `-n/--dry-run` to see the commands without running them, `-y/--yes` to skip prompts.

Checklist keys: `↑/↓` move · `space` toggle · `a` all · `n` none · `u` outdated · `m` missing · `c` core · `enter` confirm · `q` cancel.

## How it works

| OS | CLI tools | PowerShell |
|---|---|---|
| macOS | Homebrew | cask |
| Linux | Homebrew (azup offers to install it) | snap |
| Windows | winget | winget |

PowerShell modules come from the PowerShell Gallery (`Install-Module` / `Update-Module`, current-user scope),
Bicep and extensions through `az`. Tools you installed some other way show as **external** and are never touched.

## Add a tool

Add one `Tool(...)` entry to `CATALOG` in `azup.py` (brew / winget ids, or a module / extension name).

## Publishing checklist

1. (done) repo owner is set to `saeid-adz`.
2. Push; CI (`.github/workflows/ci.yml`) smoke-tests Windows, macOS and Linux.

MIT licensed.
