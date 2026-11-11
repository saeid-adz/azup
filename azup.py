#!/usr/bin/env python3
"""azup - install, update and check your Azure engineering toolbox on Windows, macOS and Linux.

    azup                  interactive menu
    azup select           checklist: tick what you want, azup installs/updates it
    azup status           what is installed / outdated
    azup install [...]    install tools  (names, --profile core|full, --all; no args = checklist)
    azup update  [...]    update tools   (default: everything outdated)
    azup doctor           sign-in + health checks
    azup changes [name]   release notes for what's outdated (or the named tools)
    azup nudge [enable]   one-line "3 tools outdated" reminder when you open a terminal
    azup schedule enable  update automatically (weekly/daily) via launchd / systemd / Task Scheduler
    azup whoami           who am I signed in as, in az / azd / Az PowerShell / Graph?
    azup ctx [name]       switch subscription everywhere at once (type-to-filter picker)
    azup login [target]   az | azd | ps | graph | all
    azup self-update      update azup itself
    azup joke             Azzy tells a joke (animations: --no-anim or AZUP_NO_ANIM=1 to disable)

Backends: Homebrew (macOS / Linux), winget (Windows), Microsoft's tarball (PowerShell on Linux, no sudo),
PowerShellGet (PowerShell modules) and `az extension` / `az bicep`.
Python 3.8+, standard library only.
"""
import argparse
import concurrent.futures as cf
import glob
import hashlib
import json
import platform
import plistlib
import os
import random
import re
import select
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = tty = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

VERSION = "2.7.1"
REPO = os.environ.get("AZUP_REPO", "saeid-adz/azup")   # set once after publishing
BRANCH = os.environ.get("AZUP_BRANCH", "main")

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
OS_KEY = "win" if IS_WIN else "mac" if IS_MAC else "linux"

if IS_WIN:
    os.system("")  # enables ANSI escape processing in the Windows console
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TTY = sys.stdout.isatty()
COLOR = TTY and "NO_COLOR" not in os.environ


# ───────────────────────────── styling ─────────────────────────────
def _c(code):
    return (lambda s: f"\x1b[{code}m{s}\x1b[0m") if COLOR else (lambda s: s)


bold, dim, red, green, yellow, cyan = _c("1"), _c("2"), _c("31"), _c("32"), _c("33"), _c("36")
azure = _c("38;5;39")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain_len(s):
    return len(ANSI.sub("", s))


def pad(s, w):
    return s + " " * max(0, w - plain_len(s))


# ───────────────────────────── animation ─────────────────────────────
# Opt out with AZUP_NO_ANIM=1, --no-anim, NO_COLOR, or by piping the output.
ANIM = TTY and not os.environ.get("AZUP_NO_ANIM") and "--no-anim" not in sys.argv
TRUECOLOR = COLOR and (
    os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    or "WT_SESSION" in os.environ
    or os.environ.get("TERM_PROGRAM") in ("iTerm.app", "vscode", "WezTerm", "ghostty"))

ART = [
    "   ___   _____  _   _ ____  ",
    "  / _ \\ |__  / | | | |  _ \\ ",
    " / /_\\ \\  / /  | | | | |_) |",
    "/ _____ \\/ /_  | |_| |  __/ ",
    "\\_/   \\_/____|  \\___/|_|    ",
]
STOPS = [(0, 90, 200), (0, 150, 235), (80, 220, 255)]            # Azure blues
CONFETTI = [(242, 80, 34), (127, 186, 0), (0, 164, 239), (255, 185, 0), (180, 110, 255)]


def rgb(r, g, b, s):
    return f"\x1b[38;2;{r};{g};{b}m{s}\x1b[0m" if TRUECOLOR else azure(s)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def grad(t):
    t = min(1.0, max(0.0, t))
    return lerp(STOPS[0], STOPS[1], t * 2) if t < .5 else lerp(STOPS[1], STOPS[2], (t - .5) * 2)


def art_frame(pos):
    """The banner as 5 coloured lines, with a white shine centred on column `pos`."""
    w = max(len(l) for l in ART)
    out = []
    for line in ART:
        if not TRUECOLOR:
            out.append(azure(line))
            continue
        s = ""
        for x, ch in enumerate(line):
            if ch == " ":
                s += " "
                continue
            k = max(0.0, 1 - abs(x - pos) / 5) ** 2 * 0.85
            s += rgb(*lerp(grad(x / w), (255, 255, 255), k), ch)
        out.append(s)
    return out


def banner(animate=False):
    print()
    animate = animate and ANIM
    if animate and TRUECOLOR:
        w = max(len(l) for l in ART)
        for n, pos in enumerate(range(-6, w + 10, 2)):
            if n:
                sys.stdout.write(f"\x1b[{len(ART)}A")
            sys.stdout.write("\n".join("  " + l for l in art_frame(pos)) + "\n")
            sys.stdout.flush()
            time.sleep(0.025)
    else:
        for l in art_frame(-99):
            print("  " + l)
    tag = f"Azure toolbox manager · v{VERSION} · {OS_NAME}"
    if animate:
        sys.stdout.write("  ")
        for ch in tag:
            sys.stdout.write(dim(ch))
            sys.stdout.flush()
            time.sleep(0.008)
        print()
    else:
        print("  " + dim(tag))
    print()


# ── mascot ("Azzy", an original little cloud-bot) ──
JOKES = [
    "It works on my subscription.",
    "Tried turning the tenant off and on again?",
    "Naming things: still the hard part.",
    "Please don't `az group delete` on a Friday.",
    "I read your Bicep. It's lovely. (I'm a bot.)",
    "Tags are a love language. Tag your stuff.",
    "Tip: 'u' in the checklist ticks outdated.",
    "Tip: `azup doctor` checks all your sign-ins.",
    "Fun fact: Graph 'beta' is a lifestyle.",
    "99 bugs in the code. Patch one: 117 bugs.",
    "Localhost: the only region with 100% uptime.",
    "Reticulating Azure splines…",
    "Counting your subscriptions. Is that… 14?",
    "Teaching the cloud to behave…",
]
CHEERS = ["All done! Your toolbox is looking sharp.",
          "Done. That was smooth. Suspiciously smooth.",
          "Everything is shipshape. No resource groups were harmed.",
          "Fresh bits installed. Go deploy something!"]
OOPS = ["Something went wrong. The log above knows more than I do.",
        "Oops. Scroll up – the error is shy, but it's there."]
BUBBLE_W = 46


# Azzy is an "A" (a nod to the Azure shape, drawn from scratch): light left half, dark right half.
# 'e' = eye cell, 'm' = mouth cell.
MASCOT = ["    ▟███▙    ",
          "   ▟█████▙   ",
          "  ▟█e███e█▙  ",
          " ▟████m████▙ ",
          "▟██▘     ▝██▙"]
MASCOT_W = 13


def mascot_rows(eyes="◕ ◕", mouth="‿"):
    out = []
    for row in MASCOT:
        s, seen = "", 0
        for x, ch in enumerate(row):
            fg, bg = ("38;5;81", "48;5;81") if x < MASCOT_W // 2 else ("38;5;33", "48;5;33")
            if ch == " ":
                s += " "
            elif ch in "em":
                g = mouth if ch == "m" else (eyes[0] if not seen else eyes[-1])
                seen += ch == "e"
                s += f"\x1b[97;{bg}m{g}\x1b[0m" if COLOR else g
            else:
                s += f"\x1b[{fg}m{ch}\x1b[0m" if COLOR else ch
        out.append(s)
    return out


def mascot_block(lines, eyes="◕ ◕", mouth="‿"):
    """Mascot on the left, speech bubble on the right. Returns a list of strings."""
    w = max(plain_len(l) for l in lines)
    body = [f"│ {pad(l, w)} │" for l in lines]
    bub = ["╭" + "─" * (w + 2) + "╮"] + body + ["╰" + "─" * (w + 2) + "╯"]
    m = mascot_rows(eyes, mouth)
    start = max(0, (len(m) - len(bub)) // 2)
    mid = start + len(bub) // 2
    rows = []
    for r in range(max(len(m), len(bub) + start)):
        left = m[r] if r < len(m) else " " * MASCOT_W
        j = r - start
        right = dim(bub[j]) if 0 <= j < len(bub) else ""
        tail = azure("◂ ") if r == mid else "  "
        rows.append("  " + left + tail + right)
    return rows


def say(msg, mood="happy"):
    eyes, mouth = {"happy": ("◕ ◕", "‿"), "sad": ("╥ ╥", "∩"), "think": ("◔ ◔", "─")}[mood]
    lines = textwrap.wrap(msg, BUBBLE_W) or [""]
    print("\n".join(mascot_block(lines, eyes, mouth)))


def confetti(width=46):
    if not ANIM:
        return
    syms = "✦✧·•*+"
    for _ in range(12):
        line = "".join(rgb(*random.choice(CONFETTI), random.choice(syms)) if random.random() < .35
                       else " " for _ in range(width))
        sys.stdout.write("\r  " + line + "\x1b[K")
        sys.stdout.flush()
        time.sleep(0.06)
    sys.stdout.write("\r\x1b[K")


def progress(i, n, width=18):
    filled = int(width * i / max(n, 1))
    cells = "".join(rgb(*grad(x / width), "█") if x < filled else dim("░") for x in range(width))
    return f"▕{cells}▏"


class Spinner:
    """Plain spinner, or (mascot=True) Azzy with blinking eyes who tells jokes if you wait."""
    FR = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    EYES = ["◕ ◕"] * 8 + ["─ ─"] + ["◕ ◕"] * 5 + ["◔ ◔"] * 3 + ["◑ ◑"] * 3 + ["◕ ◕"] * 4
    H = 5

    def __init__(self, msg, mascot=False):
        self.msg, self._stop = msg, threading.Event()
        self.mascot = mascot and ANIM and shutil.get_terminal_size().columns >= 72
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        i, t0, seed = 0, time.time(), random.randrange(len(JOKES))
        while not self._stop.is_set():
            if self.mascot:
                el = time.time() - t0
                text = self.msg if el < 2.5 else JOKES[(seed + int((el - 2.5) // 3.5)) % len(JOKES)]
                text = pad(f"{cyan(self.FR[i % 10])} {text}", BUBBLE_W)
                rows = mascot_block([text], self.EYES[(i // 2) % len(self.EYES)], "o")
                if i:
                    sys.stdout.write(f"\x1b[{self.H}A")
                sys.stdout.write("".join("\x1b[K" + r + "\n" for r in rows))
            else:
                sys.stdout.write(f"\r  {cyan(self.FR[i % 10])} {self.msg}\x1b[K")
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    def __enter__(self):
        if TTY:
            sys.stdout.write("\x1b[?25l")
            self.t.start()
        return self

    def __exit__(self, *a):
        if TTY:
            self._stop.set()
            self.t.join()
            sys.stdout.write(f"\x1b[{self.H}A\x1b[J" if self.mascot else "\r\x1b[K")
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()



# ───────────────────────────── catalog ─────────────────────────────
@dataclass
class Tool:
    key: str
    name: str
    group: str
    kind: str                 # pkg | psmod | azext | bicep
    desc: str = ""
    core: bool = False
    brew: str = ""            # Homebrew formula (macOS + Linux)
    cask: str = ""            # Homebrew cask (macOS only, preferred over formula there)
    winget: str = ""          # winget package id (Windows)
    snap: str = ""            # snap (Linux fallback when there is no brew formula)
    ghtar: str = ""           # GitHub repo whose release tarball is installed to ~/.local (Linux, no sudo)
    cmd: str = ""             # binary name, used to spot installs made outside azup
    vcmd: Tuple[str, ...] = ()
    pkg: str = ""             # module / extension name for psmod / azext / azdext
    ai: bool = False          # part of `--profile ai`
    platforms: Tuple[str, ...] = ()   # limit a pkg to these OSes: "win", "mac", "linux"
    min_azd: str = ""         # azd extensions: minimum azd version
    repo: str = ""            # GitHub "owner/name" that publishes release notes
    tag_re: str = ""          # only releases whose tag matches (for repos that mix several products)
    notes_url: str = ""       # markdown release-notes page with "## <date>\\n\\nVersion x.y.z" sections

    @property
    def short(self):
        return (self.brew or self.cask).split("/")[-1]


CATALOG: List[Tool] = [
    Tool("az", "Azure CLI", "CLI", "pkg", "az command line", True,
         brew="azure-cli", winget="Microsoft.AzureCLI", cmd="az", repo="Azure/azure-cli", tag_re=r"^azure-cli-\d", notes_url="https://raw.githubusercontent.com/MicrosoftDocs/azure-docs-cli/main/docs-ref-conceptual/Latest-version/release-notes-azure-cli.md"),
    Tool("azd", "Azure Developer CLI", "CLI", "pkg", "azd up / templates", True,
         brew="azd", winget="Microsoft.Azd", cmd="azd", vcmd=("azd", "version"), repo="Azure/azure-dev", tag_re=r"^azure-dev-cli_"),
    Tool("bicep", "Bicep", "CLI", "bicep", "IaC language (via az bicep)", True, repo="Azure/bicep"),
    Tool("func", "Functions Core Tools", "CLI", "pkg", "local Azure Functions",
         brew="azure/functions/azure-functions-core-tools@4",
         winget="Microsoft.Azure.FunctionsCoreTools", cmd="func", repo="Azure/azure-functions-core-tools"),
    Tool("kubelogin", "kubelogin", "CLI", "pkg", "Entra auth for AKS",
         brew="azure/kubelogin/kubelogin", winget="Microsoft.Azure.Kubelogin", cmd="kubelogin", repo="Azure/kubelogin"),
    Tool("kubectl", "kubectl", "CLI", "pkg", "Kubernetes CLI",
         brew="kubernetes-cli", winget="Kubernetes.kubectl", cmd="kubectl",
         vcmd=("kubectl", "version", "--client"), repo="kubernetes/kubernetes"),
    Tool("azcopy", "AzCopy", "CLI", "pkg", "fast blob/file transfer",
         brew="azcopy", winget="Microsoft.Azure.AZCopy.10", cmd="azcopy", repo="Azure/azure-storage-azcopy"),
    Tool("sqlcmd", "sqlcmd (go)", "CLI", "pkg", "Azure SQL shell",
         brew="sqlcmd", winget="Microsoft.Sqlcmd", cmd="sqlcmd", repo="microsoft/go-sqlcmd"),
    Tool("terraform", "Terraform", "CLI", "pkg", "IaC (azurerm)",
         brew="hashicorp/tap/terraform", winget="Hashicorp.Terraform", cmd="terraform", repo="hashicorp/terraform"),
    Tool("pwsh", "PowerShell 7", "PowerShell", "pkg", "needed for the modules below", True,
         cask="powershell", winget="Microsoft.PowerShell", ghtar="PowerShell/PowerShell", cmd="pwsh", repo="PowerShell/PowerShell"),
    Tool("Az", "Az PowerShell", "PowerShell", "psmod", "Azure PowerShell module", True, pkg="Az", repo="Azure/azure-powershell"),
    Tool("Microsoft.Graph", "Microsoft Graph SDK", "PowerShell", "psmod", "Graph API v1.0 cmdlets",
         True, pkg="Microsoft.Graph", repo="microsoftgraph/msgraph-sdk-powershell"),
    Tool("Microsoft.Graph.Beta", "Microsoft Graph Beta", "PowerShell", "psmod",
         "Graph API beta cmdlets", pkg="Microsoft.Graph.Beta", repo="microsoftgraph/msgraph-sdk-powershell"),
    Tool("Microsoft.Entra", "Microsoft Entra PS", "PowerShell", "psmod", "Entra ID admin cmdlets",
         pkg="Microsoft.Entra", repo="microsoftgraph/entra-powershell"),
    Tool("ext-graph", "ext: resource-graph", "az extensions", "azext",
         "Azure Resource Graph queries", True, pkg="resource-graph"),
    Tool("ext-devops", "ext: azure-devops", "az extensions", "azext", "Azure DevOps from az",
         pkg="azure-devops"),
    Tool("ext-aks", "ext: aks-preview", "az extensions", "azext", "AKS preview features",
         pkg="aks-preview"),
    # ── Microsoft Foundry / AI ──
    Tool("foundry-local", "Foundry Local", "AI", "pkg", "run models on your machine (preview)",
         ai=True, brew="microsoft/foundrylocal/foundrylocal", winget="Microsoft.FoundryLocal",
         cmd="foundry", platforms=("win", "mac"), repo="microsoft/Foundry-Local"),
    Tool("azd-foundry", "azd: Microsoft Foundry", "AI", "azdext", "Foundry bundle for azd (agents, models…)",
         ai=True, pkg="microsoft.foundry", min_azd="1.37.0", repo="Azure/azure-dev", tag_re=r"^azd-ext-microsoft-foundry_"),
    Tool("azd-agents", "azd: AI agents", "AI", "azdext", "scaffold / deploy hosted agents",
         pkg="azure.ai.agents", min_azd="1.25.0", repo="Azure/azure-dev", tag_re=r"^azd-ext-azure-ai-agents_"),
    Tool("ext-ml", "ext: ml (Azure ML)", "AI", "azext", "Azure ML v2 + Foundry hubs for az",
         ai=True, pkg="ml"),
]


def spec(t: Tool) -> Optional[Tuple[str, str]]:
    """(manager, package-id) for a 'pkg' tool on this OS, else None."""
    if t.kind != "pkg":
        return None
    if t.platforms and OS_KEY not in t.platforms:
        return None
    if IS_WIN:
        return ("winget", t.winget) if t.winget else None
    if IS_MAC:
        if t.cask:
            return ("cask", t.cask)
        return ("brew", t.brew) if t.brew else None
    if t.brew:
        return ("brew", t.brew)
    if t.ghtar:
        return ("tarball", t.ghtar)
    return ("snap", t.snap) if t.snap else None


TOOLS = [t for t in CATALOG if t.kind != "pkg" or spec(t)]
BY_KEY = {}
for _t in TOOLS:
    BY_KEY.setdefault(_t.key.lower(), _t)       # case-insensitive; first in the catalog wins ("az" = Azure CLI)
ALIASES = {"azps": "Az", "az-ps": "Az", "az-powershell": "Az", "azure-cli": "az", "azcli": "az",
           "graph": "Microsoft.Graph", "entra": "Microsoft.Entra", "functions": "func"}
HOST_NAME = {"brew": "Homebrew", "cask": "Homebrew", "winget": "winget", "snap": "snap"}


# ───────────────────────────── process helpers ─────────────────────────────
def have(c):
    return shutil.which(c) is not None


def refresh_path():
    """Pick up PATH changes made by installers that ran during this session."""
    if IS_WIN:
        try:
            import winreg
            parts = []
            for hive, sub in ((winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                              (winreg.HKEY_CURRENT_USER, "Environment")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
                except OSError:
                    pass
            wa = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps")   # winget lives here
            os.environ["PATH"] = os.pathsep.join(parts + [wa, os.environ.get("PATH", "")])
        except ImportError:
            pass
    else:
        for d in ("/opt/homebrew/bin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin",
                  os.path.expanduser("~/.linuxbrew/bin")):
            if os.path.isdir(d) and d not in os.environ.get("PATH", "").split(":"):
                os.environ["PATH"] += ":" + d
        lb = os.path.expanduser("~/.local/bin")
        if os.path.islink(os.path.join(lb, "pwsh")) and not os.environ.get("PATH", "").startswith(lb + ":"):
            os.environ["PATH"] = lb + ":" + os.environ.get("PATH", "")   # prefer azup's PowerShell


def resolve_exe(cmd):
    exe = shutil.which(cmd[0])
    return [exe] + list(cmd[1:]) if exe else list(cmd)


def run(cmd, timeout=60):
    try:
        p = subprocess.run(resolve_exe(cmd), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def ps_exe():
    """PowerShell 7 if present, else Windows PowerShell 5.1."""
    for c in ("pwsh", "powershell") if IS_WIN else ("pwsh",):
        if have(c):
            return c
    return None


def ps_cmd(body):
    return [ps_exe() or "pwsh", "-NoProfile", "-NonInteractive", "-Command",
            "$ProgressPreference='SilentlyContinue';" + body]


def ps_json(script):
    if not ps_exe():
        return []
    rc, out = run(ps_cmd(script), 120)
    if rc or not out:
        return []
    try:
        d = json.loads(out)
    except ValueError:
        return []
    return d if isinstance(d, list) else [d]


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


# ───────────────────────────── detection ─────────────────────────────
@dataclass
class State:
    installed: bool = False
    version: str = ""
    managed: bool = True      # installed through the backend azup uses
    latest: str = ""

    @property
    def outdated(self):
        return bool(self.installed and self.latest and self.version
                    and vtuple(self.latest) > vtuple(self.version))


def winget_state(pid):
    rc, out = run(["winget", "list", "--id", pid, "-e", "--accept-source-agreements"], 60)
    for ln in out.splitlines():
        tk = ln.split()
        if pid in tk:
            j = tk.index(pid) + 1
            while j < len(tk) and tk[j] in (">", "<", "~"):
                j += 1
            if j >= len(tk):
                return None
            avail = tk[j + 1] if j + 1 < len(tk) and tk[j + 1] not in ("winget", "msstore") else ""
            return tk[j].lstrip("><~"), avail
    return None


# ── Linux PowerShell: Microsoft's official release tarball, installed to ~/.local (no sudo) ──
def pwsh_link():
    return os.path.expanduser("~/.local/bin/pwsh")


def linux_arch():
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64",
            "armv7l": "arm32"}.get(platform.machine().lower())
    if arch and glob.glob("/lib/ld-musl-*") and arch == "x64":
        return "linux-musl-x64"                      # Alpine
    return f"linux-{arch}" if arch else None


def gh_json(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "azup"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {tok}"        # only ever sent to api.github.com
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def tarball_state(repo, check_latest):
    link = pwsh_link()
    if not (os.path.islink(link) and os.path.exists(link)):
        return None
    m = re.search(r"/powershell/(\d+(?:\.\d+)+)/", os.readlink(link))
    if not m:
        return None
    latest = ""
    if check_latest:
        try:
            latest = gh_json(f"https://api.github.com/repos/{repo}/releases/latest")["tag_name"].lstrip("v")
        except Exception:
            pass
    return m.group(1), latest


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "azup"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        total, got = int(r.headers.get("Content-Length") or 0), 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if TTY and total and not is_quiet():
                sys.stdout.write(f"\r    {progress(got, total)} {got * 100 // total:3d}%")
                sys.stdout.flush()
    if TTY and not is_quiet():
        sys.stdout.write("\r\x1b[K")


def tarball_install(t, verb, dry):
    arch = linux_arch()
    if not arch:
        print(red(f"    unsupported CPU architecture: {platform.machine()}"))
        return False
    print("    " + dim(f"$ download latest {t.ghtar} {arch} tarball -> ~/.local/share/powershell/<version>"))
    if dry:
        return True
    try:
        rel = gh_json(f"https://api.github.com/repos/{t.ghtar}/releases/latest")
        ver = rel["tag_name"].lstrip("v")
        name = f"powershell-{ver}-{arch}.tar.gz"
        assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
        if name not in assets or "hashes.sha256" not in assets:
            raise RuntimeError(f"release {ver} has no {name} (or no checksum file)")
        base = os.path.expanduser("~/.local/share/powershell")
        dest = os.path.join(base, ver)
        if verb == "update" and os.path.isfile(os.path.join(dest, "pwsh")):
            print(f"    {green('✔')} {ver} is already the latest")
            return True
        os.makedirs(base, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            tgz = os.path.join(tmp, name)
            download(assets[name], tgz)
            req = urllib.request.Request(assets["hashes.sha256"], headers={"User-Agent": "azup"})
            raw = urllib.request.urlopen(req, timeout=30).read()
            sums = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
            expected = next((l.split()[0] for l in sums.splitlines() if l.strip().endswith(name)), None)
            h = hashlib.sha256()
            with open(tgz, "rb") as f:
                for blk in iter(lambda: f.read(1 << 20), b""):
                    h.update(blk)
            if not expected or h.hexdigest().lower() != expected.lower():
                raise RuntimeError("checksum mismatch – refusing to install the download")
            print(f"    {green('✔')} sha256 verified")
            os.makedirs(dest, exist_ok=True)
            with tarfile.open(tgz) as tar:
                try:
                    tar.extractall(dest, filter="data")
                except TypeError:                    # Python < 3.12 without the filter argument
                    tar.extractall(dest)
        os.chmod(os.path.join(dest, "pwsh"), 0o755)
        link = pwsh_link()
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(os.path.join(dest, "pwsh"), link)
        for d in os.listdir(base):                   # drop older versions
            if d != ver and re.match(r"^\d+(\.\d+)+", d):
                shutil.rmtree(os.path.join(base, d), ignore_errors=True)
        refresh_path()
        print(f"    {green('✔')} PowerShell {ver} -> {link}")
        if os.path.dirname(link) not in os.environ.get("PATH", "").split(":"):
            print(yellow('    add to your shell profile:  export PATH="$HOME/.local/bin:$PATH"'))
        return True
    except Exception as e:
        print(red(f"    {e}"))
        return False



def detect(check_latest=True) -> Dict[str, State]:
    refresh_path()
    st = {t.key: State() for t in TOOLS}
    specs = {t.key: spec(t) for t in TOOLS}
    mods = [t for t in TOOLS if t.kind == "psmod"]
    names = ",".join(f"'{t.pkg}'" for t in mods)
    uses = lambda *m: any(s and s[0] in m for s in specs.values())

    def brew_list():
        if not (uses("brew", "cask") and have("brew")):
            return {}
        rc, out = run(["brew", "list", "--versions"])
        return {p[0]: p[-1] for p in (ln.split() for ln in out.splitlines()) if len(p) >= 2}

    def brew_out():
        if not (check_latest and uses("brew", "cask") and have("brew")):
            return {}
        rc, out = run(["brew", "outdated", "--json=v2"], 90)
        try:
            d = json.loads(out)
        except ValueError:
            return {}
        return {x["name"].split("/")[-1]: x["current_version"]
                for x in d.get("formulae", []) + d.get("casks", [])}

    def snap_state(pid):
        if not have("snap"):
            return None
        rc, out = run(["snap", "list", pid])
        for ln in out.splitlines()[1:]:
            p = ln.split()
            if p and p[0] == pid:
                return p[1], ""
        return None

    def ps_installed():
        if not mods:
            return {}
        s = (f"Get-InstalledModule -Name {names} -ErrorAction SilentlyContinue | "
             "Select-Object Name,@{n='Version';e={$_.Version.ToString()}} | ConvertTo-Json -Compress")
        res = {}
        for r in ps_json(s):
            n, v = r["Name"], r["Version"]
            if n not in res or vtuple(v) > vtuple(res[n]):
                res[n] = v
        return res

    def ps_latest(installed):
        if not (check_latest and installed):
            return {}
        n = ",".join(f"'{x}'" for x in installed)
        s = (f"Find-Module -Name {n} -ErrorAction SilentlyContinue | "
             "Select-Object Name,@{n='Version';e={$_.Version.ToString()}} | ConvertTo-Json -Compress")
        return {r["Name"]: r["Version"] for r in ps_json(s)}

    def az_exts():
        if not have("az"):
            return {}
        rc, out = run(["az", "extension", "list", "-o", "json"], 60)
        try:
            return {e["name"]: e["version"] for e in json.loads(out)}
        except ValueError:
            return {}

    def az_ext_latest(exts):
        if not (check_latest and exts):
            return {}
        rc, out = run(["az", "extension", "list-available", "-o", "json"], 90)
        try:
            return {e["name"]: e["version"] for e in json.loads(out) if e["name"] in exts}
        except ValueError:
            return {}

    def azd_exts():
        if not have("azd"):
            return {}
        rc, out = run(["azd", "extension", "list", "--output", "json"], 60)
        try:
            d = json.loads(out[out.index("["): out.rindex("]") + 1])
        except ValueError:
            return {}
        return {e["id"]: (e.get("installedVersion") or "", e.get("version") or "")
                for e in d if e.get("installedVersion")}

    def bicep_v():
        if not have("az"):
            return ""
        rc, out = run(["az", "bicep", "version"], 30)
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        return m.group(1) if rc == 0 and m else ""

    def bin_version(t):
        rc, out = run(list(t.vcmd) or [t.cmd, "--version"], 15)
        m = re.search(r"\d+(\.\d+)+", out)
        if m:
            return m.group(0)
        return "?" if rc == 0 else ""           # "" = found on PATH but broken

    with cf.ThreadPoolExecutor(12) as ex:
        f_bl, f_bo = ex.submit(brew_list), ex.submit(brew_out)
        f_pi, f_ae, f_bv = ex.submit(ps_installed), ex.submit(az_exts), ex.submit(bicep_v)
        f_ax = ex.submit(azd_exts)
        pm = {}
        for t in TOOLS:
            s = specs[t.key]
            if s and s[0] == "winget" and have("winget"):
                pm[t.key] = ex.submit(winget_state, s[1])
            elif s and s[0] == "snap":
                pm[t.key] = ex.submit(snap_state, s[1])
            elif s and s[0] == "tarball":
                pm[t.key] = ex.submit(tarball_state, s[1], check_latest)
        bl, bo, pi, ae, bv = (f.result() for f in (f_bl, f_bo, f_pi, f_ae, f_bv))
        ax = f_ax.result()
        f_pl, f_el = ex.submit(ps_latest, pi), ex.submit(az_ext_latest, ae)
        ext_f = {}
        for t in TOOLS:
            s = specs[t.key]
            if not s:
                continue
            if s[0] in ("brew", "cask"):
                managed = t.short in bl
            else:
                managed = bool(pm.get(t.key) and pm[t.key].result())
            if not managed and t.cmd and have(t.cmd):
                ext_f[t.key] = ex.submit(bin_version, t)
        pl, el = f_pl.result(), f_el.result()
        for k, f in ext_f.items():
            v = f.result()
            if v:                                   # a binary that does not even start counts as missing
                st[k].installed, st[k].managed, st[k].version = True, False, v

    for t in TOOLS:
        s, sp = st[t.key], specs[t.key]
        if t.kind == "pkg" and sp:
            if sp[0] in ("brew", "cask") and t.short in bl:
                s.installed, s.version = True, bl[t.short]
                s.latest = bo.get(t.short, bl[t.short]) if check_latest else ""
            elif sp[0] in ("winget", "snap", "tarball") and pm.get(t.key) and pm[t.key].result():
                v, avail = pm[t.key].result()
                s.installed, s.version = True, v
                s.latest = (avail or v) if (check_latest and sp[0] == "winget") else \
                    (avail if sp[0] == "tarball" else "")
        elif t.kind == "psmod" and t.pkg in pi:
            s.installed, s.version, s.latest = True, pi[t.pkg], pl.get(t.pkg, "")
        elif t.kind == "azext" and t.pkg in ae:
            s.installed, s.version, s.latest = True, ae[t.pkg], el.get(t.pkg, "")
        elif t.kind == "azdext" and t.pkg in ax:
            s.installed, s.version = True, ax[t.pkg][0]
            s.latest = ax[t.pkg][1] if check_latest else ""
        elif t.kind == "bicep" and bv:
            s.installed, s.version = True, bv
    return st


# ───────────────────────────── display ─────────────────────────────
def status_cell(s):
    if not s.installed:
        return dim("○ not installed")
    if not s.managed:
        return yellow("● external")
    if s.outdated:
        return yellow("▲ update available")
    return green("✔ up to date") if s.latest else cyan("● installed")


def print_table(st):
    w_name = max(len(t.name) for t in TOOLS) + 2
    last = None
    for t in TOOLS:
        if t.group != last:
            print("\n  " + bold(azure(t.group.upper())))
            last = t.group
        s = st[t.key]
        new = (" → " + green(s.latest)) if s.outdated else ""
        print(f"   {pad(bold(t.name), w_name)}{pad(dim(s.version or '–'), 12)}"
              f"{pad(status_cell(s), 22)}{new}")
    n_in = sum(s.installed for s in st.values())
    n_out = sum(s.outdated for s in st.values())
    print(f"\n  {green(str(n_in))} installed · {yellow(str(n_out))} outdated · "
          f"{dim(str(len(st) - n_in))} not installed\n")


# ───────────────────────────── what's new ─────────────────────────────
def ver_of(tag):
    m = re.search(r"\d+\.\d+(?:\.\d+)*", tag or "")
    return m.group(0) if m else ""


def notes_from_github(t, cur, new):
    rels = gh_json(f"https://api.github.com/repos/{t.repo}/releases?per_page=60")
    keep = []
    for r in rels:
        if r.get("draft") or r.get("prerelease"):
            continue
        if t.tag_re and not re.search(t.tag_re, r["tag_name"]):
            continue
        v = ver_of(r["tag_name"])
        if not v or vtuple(v) <= vtuple(cur) or (new and vtuple(v) > vtuple(new)):
            continue
        keep.append((vtuple(v), v, r))
    keep.sort(key=lambda x: x[0], reverse=True)
    return {"items": [{"version": v, "date": (r.get("published_at") or "")[:10],
                       "body": r.get("body") or "", "url": r["html_url"]} for _, v, r in keep],
            "page": f"https://github.com/{t.repo}/releases"}


def notes_from_history(t, cur, new):
    """az extensions keep a HISTORY.rst per extension in Azure/azure-cli-extensions."""
    base = f"Azure/azure-cli-extensions/main/src/{t.pkg}/HISTORY.rst"
    req = urllib.request.Request("https://raw.githubusercontent.com/" + base, headers={"User-Agent": "azup"})
    text = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    parts = re.split(r"^(\d+\.\d+\.\d+\S*)\n[+~=\-]{3,}[ \t]*$", text, flags=re.M)
    items = []
    for i in range(1, len(parts) - 1, 2):
        v = parts[i]
        if vtuple(v) > vtuple(cur) and (not new or vtuple(v) <= vtuple(new)):
            items.append({"version": v, "date": "", "body": parts[i + 1].strip(),
                          "url": f"https://github.com/Azure/azure-cli-extensions/blob/main/src/{t.pkg}/HISTORY.rst"})
    return {"items": items, "page": f"https://github.com/Azure/azure-cli-extensions/blob/main/src/{t.pkg}/HISTORY.rst"}


def notes_from_markdown(t, cur, new):
    req = urllib.request.Request(t.notes_url, headers={"User-Agent": "azup"})
    text = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    parts = re.split(r"^## ([^\n]+)\n+Version (\d+(?:\.\d+)*)[^\n]*\n", text, flags=re.M)
    items = []
    for i in range(1, len(parts) - 2, 3):
        date, v, body = parts[i], parts[i + 1], parts[i + 2]
        if vtuple(v) > vtuple(cur) and (not new or vtuple(v) <= vtuple(new)):
            items.append((vtuple(v), {"version": v, "date": date, "body": body.strip(),
                                      "url": "https://learn.microsoft.com/cli/azure/release-notes-azure-cli"}))
    items.sort(key=lambda x: x[0], reverse=True)
    return {"items": [x[1] for x in items], "page": "https://learn.microsoft.com/cli/azure/release-notes-azure-cli"}


def fetch_notes(t, cur, new):
    """Release notes for versions newer than `cur` (up to `new`). Never raises."""
    try:
        if t.kind == "azext":
            return notes_from_history(t, cur, new)
        if t.notes_url:
            return notes_from_markdown(t, cur, new)
        if not t.repo:
            return {"error": "no release-notes source known for this tool"}
        return notes_from_github(t, cur, new)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return {"error": "GitHub rate limit reached – set GITHUB_TOKEN to raise it"}
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": f"couldn't fetch release notes ({e.__class__.__name__}) – offline?"}


SKIP_HEADINGS = re.compile(r"docker|install|download|checksum|asset|release notes?$", re.I)
SKIP_LINES = re.compile(r"^\s*(gallery module|to install|to update from|full changelog)", re.I)


def render_notes(body, width, max_lines):
    body = re.sub(r"```.*?```", "", body, flags=re.S)                 # install snippets etc.
    out, skipping = [], False
    for raw in body.splitlines():
        l = re.sub(r"<!--.*?-->|<[^>]+>|!\[[^\]]*\]\([^)]*\)", "", raw).rstrip()
        l = re.sub(r"\[(\[[^\]]+\])\]\([^)]*\)", r"\1", l)        # [[#123]](url) -> [#123]
        l = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", l)              # [text](url)  -> text
        l = re.sub(r"\[(#\d+)\]", r"\1", l).replace("**", "").replace("`", "")
        if not l.strip():
            continue
        head = re.match(r"^\s*#{1,6}\s*(.*)", l)
        if head:
            skipping = bool(SKIP_HEADINGS.search(head.group(1).strip()))
            if skipping:
                continue
        elif skipping or SKIP_LINES.match(l):
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.*)", l)
        if head:
            out.append(bold(head.group(1).strip()))
        else:
            txt = bullet.group(1) if bullet else l.strip()
            for k, w in enumerate(textwrap.wrap(txt, max(30, width - 6)) or [""]):
                out.append(("• " if bullet and k == 0 else "  " if bullet else "") + w)
    more = max(0, len(out) - max_lines)
    return out[:max_lines], more


def print_changes(tools, st, per_tool=4, lines=12, latest_only=False):
    """Print what's new for each tool, between the installed and the latest version."""
    if not tools:
        return
    width = min(shutil.get_terminal_size((100, 24)).columns, 110) - 4
    with Spinner("reading release notes…"):
        with cf.ThreadPoolExecutor(6) as ex:
            futs = {t.key: ex.submit(fetch_notes, t, "0" if latest_only else st[t.key].version,
                                     "" if latest_only else st[t.key].latest) for t in tools}
            res = {k: f.result() for k, f in futs.items()}
    for t in tools:
        s, r = st[t.key], res[t.key]
        move = f"{s.version} → {s.latest}" if s.outdated else (s.version or "")
        print(f"\n  {azure('▶')} {bold(t.name)}  {yellow(move) if s.outdated else dim(move)}")
        if r.get("error") or not r.get("items"):
            print(f"    {dim(r.get('error') or 'no release notes found')}"
                  + (f"  {dim(r['page'])}" if r.get("page") else ""))
            continue
        items = r["items"][:1] if latest_only else r["items"]
        for it in items[:per_tool]:
            print(f"    {bold(green('v' + it['version']))} {dim(it['date'])}")
            body, more = render_notes(it["body"], width, lines)
            for l in body:
                print("      " + l)
            if more:
                print(dim(f"      … +{more} more lines  {it['url']}"))
        left = len(items) - per_tool
        if left > 0:
            print(dim(f"    … and {left} older release{'s' if left != 1 else ''}: {r['page']}"))
    print()


def proceed(actions, st, yes=False):
    """'Proceed?' that also offers 'c' = see what's new for the updates in the plan."""
    if yes:
        return True
    if not (TTY and sys.stdin.isatty()):
        return False
    ups = [t for t, v in actions if v == "update"]
    while True:
        a = input(f"  Proceed? {dim('[Y/n/c]  c = see what' + chr(39) + 's new first') if ups else dim('[Y/n]')} ").strip().lower()
        if a in ("", "y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        if a == "c" and ups:
            print_changes(ups, st)


# ───────────────────────────── execution ─────────────────────────────
# ───────────────────────────── parallel engine ─────────────────────────────
# Work is split into "lanes", one per backend, that run side by side:
#   brew (formulas are batched into ONE `brew` call so Homebrew downloads in parallel itself)
#   ps-az / ps-graph (PowerShell modules; Az and Graph share no dependencies, Graph/Beta/Entra do)
#   az (extensions + Bicep) · azd (extensions) · winget · tarball
# Inside a lane steps run one after another. A step that needs another tool (modules need PowerShell,
# `az` extensions need Azure CLI...) waits for it, and fails cleanly if that tool failed.
QUIET = threading.local()          # set in worker threads: no prompts, stdin closed, output captured
PREREQ = {"psmod": "pwsh", "azext": "az", "bicep": "az", "azdext": "azd"}
LANE_LABEL = {"brew": "brew", "ps-az": "pwsh", "ps-graph": "pwsh", "az": "az", "azd": "azd",
              "winget": "winget", "tarball": "tarball", "snap": "snap"}
_PS_PRE_LOCK, _PS_PRE_DONE = threading.Lock(), [False]
_SHOWN: set = set()


def is_quiet():
    return getattr(QUIET, "on", False)


def lane_of(t: Tool):
    if t.kind == "pkg":
        return {"cask": "brew"}.get(spec(t)[0], spec(t)[0])
    if t.kind == "psmod":
        return "ps-az" if t.pkg == "Az" or t.pkg.startswith("Az.") else "ps-graph"
    return {"azext": "az", "bicep": "az", "azdext": "azd"}[t.kind]


class JobBuf:
    """Captures one job's output; remembers the last meaningful line for the status board."""
    def __init__(self):
        self.lines, self.cur, self.saved, self.last = [], "", "", ""

    def write(self, x):
        for ch in x:
            if ch == "\r":
                self.saved, self.cur = self.cur, ""
            elif ch == "\n":
                line = self.cur or self.saved
                self.lines.append(line)
                if line.strip():
                    self.last = ANSI.sub("", line).strip()
                self.cur = self.saved = ""
            else:
                self.cur += ch
                self.saved = ""
        if self.cur.strip():
            self.last = ANSI.sub("", self.cur).strip()
        return len(x)

    def flush(self):
        pass

    def text(self):
        return "\n".join(self.lines + ([self.cur] if self.cur else []))


class Router:
    """sys.stdout replacement: each worker thread writes into its own job buffer."""
    def __init__(self, real):
        self.real, self.bufs = real, {}

    def attach(self, buf):
        self.bufs[threading.get_ident()] = buf

    def write(self, x):
        b = self.bufs.get(threading.get_ident())
        return self.real.write(x) if b is None else b.write(x)

    def flush(self):
        self.real.flush()

    def isatty(self):
        return False

    def reconfigure(self, **kw):
        pass


class Job:
    def __init__(self, tool, verb):
        self.tool, self.verb, self.lane = tool, verb, lane_of(tool)
        self.state, self.note, self.ok = "queued", "", None
        self.t0 = self.t1 = 0.0
        self.buf = JobBuf()
        self.wait_for = ""

    def took(self):
        return (self.t1 or time.time()) - self.t0 if self.t0 else 0.0


class Board:
    """Live status board: one line per task, updated in place."""
    def __init__(self, jobs, real, title):
        self.jobs, self.real, self.title = jobs, real, title
        self.stop_ev, self.H = threading.Event(), len(jobs) + 2
        self.t = threading.Thread(target=self._run, daemon=True)

    def frame(self):
        n = len(self.jobs)
        cols = shutil.get_terminal_size((100, 24)).columns
        spin = Spinner.FR[int(time.time() * 12) % 10]
        done = sum(j.state in ("ok", "fail") for j in self.jobs)
        run = sum(j.state == "running" for j in self.jobs)
        out = [f"  {azure('»')} {bold(self.title)}  {dim(f'{run} running · {done}/{n} done')}"]
        avail = max(10, cols - 52)
        for j in self.jobs:
            if j.state == "queued":
                icon, hint = dim("○"), dim("queued")
            elif j.state == "waiting":
                icon, hint = yellow("◌"), yellow(f"waiting for {j.wait_for}")
            elif j.state == "running":
                icon, hint = cyan(spin), dim(clip(j.buf.last, avail))
            elif j.state == "ok":
                icon, hint = green("✔"), dim("done")
            else:
                icon, hint = red("✘"), red(clip(j.note or "failed", avail))
            t = f"{j.took():4.0f}s" if j.t0 else "     "
            out.append(f"   {icon} {pad(bold(j.tool.name) if j.state == 'running' else j.tool.name, 24)}"
                       f"{pad(dim(LANE_LABEL.get(j.lane, j.lane)), 9)}{dim(t)}  {hint}")
        out.append(f"  {progress(done, n)} {dim(f'{done}/{n}')}")
        return out

    def draw(self, first):
        if not first:
            self.real.write(f"\x1b[{self.H}A")
        self.real.write("".join("\x1b[K" + l + "\n" for l in self.frame()))
        self.real.flush()

    def _run(self):
        first = True
        while not self.stop_ev.is_set():
            self.draw(first)
            first = False
            time.sleep(0.1)
        self.draw(first)

    def start(self):
        self.real.write("\x1b[?25l")
        self.t.start()

    def stop(self):
        self.stop_ev.set()
        self.t.join()
        self.real.write("\x1b[?25h")
        self.real.flush()


def ps_preflight():
    """Trust PSGallery once, before concurrent module installs (two writers would race on that file)."""
    with _PS_PRE_LOCK:
        if not _PS_PRE_DONE[0]:
            subprocess.run(resolve_exe(ps_cmd("Set-PSRepository PSGallery -InstallationPolicy Trusted "
                                              "-ErrorAction SilentlyContinue")),
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            _PS_PRE_DONE[0] = True


def run_one(t: Tool, verb, dry):
    """Run a single install/update with its pre-checks. Returns (ok, note)."""
    req = requires(t)
    refresh_path()
    if not dry and not has_requirement(req):
        print(f"\n  {red('✘')} {t.name}: needs {bold(req)} – install it first")
        return False, f"needs {req} – install it first"
    if not dry and t.kind == "azdext" and t.min_azd:
        rc_, out_ = run(["azd", "version"], 20)
        m_ = re.search(r"(\d+\.\d+\.\d+)", out_)
        have_v = m_.group(1) if m_ else "0"
        if vtuple(have_v) < vtuple(t.min_azd):
            note = f"needs azd >= {t.min_azd} (you have {have_v}) – update azd first"
            print(f"\n  {red('✘')} {t.name}: {note}")
            return False, note
    if not dry and t.kind == "psmod":
        works, info = ps_works()
        if not works:
            if "ps" not in _SHOWN:
                _SHOWN.add("ps")
                explain_broken_powershell(info)
            return False, "PowerShell does not start"
        if is_quiet():
            ps_preflight()
    if t.kind == "pkg" and spec(t)[0] == "tarball":
        ok = tarball_install(t, verb, dry)
    else:
        ok = stream(build_cmd(t, verb), dry)
    if t.key == "pwsh":
        _PS_CHECK.clear()                     # re-test the freshly installed PowerShell
    return ok, ""


def run_sequential(actions, dry):
    results = []
    for i, (t, verb) in enumerate(actions, 1):
        label = "Installing" if verb == "install" else "Updating"
        print(f"\n  {progress(i - 1, len(actions))} {dim(f'{i}/{len(actions)}')}  "
              f"{azure('▶')} {bold(f'{label} {t.name}')}")
        t0 = time.time()
        ok, note = run_one(t, verb, dry)
        results.append((t, verb, ok, time.time() - t0, note))
    return results


def run_parallel(actions, jobs, yes=False):
    real = sys.stdout
    jl = [Job(t, v) for t, v in actions]
    by_key = {j.tool.key: j for j in jl}
    done_ev = {j.tool.key: threading.Event() for j in jl}
    emit_lock = threading.Lock()
    interactive = TTY and sys.stdin.isatty()

    def finish(j, ok, note=""):
        j.ok, j.note, j.t1 = ok, note, time.time()
        j.state = "ok" if ok else "fail"
        done_ev[j.tool.key].set()
        if not use_board:                                    # plain/log mode: one block per finished job
            with emit_lock:
                real.write(f"\n  {green('✔') if ok else red('✘')} {bold(j.tool.name)}  "
                           f"{dim(LANE_LABEL.get(j.lane, j.lane) + ' · ' + format(j.took(), '.0f') + 's')}"
                           + (f"  {red(note)}" if note else "") + "\n")
                for l in j.buf.text().splitlines():
                    real.write(dim("      " + l) + "\n")
                real.flush()

    # 1) things that may ask for a password (macOS casks) run first, one at a time, with a real terminal
    first = [j for j in jl if j.tool.kind == "pkg" and spec(j.tool)[0] == "cask"] if interactive else []
    for j in first:
        use_board = False
        print(f"\n  {azure('▶')} {bold(('Installing ' if j.verb == 'install' else 'Updating ') + j.tool.name)}  {dim('(may ask for your password)')}")
        j.t0, j.state = time.time(), "running"
        ok, note = run_one(j.tool, j.verb, False)
        j.ok, j.note, j.t1, j.state = ok, note, time.time(), "ok" if ok else "fail"
        done_ev[j.tool.key].set()
    rest = [j for j in jl if j not in first]
    if not rest:
        return [(j.tool, j.verb, j.ok, j.took(), j.note) for j in jl]

    use_board = interactive and TTY and len(rest) + 3 <= shutil.get_terminal_size((100, 24)).lines
    lanes = {}
    for j in rest:
        lanes.setdefault(j.lane, []).append(j)
    for lane in lanes.values():                              # prerequisites first, so dependents unblock early
        lane.sort(key=lambda j: 0 if j.tool.key in PREREQ.values() else 1)
    router = Router(real)

    def wait_prereq(j):
        need = PREREQ.get(j.tool.kind)
        if need and need in done_ev and need != j.tool.key:
            j.state, j.wait_for = "waiting", {"pwsh": "PowerShell", "az": "Azure CLI", "azd": "azd"}[need]
            done_ev[need].wait()
            if by_key[need].ok is False:
                return f"needs {j.wait_for} – it failed"
        return ""

    def run_job(j, shared_buf=None):
        if shared_buf is not None:
            j.buf = shared_buf
        router.attach(j.buf)
        why = wait_prereq(j)
        j.t0, j.state = time.time(), "running"
        if why:
            finish(j, False, why)
            return
        try:
            ok, note = run_one(j.tool, j.verb, False)
        except Exception as e:                               # never let a lane die silently
            ok, note = False, f"{e.__class__.__name__}: {e}"
        finish(j, ok, note)

    def brew_batch(batch):
        """One `brew install a b c` for several formulas; if it fails, redo them one by one."""
        shared = JobBuf()
        router.attach(shared)
        for j in batch:
            j.buf, j.t0, j.state = shared, time.time(), "running"
        verb = batch[0].verb
        cmd = ["brew", "install" if verb == "install" else "upgrade"] + [spec(j.tool)[1] for j in batch]
        try:
            ok = stream(cmd, False)
        except Exception:
            ok = False
        if ok:
            for j in batch[1:]:
                j.buf = JobBuf()
                j.buf.write("(installed in the same brew batch)\n")
            for j in batch:
                finish(j, True)
            return
        print(f"\n  {yellow('▲')} batch failed – retrying one by one")
        for j in batch:
            j.buf = JobBuf()
            run_job(j)

    def lane_worker(name, js):
        QUIET.on = True
        try:
            steps, cur = [], []
            if name == "brew":                               # batch plain formulas per verb, prerequisites first
                for grp in ([j for j in js if j.tool.key in PREREQ.values()], [j for j in js if j.tool.key not in PREREQ.values()]):
                    for v in ("install", "update"):
                        g = [j for j in grp if j.verb == v and spec(j.tool)[0] == "brew"]
                        if len(g) >= 2:
                            steps.append(g)
                        else:
                            steps += [[j] for j in g]
                steps += [[j] for j in js if spec(j.tool)[0] == "cask"]
            else:
                steps = [[j] for j in js]
            for step in steps:
                if len(step) > 1:
                    brew_batch(step)
                else:
                    run_job(step[0])
        finally:
            for j in js:                                     # unblock dependents whatever happens
                if j.ok is None:
                    finish(j, False, "interrupted")

    title = f"{len(jl)} tasks · {len(lanes)} lanes in parallel"
    board = Board(jl, real, title) if use_board else None
    if not use_board:
        real.write(f"\n  {azure('»')} {bold(title)}\n")
    sys.stdout = router
    if board:
        board.start()
    try:
        order = sorted(lanes, key=lambda n: any(PREREQ.get(j.tool.kind) for j in lanes[n]))   # independent lanes first
        with cf.ThreadPoolExecutor(max_workers=max(1, min(jobs, len(lanes)))) as ex:
            futs = [ex.submit(lane_worker, n, lanes[n]) for n in order]
            for f in futs:
                f.result()
    finally:
        sys.stdout = real
        if board:
            board.stop()
    # 2) anything that needs you (Homebrew asking to trust a tap) is handled now, one at a time
    for j in jl:
        txt = j.buf.text()
        if j.ok is False and "untrusted tap" in txt and interactive and trust_tap(txt):
            print(f"\n  {azure('▶')} {bold('Retrying ' + j.tool.name)}")
            t0 = time.time()
            ok, note = run_one(j.tool, j.verb, False)
            j.ok, j.note, j.t1, j.t0, j.state = ok, note, time.time(), t0, "ok" if ok else "fail"
    if use_board:                                            # show why things failed
        for j in jl:
            if j.ok is False and j.buf.lines:
                print(f"\n  {red('✘')} {bold(j.tool.name)} {dim('– last output')}")
                for l in j.buf.text().splitlines()[-25:]:
                    print(dim("      " + l))
    return [(j.tool, j.verb, bool(j.ok), j.took(), j.note) for j in jl]


def resolve_jobs(jobs):
    if jobs:
        return max(1, int(jobs))
    env = os.environ.get("AZUP_JOBS", "")
    return max(1, int(env)) if env.isdigit() else 4


def can_parallel(actions):
    lanes = {lane_of(t) for t, _ in actions}
    brews = sum(1 for t, _ in actions if t.kind == "pkg" and spec(t)[0] == "brew")
    return len(actions) >= 2 and (len(lanes) >= 2 or brews >= 2)



def build_cmd(t: Tool, verb: str):
    inst = verb == "install"
    if t.kind == "pkg":
        mgr, pid = spec(t)
        if mgr == "brew":
            return ["brew", "install" if inst else "upgrade", pid]
        if mgr == "cask":
            return ["brew", "install" if inst else "upgrade", "--cask", pid]
        if mgr == "winget":
            return ["winget", "install" if inst else "upgrade", "--id", pid, "-e",
                    "--accept-package-agreements", "--accept-source-agreements"]
        return ["sudo", "snap", "install" if inst else "refresh", pid] + \
               (["--classic"] if inst else [])
    if t.kind == "psmod":
        if inst:
            return ps_cmd(("" if _PS_PRE_DONE[0] else
                           "Set-PSRepository PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue;")
                          + f"Install-Module {t.pkg} -Scope CurrentUser -Force -AllowClobber "
                          "-ErrorAction Stop")
        return ps_cmd(f"Update-Module {t.pkg} -Force -ErrorAction Stop")
    if t.kind == "azext":
        return ["az", "extension", "add", "-n", t.pkg, "--upgrade", "-y"]
    if t.kind == "azdext":
        return ["azd", "extension", "install" if inst else "update", t.pkg]
    return ["az", "bicep", "install" if inst else "upgrade"]


def requires(t: Tool):
    if t.kind == "pkg":
        return "" if spec(t)[0] == "tarball" else spec(t)[0]     # tarball needs no package manager
    return {"psmod": "powershell", "azdext": "azd"}.get(t.kind, "az")


_PS_CHECK = {}


def ps_works(refresh=False):
    """Smoke-test PowerShell: (ok, output). A broken pwsh would otherwise fail every module step."""
    exe = ps_exe()
    if not exe:
        return False, "PowerShell was not found on PATH."
    if refresh or exe not in _PS_CHECK:
        try:
            p = subprocess.run(resolve_exe([exe, "-NoProfile", "-NonInteractive", "-Command", "exit 0"]),
                               capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace")
            _PS_CHECK[exe] = (p.returncode == 0, (p.stdout + p.stderr).strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            _PS_CHECK[exe] = (False, str(e))
    return _PS_CHECK[exe]


def explain_broken_powershell(info):
    exe = ps_exe() or "pwsh"
    lines = [l for l in info.splitlines() if l.strip()]
    shown = "\n".join("      " + l for l in (lines[:4] + (["      …"] if len(lines) > 4 else [])))
    print(f"\n  {red('✘')} PowerShell is installed but does not start, so the modules can't be installed.")
    if shown:
        print(dim(shown))
    print("    Check it by hand:  " + bold(f"{exe} -NoProfile -Command '$PSVersionTable.PSVersion'"))
    if not IS_WIN:
        print("    Useful details:    " + bold("which -a pwsh; echo $TERM $HOME; ls -ld ~ ."))
        print("    azup's Linux PowerShell build needs ICU: " + bold("sudo apt install -y libicu-dev")
              + dim("  (Fedora/RHEL: sudo dnf install -y libicu)"))
        print("    Other ways to install PowerShell: "
              "https://learn.microsoft.com/powershell/scripting/install/install-other-linux")
    print("    Fix it, then re-run azup – it will pick up where it left off.")


def has_requirement(req):
    if not req:
        return True
    if req == "powershell":
        return ps_exe() is not None
    return have({"cask": "brew"}.get(req, req))


def confirm(q, yes=False):
    if yes:
        return True
    if not (TTY and sys.stdin.isatty()):
        return False
    return input(f"  {q} {dim('[Y/n]')} ").strip().lower() in ("", "y", "yes")


def ensure_manager(mgr, dry, yes):
    mgr = {"cask": "brew"}.get(mgr, mgr)
    if have(mgr):
        return True
    if mgr == "brew" and not IS_WIN:
        print(yellow("  Homebrew is not installed. azup uses it for CLI tools on "
                     + OS_NAME + "."))
        if dry:
            print(dim("    would run the official Homebrew installer"))
            return True
        if not confirm("Install Homebrew now?", yes):
            return False
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            print(red("  Homebrew refuses to install as root. Run azup as a normal user that has sudo."))
            return False
        if not have("sudo"):
            print(red("  Homebrew needs sudo to create its install directory, and sudo is missing."))
            print("    Install sudo (or ask an admin), then re-run azup.")
            return False
        # The installer runs non-interactively, so authenticate sudo here where it can prompt.
        print(dim("  Homebrew needs administrator rights to create its directory – enter your password:"))
        if subprocess.call(["sudo", "-v"]) != 0:
            print(red("  sudo authentication failed."))
            return False
        rc = subprocess.call(["/bin/bash", "-c", 'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL '
                              'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'])
        refresh_path()
        if rc != 0 or not have("brew"):
            if IS_LINUX:
                print(yellow("  Homebrew on Linux needs build tools. On Ubuntu/Debian:\n"
                             "    sudo apt update && sudo apt install -y build-essential procps curl file git"))
            return False
        return True
    if mgr == "winget" and IS_WIN and not dry:
        # Fresh Windows installs often have App Installer but haven't registered it for this user yet.
        print(dim("  winget not found – trying to register 'App Installer'…"))
        subprocess.call(["powershell", "-NoProfile", "-Command",
                         "Add-AppxPackage -RegisterByFamilyName -MainPackage "
                         "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        refresh_path()
        if have("winget"):
            return True
    hints = {"winget": "winget ships with Windows 10/11 – update 'App Installer' in the Microsoft Store "
                       "(or https://aka.ms/getwinget), then re-run azup.",
             "snap": "snap is not available – install PowerShell via "
                     "https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux"}
    print(red(f"  '{mgr}' not found. ") + hints.get(mgr, ""))
    return False


def stream(cmd, dry):
    print("    " + dim("$ " + " ".join(cmd)))
    if dry:
        return True
    try:
        if cmd[0] == "brew" or SCHEDULED or is_quiet():
            rc, out = run_tee(cmd)
            if rc != 0 and "untrusted tap" in out and not is_quiet() and trust_tap(out):
                rc, out = run_tee(cmd)        # retry once after trusting
            if rc == 0 and "not linked" in out:
                # e.g. the first attempt installed the keg but died before linking it
                for m in re.finditer(r"brew link (\S+)", out):
                    print("    " + dim(f"$ brew link {m.group(1)}"))
                    rc, _ = run_tee(["brew", "link", m.group(1)])
            return rc == 0
        return subprocess.call(resolve_exe(cmd)) == 0
    except OSError as e:
        print(red(f"    {e}"))
        return False


def run_tee(cmd):
    """Run a command, echo its output live and also return it."""
    p = subprocess.Popen(resolve_exe(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL if is_quiet() else None,
                         text=True, encoding="utf-8", errors="replace")
    buf = []
    for line in p.stdout:
        sys.stdout.write(line)
        buf.append(line)
    p.wait()
    return p.returncode, "".join(buf)


TRUST_HINT = re.compile(r"`brew trust ((?:--\w+ )?[\w./@+-]+)`")


def trust_tap(output):
    """Homebrew refuses taps it does not trust. Show the fix; apply it if the user agrees."""
    m = TRUST_HINT.search(output)
    if not m:
        return False
    args = m.group(1).split()
    cmd = ["brew", "trust"] + args
    print(f"\n  {yellow('▲')} Homebrew refuses to load from a tap you haven't trusted "
          f"({bold(args[-1])}).")
    print(dim("    Trusting lets Homebrew run that tap's formulae/casks. Only do this for sources you rely on."))
    if not confirm(f"Run `{' '.join(cmd)}` and retry?"):
        print(f"    To fix it yourself run:  {bold(' '.join(cmd))}")
        return False
    return subprocess.call(resolve_exe(cmd)) == 0


Action = Tuple[Tool, str]


def execute(actions: List[Action], dry=False, yes=False, jobs=None):
    if not actions:
        print(f"  {green('✔')} nothing to do.\n")
        return True
    rank = {"pkg": 0, "bicep": 2, "psmod": 1, "azext": 2, "azdext": 2}
    actions = sorted(actions, key=lambda a: rank[a[0].kind])
    for mgr in sorted({requires(t) for t, _ in actions if t.kind == "pkg"} - {""}):
        if not ensure_manager(mgr, dry, yes):
            print(red(f"  cannot continue without {mgr}.\n"))
            return False
    if any(v == "update" and t.kind == "pkg" and spec(t)[0] in ("brew", "cask")
           for t, v in actions):
        print(f"\n  {azure('▶')} {bold('Refreshing Homebrew index')}")
        stream(["brew", "update"], dry)
    _SHOWN.clear()
    n_jobs = resolve_jobs(jobs)
    if not dry and n_jobs > 1 and can_parallel(actions):
        results = run_parallel(actions, n_jobs, yes)
    else:
        results = run_sequential(actions, dry)
    print("\n  " + bold("Summary"))
    for t, verb, ok, dt, note in results:
        print(f"   {green('✔') if ok else red('✘')} {pad(t.name, 24)}{pad(dim(verb), 10)}"
              f"{dim(f'{dt:4.0f}s')}  {red(note) if note else ''}")
    bad = [r for r in results if not r[2]]
    LAST_RESULTS[:] = results
    if not dry:
        cache_forget({r[0].key for r in results if r[2]})
    print(f"\n  {progress(len(results) - len(bad), len(results))} "
          f"{dim(f'{len(results) - len(bad)}/{len(results)}')}\n")
    if SCHEDULED:
        print(f"{len(results) - len(bad)} ok, {len(bad)} failed")
    elif not dry:
        if bad:
            say(random.choice(OOPS), "sad")
            print(f"\n  {red(str(len(bad)))} failed – scroll up for the error output.\n")
        else:
            confetti()
            say(random.choice(CHEERS))
            print()
    elif bad:
        print(f"  {red(str(len(bad)))} failed.\n")
    return not bad


# ───────────────────────────── keyboard menus ─────────────────────────────
def read_key():
    if IS_WIN:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": b"\x1b[A", "P": b"\x1b[B"}.get(msvcrt.getwch(), b"")
        return {"\r": b"\r"}.get(ch, ch.encode())
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b" and select.select([fd], [], [], 0.05)[0]:
            ch += os.read(fd, 2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def interactive_ok():
    return TTY and sys.stdin.isatty() and (IS_WIN or termios is not None)


Row = Tuple[str, str, bool]   # label, hint, is_header


def pick(title, rows: List[Row], multi=False, preselect=(), extra: Optional[Dict[bytes, Tuple[str, Callable]]] = None,
         start=0):
    """Arrow-key menu.  Returns an index (single), a sorted list (multi) or None if cancelled."""
    sel = set(preselect)
    n = len(rows)
    sel_idx = [i for i, r in enumerate(rows) if not r[2]]
    lw = min(36, max([24] + [plain_len(r[0]) + 2 for r in rows if not r[2]]))     # label column width
    cur = start if start in sel_idx else sel_idx[0]
    extra = extra or {}
    keys = ["space toggle", "a all", "n none"] if multi else []
    keys += [f"{k.decode()} {v[0]}" for k, v in extra.items()]
    help_ = "↑/↓ move · " + " · ".join(keys + (["enter confirm"] if multi else ["enter select"]) + ["q cancel"])

    def draw(first):
        if not first:
            sys.stdout.write(f"\x1b[{n + 2}A")
        out = [f"\x1b[K  {bold(title)}"]
        for i, (lab, hint, head) in enumerate(rows):
            if head:
                out.append(f"\x1b[K  {bold(azure(lab))}")
                continue
            ptr = azure("❯") if i == cur else " "
            box = (green("◉ ") if i in sel else dim("○ ")) if multi else ""
            out.append(f"\x1b[K   {ptr} {box}{pad(bold(lab) if i == cur else lab, lw)} {hint}")
        out.append(f"\x1b[K  {dim(help_)}")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    sys.stdout.write("\x1b[?25l")
    try:
        draw(True)
        while True:
            k = read_key()
            pos = sel_idx.index(cur)
            if k == b"\x03":
                raise KeyboardInterrupt
            if k in (b"q", b"\x1b"):
                return None
            if k in (b"\x1b[A", b"k"):
                cur = sel_idx[(pos - 1) % len(sel_idx)]
            elif k in (b"\x1b[B", b"j"):
                cur = sel_idx[(pos + 1) % len(sel_idx)]
            elif k == b" " and multi:
                sel ^= {cur}
            elif k == b"a" and multi:
                sel = set(sel_idx)
            elif k == b"n" and multi:
                sel = set()
            elif k in extra:
                sel = set(extra[k][1]())
            elif k in (b"\r", b"\n"):
                return sorted(sel) if multi else cur
            draw(False)
    finally:
        sys.stdout.write("\x1b[?25h")


def pause():
    if interactive_ok():
        input(dim("  press enter to continue…"))


# ───────────────────────────── commands ─────────────────────────────
def load(check=True):
    with Spinner("Inspecting your toolbox…", mascot=True):
        return detect(check)


def resolve(names):
    out = []
    for n in names:
        t = (next((x for x in TOOLS if n in (x.key, x.pkg)), None)              # exact case: az vs Az
             or (next((x for x in TOOLS if x.key == ALIASES[n.lower()]), None) if n.lower() in ALIASES else None)
             or BY_KEY.get(n.lower())
             or next((x for x in TOOLS if x.name.lower() == n.lower()), None))
        if not t:
            sys.exit(red(f"  unknown tool '{n}'. Known: ") + ", ".join(x.key for x in TOOLS))
        out.append(t)
    return out


def hint_for(s: State):
    if not s.installed:
        return dim("not installed")
    if not s.managed:
        return yellow(f"{s.version} · external (not managed)")
    if s.outdated:
        return yellow(f"{s.version} → {s.latest}")
    return green(f"{s.version} ✔")


def select_flow(st=None, dry=False, yes=False, jobs=None):
    """One checklist for everything: missing -> install, outdated -> update."""
    if not interactive_ok():
        print(red("  the checklist needs an interactive terminal."))
        return False
    st = st or load(True)
    rows: List[Row] = []
    idx: List[Optional[Tool]] = []
    last = None
    for t in TOOLS:
        if t.group != last:
            rows.append((t.group.upper(), "", True))
            idx.append(None)
            last = t.group
        rows.append((t.name, hint_for(st[t.key]) + dim("  " + t.desc), False))
        idx.append(t)
    pick_where = lambda f: [i for i, t in enumerate(idx) if t and f(st[t.key], t)]
    sel = pick("Select components to install / update", rows, True,
               pick_where(lambda s, t: s.outdated),
               {b"u": ("outdated", lambda: pick_where(lambda s, t: s.outdated)),
                b"m": ("missing", lambda: pick_where(lambda s, t: not s.installed)),
                b"c": ("core", lambda: pick_where(lambda s, t: t.core)),
                b"i": ("ai", lambda: pick_where(lambda s, t: t.core or t.ai))})
    if not sel:
        return True
    actions: List[Action] = []
    notes = []
    for i in sel:
        t, s = idx[i], st[idx[i].key]
        if not s.installed:
            actions.append((t, "install"))
        elif not s.managed:
            notes.append(f"{t.name}: installed outside azup – update it with its own installer")
        elif s.outdated or not s.latest:
            actions.append((t, "update"))
        else:
            notes.append(f"{t.name}: already up to date")
    print("\n  " + bold("Plan"))
    for t, v in actions:
        print(f"   {green('+') if v == 'install' else yellow('↑')} {v:8}{t.name}")
    for n in notes:
        print(f"   {dim('·')} {dim(n)}")
    if not actions:
        print()
        return True
    print()
    if not proceed(actions, st, yes):
        print("  cancelled.\n")
        return True
    return execute(actions, dry, yes, jobs)


def cmd_status(a):
    banner()
    print_table(load(not a.offline))
    return 0


def cmd_select(a):
    return 0 if select_flow(dry=a.dry_run, yes=a.yes, jobs=a.jobs) else 1


def cmd_install(a):
    if not (a.names or a.all or a.profile):
        return cmd_select(a)
    st = load(False)
    tools = [t for t in TOOLS if t.core] if a.profile == "core" else \
        [t for t in TOOLS if t.core or t.ai] if a.profile == "ai" else \
        list(TOOLS) if (a.all or a.profile == "full") else resolve(a.names)
    todo = [(t, "install") for t in tools if not st[t.key].installed]
    skipped = [t.name for t in tools if st[t.key].installed]
    if skipped:
        print(dim("  already installed: " + ", ".join(skipped)))
    return 0 if execute(todo, a.dry_run, a.yes, a.jobs) else 1


def cmd_update(a):
    if getattr(a, "scheduled", False):
        return scheduled_update(a)
    st = load(True)
    tools = [t for t in resolve(a.names) if st[t.key].installed] if a.names \
        else [t for t in TOOLS if st[t.key].outdated]
    ext = [t.name for t in tools if not st[t.key].managed]
    if ext:
        print(yellow("  skipping (not managed by azup): " + ", ".join(ext)))
    acts = [(t, "update") for t in tools if st[t.key].managed]
    if getattr(a, "changes", False) and acts:
        print_changes([t for t, _ in acts], st)
        if not proceed([], st, a.yes):
            print("  cancelled.\n")
            return 0
    return 0 if execute(acts, a.dry_run, a.yes, a.jobs) else 1


def cmd_changes(a):
    st = load(True)
    if a.names:
        tools = [t for t in resolve(a.names) if st[t.key].installed]
        quiet = [t for t in tools if not st[t.key].outdated]
        tools = [t for t in tools if st[t.key].outdated]
    else:
        tools = [t for t in TOOLS if st[t.key].outdated]
        quiet = []
    if not tools and not quiet:
        print(f"  {green('✔')} nothing is outdated – nothing new to read.\n")
        return 0
    print_changes(tools, st)
    if quiet:
        print(dim("  already on the latest version – showing the most recent release:"))
        print_changes(quiet, st, latest_only=True)
    return 0


def cmd_login(a):
    pw = ps_exe() or "pwsh"
    tid = getattr(a, "tenant", None)
    steps = {"az": ["az", "login"] + (["--tenant", tid] if tid else []),
             "azd": ["azd", "auth", "login"] + (["--tenant-id", tid] if tid else []),
             "ps": [pw, "-NoProfile", "-Command", "Connect-AzAccount" + (f" -Tenant '{tid}'" if tid else "")],
             "graph": [pw, "-NoProfile", "-Command",
                       "Connect-MgGraph " + (f"-TenantId '{tid}' " if tid else "") +
                       "-Scopes 'User.Read.All','Group.Read.All','Directory.Read.All'"]}
    for k in (list(steps) if a.target == "all" else [a.target]):
        if not have(steps[k][0]):
            print(yellow(f"  {steps[k][0]} not installed – skipping {k}"))
            continue
        print(f"\n  {azure('▶')} {bold('Sign in: ' + k)}")
        stream(steps[k], False)
    return 0


def cmd_doctor(a):
    refresh_path()
    banner()
    ok = lambda m: print(f"   {green('✔')} {m}")
    warn = lambda m: print(f"   {yellow('▲')} {m}")
    bad = lambda m: print(f"   {red('✘')} {m}")
    print("  " + bold("Environment"))
    mgr = "winget" if IS_WIN else "brew"
    ok(f"{HOST_NAME[mgr]} found") if have(mgr) else bad(
        f"{HOST_NAME[mgr]} missing – azup offers to install it" if not IS_WIN else "winget missing")
    ps = ps_exe()
    if ps:
        rc, v = run([ps, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        good = vtuple(v) >= (7, 4)
        (ok if good else warn)(f"{ps} {v}" + ("" if good else "  (PowerShell 7.4+ recommended → azup install pwsh)"))
    else:
        bad("PowerShell missing  →  azup install pwsh")
    print("\n  " + bold("Sign-in"))
    if have("az"):
        with Spinner("checking az account…"):
            rc, out = run(["az", "account", "show", "-o", "json"], 30)
        if rc == 0:
            d = json.loads(out)
            ok(f"az: {d.get('user', {}).get('name', '?')} · {d['name']} · tenant {d['tenantId'][:8]}…")
        else:
            warn("az not signed in  →  azup login az")
    else:
        bad("Azure CLI missing  →  azup install az")
    if have("azd"):
        rc, _ = run(["azd", "auth", "login", "--check-status"], 20)
        ok("azd signed in") if rc == 0 else warn("azd not signed in  →  azup login azd")
    if ps:
        with Spinner("checking PowerShell sessions…"):
            rc, out = run([ps, "-NoProfile", "-Command",
                           "$a='';try{$a=(Get-AzContext).Account.Id}catch{};"
                           "$g='';try{$g=(Get-MgContext).Account}catch{};$a+'|'+$g"], 60)
        a_, _, g_ = out.partition("|")
        ok(f"Az PowerShell: {a_}") if a_ else warn("Az PowerShell not connected  →  azup login ps")
        ok(f"Graph: {g_}") if g_ else warn("Graph not connected  →  azup login graph")
    st = load(True)
    print("\n  " + bold("Toolbox"))
    old = [t.name for t in TOOLS if st[t.key].outdated]
    warn(f"{len(old)} outdated: {', '.join(old)}  →  azup update") if old else ok(
        "everything installed is up to date")
    print()
    return 0


# ───────────────────────────── identity: whoami / ctx ─────────────────────────────
def clip(text, w):
    return text if len(text) <= w else text[: w - 1] + "…"


def gather_context():
    """Who/where each tool is signed in as. Returns {tool: dict | None (not installed)}."""
    def az_ctx():
        if not have("az"):
            return None
        rc, out = run(["az", "account", "show", "-o", "json"], 30)
        if rc:
            return {"in": False}
        d = json.loads(out)
        return {"in": True, "account": d.get("user", {}).get("name", "?"), "sub": d["name"],
                "sub_id": d["id"], "tenant": d["tenantId"]}

    def azd_ctx():
        if not have("azd"):
            return None
        rc, out = run(["azd", "auth", "login", "--check-status"], 20)
        m = re.search(r"as (\S+)", out)
        sub_id = ""
        rc2, o2 = run(["azd", "config", "get", "defaults.subscription"], 15)
        if rc2 == 0 and o2 and not o2.lstrip().startswith("{"):
            sub_id = o2.strip().strip('"')
        return {"in": rc == 0, "account": m.group(1) if m else "", "sub_id": sub_id}

    def ps_ctx():
        if not ps_exe():
            return None, None
        body = ("$r=@{az=$null;mg=$null};"
                "try{$c=Get-AzContext;if($c){$r.az=@{account=$c.Account.Id;sub=$c.Subscription.Name;"
                "sub_id=$c.Subscription.Id;tenant=$c.Tenant.Id}}}catch{};"
                "try{$g=Get-MgContext;if($g){$r.mg=@{account=$g.Account;tenant=$g.TenantId}}}catch{};"
                "$r|ConvertTo-Json -Compress -Depth 4")
        rc, out = run(ps_cmd(body), 90)
        try:
            d = json.loads(out)
        except ValueError:
            d = {}
        z, g = d.get("az"), d.get("mg")
        return ({"in": bool(z), **(z or {})}, {"in": bool(g), **(g or {})})

    with cf.ThreadPoolExecutor(3) as ex:
        fa, fd, fp = ex.submit(az_ctx), ex.submit(azd_ctx), ex.submit(ps_ctx)
        res = {"az": fa.result(), "azd": fd.result()}
        res["ps"], res["graph"] = fp.result()
    return res


def cmd_whoami(a):
    refresh_path()
    banner()
    with Spinner("asking every tool who you are…", mascot=True):
        c = gather_context()
    rows = [("Azure CLI", "az", "az login"), ("Azure Developer CLI", "azd", "azd auth login"),
            ("Az PowerShell", "ps", "azup login ps"), ("Microsoft Graph", "graph", "azup login graph")]
    print("  " + bold("Who am I?") + "\n")
    for label, key, fix in rows:
        d = c.get(key)
        if d is None:
            print(f"   {pad(bold(label), 24)}{dim('○ not installed')}")
        elif not d.get("in"):
            print(f"   {pad(bold(label), 24)}{yellow('▲ not signed in')}  {dim('→ ' + fix)}")
        else:
            bits = [d.get("account", "")]
            if d.get("sub"):
                bits.append(f"{clip(d['sub'], 34)} ({d['sub_id'][:8]})")
            elif d.get("sub_id"):
                bits.append(f"default sub {d['sub_id'][:8]}")
            if d.get("tenant"):
                bits.append(f"tenant {d['tenant'][:8]}")
            print(f"   {pad(bold(label), 24)}{green('✔')} {dim(' · ').join(x for x in bits if x)}")
    az, ps, g = c.get("az") or {}, c.get("ps") or {}, c.get("graph") or {}
    drift = []
    if az.get("in") and ps.get("in") and az["sub_id"] != ps.get("sub_id"):
        drift.append(f"Az PowerShell is on '{clip(ps.get('sub', '?'), 30)}', az is on '{clip(az['sub'], 30)}'")
    if az.get("in") and g.get("in") and az["tenant"] != g.get("tenant"):
        drift.append("Microsoft Graph is signed in to a different tenant than az")
    print()
    if drift:
        for d in drift:
            print(f"   {yellow('▲')} {d}")
        print(dim("     fix it with:  azup ctx\n"))
    elif az.get("in"):
        print(f"   {green('✔')} everything points at the same place\n")
    return 0


def pick_filter(title, items, current=None, page=12):
    """Type-to-filter list. items = [(label, hint, value)]. Returns a value or None."""
    if not interactive_ok():
        return None
    q, first, H = "", True, page + 3

    def matches():
        words = q.lower().split()
        return [i for i, (l, h, v) in enumerate(items) if all(w in (l + " " + h).lower() for w in words)]

    m = matches()
    cur = next((j for j, i in enumerate(m) if items[i][2] == current), 0)
    sys.stdout.write("\x1b[?25l")
    try:
        while True:
            if not first:
                sys.stdout.write(f"\x1b[{H}A")
            first = False
            start = max(0, min(cur - page // 2, len(m) - page)) if len(m) > page else 0
            lines = [f"\x1b[K  {bold(title)}",
                     f"\x1b[K  {azure('›')} {q}{dim('▏')}   {dim(f'{len(m)}/{len(items)}')}"]
            for r in range(page):
                j = start + r
                if j < len(m):
                    lab, hint, _ = items[m[j]]
                    ptr = azure("❯") if j == cur else " "
                    lines.append(f"\x1b[K   {ptr} {pad(bold(clip(lab, 40)) if j == cur else clip(lab, 40), 42)} {dim(hint)}")
                else:
                    lines.append("\x1b[K")
            lines.append(f"\x1b[K  {dim('type to filter · ↑/↓ move · enter select · esc cancel')}")
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            k = read_key()
            if k == b"\x03":
                raise KeyboardInterrupt
            if k == b"\x1b":
                return None
            if k == b"\x1b[A":
                cur = max(0, cur - 1)
            elif k == b"\x1b[B":
                cur = min(max(len(m) - 1, 0), cur + 1)
            elif k in (b"\r", b"\n"):
                if m:
                    return items[m[cur]][2]
            elif k in (b"\x7f", b"\x08"):
                q, m, cur = q[:-1], None, 0
                m = matches()
            elif len(k) == 1 and 32 <= k[0] < 127:
                q += k.decode()
                m, cur = matches(), 0
    finally:
        sys.stdout.write(f"\x1b[{H}A\x1b[J\x1b[?25h")


def set_context(sub):
    """Point az, azd and Az PowerShell at one subscription. Graph is tenant-scoped: hint only."""
    sid, tid, name = sub["id"], sub["tenantId"], sub["name"]
    print(f"\n  {azure('▶')} {bold('Switching to ' + clip(name, 50))}  {dim(sid)}")
    ok = True
    rc, _ = run(["az", "account", "set", "--subscription", sid], 30)
    print(f"   {green('✔') if rc == 0 else red('✘')} Azure CLI")
    ok &= rc == 0
    if have("azd"):
        rc, _ = run(["azd", "config", "set", "defaults.subscription", sid], 20)
        print(f"   {green('✔') if rc == 0 else yellow('▲')} azd default subscription")
    if ps_exe():
        body = ("try{$c=Get-AzContext;if($c){Set-AzContext -SubscriptionId '%s' -TenantId '%s' "
                "-ErrorAction Stop|Out-Null;'ok'}else{'none'}}catch{'fail'}" % (sid, tid))
        with Spinner("switching Az PowerShell…"):
            rc, out = run(ps_cmd(body), 90)
        res = out.strip().splitlines()[-1] if out.strip() else "fail"
        if res == "ok":
            print(f"   {green('✔')} Az PowerShell")
        elif res == "none":
            print(f"   {dim('·')} Az PowerShell {dim('(not signed in – azup login ps)')}")
        else:
            print(f"   {yellow('▲')} Az PowerShell {dim('(could not switch – try: azup login ps --tenant ' + tid + ')')}")
    print(f"   {dim('·')} Microsoft Graph {dim('is tenant-scoped – if you changed tenant: azup login graph --tenant ' + tid)}\n")
    return ok


def cmd_ctx(a):
    if not have("az"):
        print(red("  Azure CLI is not installed  →  azup install az"))
        return 1
    with Spinner("loading your subscriptions…", mascot=True):
        rc, out = run(["az", "account", "list", "--all", "-o", "json"], 90)
    try:
        subs = json.loads(out)
    except ValueError:
        subs = []
    if rc or not subs:
        print(yellow("  no subscriptions found – sign in first:  azup login az"))
        return 1
    if not getattr(a, "all", False):
        subs = [x for x in subs if x.get("state") == "Enabled"]      # disabled ones can't be used
    subs.sort(key=lambda x: x["name"].lower())
    cur = next((x["id"] for x in subs if x.get("isDefault")), None)
    if a.list:
        for x in subs:
            print(f"  {green('●') if x['id'] == cur else ' '} {pad(clip(x['name'], 44), 46)}{dim(x['id'])}  "
                  f"{dim(x.get('tenantDisplayName') or x['tenantId'][:8])}")
        return 0
    if a.query:
        q = a.query.lower()
        hit = [x for x in subs if q == x["id"].lower() or q in x["name"].lower()]
        exact = [x for x in hit if q == x["id"].lower() or q == x["name"].lower()]
        hit = exact or hit
        if len(hit) != 1:
            print(red(f"  '{a.query}' matches {len(hit)} subscriptions") + (":" if hit else "."))
            for x in hit[:15]:
                print(f"    {x['name']}  {dim(x['id'])}")
            return 1
        return 0 if set_context(hit[0]) else 1
    items = [(x["name"], f"{x['id'][:8]} · {x.get('tenantDisplayName') or x['tenantId'][:8]}"
              + ("" if x.get("state") == "Enabled" else f" · {x.get('state')}"), x) for x in subs]
    chosen = pick_filter(f"Switch subscription ({len(subs)})", items, current=next((x for x in subs if x['id'] == cur), None))
    if not chosen:
        print("  cancelled.")
        return 0
    return 0 if set_context(chosen) else 1



def cmd_self_update(a):
    here = os.path.abspath(__file__)
    low = os.path.realpath(here).lower().replace("\\", "/")
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable).lower().replace("\\", "/")
        hint = "winget upgrade saeid-adz.azup" if "/winget/" in exe else "download the latest azup.exe from the GitHub releases page"
        print(yellow(f"  this azup.exe is a packaged build – update it with:  {hint}"))
        return 0
    if "/cellar/azup/" in low:                       # azup's own Homebrew keg, not just Homebrew's Python
        print(yellow("  azup was installed with Homebrew – run:  brew upgrade azup"))
        return 0
    if "site-packages" in low or "dist-packages" in low:
        print(yellow("  azup was installed with pip/pipx – run:  pipx upgrade azup-cli"))
        return 0
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/azup.py"
    try:
        with Spinner("checking GitHub…"):
            data = urllib.request.urlopen(url, timeout=20).read().decode("utf-8")
    except Exception as e:
        print(red(f"  could not download {url}: {e}"))
        return 1
    m = re.search(r'^VERSION = "([^"]+)"', data, re.M)
    if not m or "def main" not in data:
        print(red("  downloaded file does not look like azup – not replacing."))
        return 1
    if vtuple(m.group(1)) <= vtuple(VERSION):
        print(f"  {green('✔')} azup {VERSION} is the latest.")
        return 0
    with open(here, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    print(f"  {green('✔')} updated azup {VERSION} → {m.group(1)}")
    return 0


# ───────────────────────────── nudge & scheduled updates ─────────────────────────────
AZUP_DIR = os.path.expanduser("~/.azup")
SCHEDULED = False                 # True while `azup update --scheduled` runs unattended
LAST_RESULTS: list = []           # (tool, verb, ok, seconds, note) of the last execute()
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SCHED_LABEL, TASK_NAME = "io.github.azup.update", "azup-update"


def _p(*parts):
    return os.path.join(AZUP_DIR, *parts)


def jload(name, default=None):
    try:
        with open(_p(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(name, obj):
    try:
        os.makedirs(AZUP_DIR, exist_ok=True)
        tmp = _p(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, _p(name))
    except OSError:
        pass


def cfg_get():
    return jload("config.json") or {}


def cfg_set(section, **kw):
    c = cfg_get()
    c.setdefault(section, {}).update(kw)
    jsave("config.json", c)


def parse_dur(txt):
    m = re.fullmatch(r"(\d+)\s*([hdw])", (txt or "").strip().lower())
    return int(m.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[m.group(2)] if m else None


def ago(ts):
    d = max(0, time.time() - ts)
    return "just now" if d < 90 else f"{int(d // 60)}m ago" if d < 5400 else \
        f"{int(d // 3600)}h ago" if d < 172800 else f"{int(d // 86400)}d ago"


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, x):
        for s in self.streams:
            s.write(x)
        return len(x)

    def flush(self):
        for s in self.streams:
            s.flush()

    def isatty(self):
        return False

    def reconfigure(self, **kw):
        pass


# ── cache of "what is outdated", refreshed in the background ──
def cache_write(st):
    out = [{"key": t.key, "name": t.name, "version": st[t.key].version, "latest": st[t.key].latest}
           for t in TOOLS if st[t.key].outdated and st[t.key].managed]
    jsave("state.json", {"checked": time.time(), "refreshing": 0, "outdated": out})


def cache_forget(keys):
    s = jload("state.json")
    if s and keys:
        s["outdated"] = [o for o in s.get("outdated", []) if o["key"] not in keys]
        jsave("state.json", s)


def spawn_refresh():
    s = jload("state.json") or {}
    if time.time() - s.get("refreshing", 0) < 600:            # one refresh at a time
        return
    s["refreshing"] = time.time()
    jsave("state.json", s)
    kw = {"creationflags": 0x00000008 | 0x08000000} if IS_WIN else {"start_new_session": True}
    env = None
    if getattr(sys, "frozen", False):
        # one-file PyInstaller: give the detached child its own extraction dir, since the parent
        # deletes its own on exit
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        subprocess.Popen(self_cmd() + ["nudge", "refresh"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, env=env, **kw)
    except OSError:
        pass


def nudge_show():
    """Called from your shell profile: at most one short line, from the cache. Never slow, never noisy."""
    if not TTY:
        return
    now = time.time()
    cfg = cfg_get().get("nudge", {})
    st = jload("state.json") or {}
    if now - st.get("checked", 0) > 12 * 3600:
        spawn_refresh()
    lr = jload("last-run.json")
    if lr and not lr.get("seen") and (lr.get("updated") or lr.get("failed")):
        bits = []
        if lr.get("updated"):
            bits.append(green(f"updated {len(lr['updated'])}") + dim(f" ({', '.join(lr['updated'][:3])}"
                        + (f" +{len(lr['updated']) - 3}" if len(lr["updated"]) > 3 else "") + ")"))
        if lr.get("failed"):
            bits.append(red(f"{len(lr['failed'])} failed") + dim(f" ({', '.join(lr['failed'][:3])})"))
        print(f"  {azure('▟▙')} {bold('azup')} auto-update {ago(lr.get('at', now))}: " + " · ".join(bits)
              + dim(f"  log: {lr.get('log', '')}"))
        lr["seen"] = True
        jsave("last-run.json", lr)
    out = st.get("outdated") or []
    if out and now >= cfg.get("snooze_until", 0) and now - cfg.get("last_shown", 0) >= cfg.get("every_h", 24) * 3600:
        names = [o["name"] for o in out]
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        print(f"  {azure('▟▙')} {bold('azup')} {yellow(f'{len(out)} tool' + ('s' if len(out) != 1 else '') + ' outdated')} "
              f"{dim('(' + shown + ')')} → {bold('azup update')} {dim('· azup changes · azup nudge snooze 7d')}")
        cfg["last_shown"] = now
        cfg_set("nudge", **cfg)


# ── shell hooks (a clearly marked block you can remove with `azup nudge disable`) ──
NUDGE_START, NUDGE_END = "# >>> azup nudge >>>", "# <<< azup nudge <<<"
SH_BODY = "command -v azup >/dev/null 2>&1 && azup nudge"
PS_BODY = "if (Get-Command azup -ErrorAction SilentlyContinue) { azup nudge }"


def ps_profile_paths():
    out = []
    for exe in (("pwsh", "powershell") if IS_WIN else ("pwsh",)):
        if have(exe):
            rc, o = run([exe, "-NoProfile", "-Command", "$PROFILE.CurrentUserCurrentHost"], 30)
            if rc == 0 and o.strip():
                out.append((exe, o.strip().splitlines()[-1]))
    return out


def shell_targets(which=None):
    t = []
    sh = "" if IS_WIN else os.path.basename(os.environ.get("SHELL", ""))
    if which == "zsh" or (which is None and sh == "zsh"):
        t.append(("zsh", os.path.expanduser("~/.zshrc"), SH_BODY))
    if which == "bash" or (which is None and sh == "bash"):
        t.append(("bash", os.path.expanduser("~/.bash_profile" if IS_MAC else "~/.bashrc"), SH_BODY))
    if which in (None, "pwsh"):
        t += [(exe, path, PS_BODY) for exe, path in ps_profile_paths()]
    return t


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def add_block(path, body):
    try:
        text = read_text(path)
    except OSError:
        text = ""
    if NUDGE_START in text:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("\n" if text and not text.endswith("\n") else "") + f"{NUDGE_START}\n{body}\n{NUDGE_END}\n")
    return True


def remove_block(path):
    try:
        text = read_text(path)
    except OSError:
        return False
    new = re.sub(rf"\n?{re.escape(NUDGE_START)}.*?{re.escape(NUDGE_END)}\n?", "\n", text, flags=re.S)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True


def nudge_enable(a):
    targets = shell_targets(getattr(a, "shell", None))
    if not targets:
        print(yellow("  couldn't tell which shell you use. Pick one:  azup nudge enable --shell zsh|bash|pwsh"))
        return 1
    print(f"\n  {bold('Terminal nudge')} – when you open a terminal azup prints one short line if tools are\n"
          f"  outdated (at most once a day). It reads a small cache that a background job refreshes with the\n"
          f"  same package-manager lookups as `azup status`. Nothing is installed or changed automatically.\n")
    for label, path, body in targets:
        print(f"   {azure('•')} {label}: {path}")
    print()
    if not confirm("Add the one-line hook to these files?", getattr(a, "yes", False)):
        print("  cancelled.\n")
        return 0
    for label, path, body in targets:
        print(f"   {green('✔') if add_block(path, body) else dim('·')} {label}")
    cfg_set("nudge", enabled=True)
    spawn_refresh()
    print(f"\n  Done. Open a new terminal. Remove it anytime with {bold('azup nudge disable')}.\n")
    return 0


def nudge_disable(a):
    paths = {os.path.expanduser(p) for p in ("~/.zshrc", "~/.bashrc", "~/.bash_profile", "~/.profile")}
    paths |= {path for _, path in ps_profile_paths()}
    gone = [p for p in sorted(paths) if remove_block(p)]
    cfg_set("nudge", enabled=False)
    for p in gone:
        print(f"   {green('✔')} removed from {p}")
    print(f"  {green('✔')} nudge is off.\n")
    return 0


def nudge_status(a):
    cfg, st = cfg_get().get("nudge", {}), jload("state.json") or {}
    hooked = [p for _, p, _ in shell_targets() if NUDGE_START in (read_text(p) if os.path.exists(p) else "")]
    print(f"\n  {bold('Terminal nudge')}: {green('on') if cfg.get('enabled') else dim('off')}"
          + (f"  {dim('in ' + ', '.join(hooked))}" if hooked else ""))
    if st.get("checked"):
        n = len(st.get("outdated") or [])
        print(f"  last check: {ago(st['checked'])} · {n} outdated")
    else:
        print(dim("  no check yet (a background refresh runs when you open a terminal)"))
    if cfg.get("snooze_until", 0) > time.time():
        print(f"  snoozed for another {int((cfg['snooze_until'] - time.time()) // 3600)}h")
    print()
    return 0


def cmd_nudge(a):
    act = a.action
    try:
        if act == "show":
            nudge_show()
        elif act == "refresh":
            cache_write(detect(True))
        elif act == "enable":
            return nudge_enable(a)
        elif act == "disable":
            return nudge_disable(a)
        elif act == "status":
            return nudge_status(a)
        elif act == "snooze":
            secs = parse_dur(a.arg or "7d")
            if not secs:
                print(red("  use a duration like 12h, 7d or 2w"))
                return 1
            cfg_set("nudge", snooze_until=time.time() + secs)
            print(f"  {green('✔')} quiet for {a.arg or '7d'}")
    except Exception:
        if act != "show":
            raise
    return 0


# ── scheduled updates: launchd (macOS) · systemd or cron (Linux) · Task Scheduler (Windows) ──
def self_cmd():
    """Command that re-launches azup: the exe itself when frozen, else python + this script."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def sched_cmd():
    cmd = self_cmd()
    if IS_WIN and not getattr(sys, "frozen", False):
        pw = os.path.join(os.path.dirname(cmd[0]), "pythonw.exe")       # no console window flashing
        cmd[0] = pw if os.path.exists(pw) else cmd[0]
    return cmd + ["update", "--scheduled"]


def sched_path_env():
    return ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin",
                     os.path.expanduser("~/.local/bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


def parse_at(txt):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", txt or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("use --at HH:MM (24h), e.g. 09:00")
    return int(m.group(1)), int(m.group(2))


def launchd_plist(cmd, freq, day, hh, mm):
    cal = {"Hour": hh, "Minute": mm}
    if freq == "weekly":
        cal["Weekday"] = (DAYS.index(day) + 1) % 7                      # launchd: Sunday = 0
    return plistlib.dumps({"Label": SCHED_LABEL, "ProgramArguments": cmd, "StartCalendarInterval": cal,
                           "EnvironmentVariables": {"PATH": sched_path_env()},
                           "StandardOutPath": _p("logs", "launchd.log"), "StandardErrorPath": _p("logs", "launchd.log"),
                           "RunAtLoad": False, "ProcessType": "Background"}).decode()


def systemd_units(cmd, freq, day, hh, mm):
    service = ("[Unit]\nDescription=azup: update the Azure toolbox\n\n[Service]\nType=oneshot\n"
               f"Environment=PATH={sched_path_env()}\nExecStart={shlex.join(cmd)}\n")
    cal = (f"{day.capitalize()} " if freq == "weekly" else "") + f"*-*-* {hh:02d}:{mm:02d}:00"
    timer = (f"[Unit]\nDescription=azup scheduled update ({freq})\n\n[Timer]\nOnCalendar={cal}\n"
             "Persistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def cron_line(cmd, freq, day, hh, mm):
    dow = str((DAYS.index(day) + 1) % 7) if freq == "weekly" else "*"
    log = _p("logs", "cron.log")
    return (f"{mm} {hh} * * {dow} PATH={shlex.quote(sched_path_env())} {shlex.join(cmd)} "
            f">> {shlex.quote(log)} 2>&1  # {TASK_NAME}")


def schtasks_create(cmd, freq, day, hh, mm):
    args = ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "WEEKLY" if freq == "weekly" else "DAILY",
            "/ST", f"{hh:02d}:{mm:02d}", "/TR", subprocess.list2cmdline(cmd), "/F"]
    if freq == "weekly":
        args[6:6] = ["/D", day.upper()]
    return args


def systemd_ok():
    return have("systemctl") and run(["systemctl", "--user", "show-environment"], 15)[0] == 0


def sched_enable(freq, day, at, dry):
    hh, mm = parse_at(at)
    cmd = sched_cmd()
    os.makedirs(_p("logs"), exist_ok=True)

    def show(title, text):
        print(f"\n  {dim(title)}\n" + "\n".join("    " + l for l in text.rstrip().splitlines()))

    if IS_MAC:
        path = os.path.expanduser(f"~/Library/LaunchAgents/{SCHED_LABEL}.plist")
        text = launchd_plist(cmd, freq, day, hh, mm)
        if dry:
            show(f"would write {path}", text)
            return True
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        dom = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", f"{dom}/{SCHED_LABEL}"], 20)
        return run(["launchctl", "bootstrap", dom, path], 20)[0] == 0
    if IS_WIN:
        args = schtasks_create(cmd, freq, day, hh, mm)
        if dry:
            show("would run", " ".join(args))
            return True
        return run(args, 30)[0] == 0
    if systemd_ok():
        svc, tmr = systemd_units(cmd, freq, day, hh, mm)
        d = os.path.expanduser("~/.config/systemd/user")
        if dry:
            show(f"would write {d}/azup-update.service", svc)
            show(f"would write {d}/azup-update.timer", tmr)
            return True
        os.makedirs(d, exist_ok=True)
        write_text(os.path.join(d, "azup-update.service"), svc)
        write_text(os.path.join(d, "azup-update.timer"), tmr)
        run(["systemctl", "--user", "daemon-reload"], 20)
        return run(["systemctl", "--user", "enable", "--now", "azup-update.timer"], 20)[0] == 0
    if have("crontab"):
        line = cron_line(cmd, freq, day, hh, mm)
        if dry:
            show("would add to your crontab", line)
            return True
        rc, cur = run(["crontab", "-l"], 15)
        keep = [l for l in (cur.splitlines() if rc == 0 else []) if TASK_NAME not in l]
        p = subprocess.run(["crontab", "-"], input="\n".join(keep + [line]) + "\n", text=True)
        return p.returncode == 0
    print(red("  neither systemd (user) nor cron is available on this machine."))
    return False


def sched_disable():
    if IS_MAC:
        path = os.path.expanduser(f"~/Library/LaunchAgents/{SCHED_LABEL}.plist")
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{SCHED_LABEL}"], 20)
        if os.path.exists(path):
            os.remove(path)
        return True
    if IS_WIN:
        return run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], 30)[0] == 0
    d = os.path.expanduser("~/.config/systemd/user")
    if have("systemctl"):
        run(["systemctl", "--user", "disable", "--now", "azup-update.timer"], 20)
    for f in ("azup-update.service", "azup-update.timer"):
        if os.path.exists(os.path.join(d, f)):
            os.remove(os.path.join(d, f))
    if have("crontab"):
        rc, cur = run(["crontab", "-l"], 15)
        if rc == 0 and TASK_NAME in cur:
            subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if TASK_NAME not in l) + "\n", text=True)
    return True


def sched_active():
    if IS_MAC:
        return run(["launchctl", "print", f"gui/{os.getuid()}/{SCHED_LABEL}"], 20)[0] == 0
    if IS_WIN:
        return run(["schtasks", "/Query", "/TN", TASK_NAME], 30)[0] == 0
    if have("systemctl") and run(["systemctl", "--user", "is-enabled", "azup-update.timer"], 15)[0] == 0:
        return True
    return have("crontab") and TASK_NAME in run(["crontab", "-l"], 15)[1]


def cmd_schedule(a):
    act = a.action
    if act == "run":
        return cmd_update(argparse.Namespace(names=[], dry_run=False, yes=True, changes=False, scheduled=True))
    if act == "status":
        on, cfg = sched_active(), cfg_get().get("schedule", {})
        print(f"\n  {bold('Scheduled update')}: {green('on') if on else dim('off')}"
              + (f"  {dim(cfg.get('freq', '') + ' ' + (cfg.get('day', '') if cfg.get('freq') == 'weekly' else '') + ' ' + cfg.get('at', ''))}" if on and cfg else ""))
        lr = jload("last-run.json")
        if lr:
            print(f"  last run: {ago(lr['at'])} · {len(lr.get('updated', []))} updated, {len(lr.get('failed', []))} failed"
                  + (f", {len(lr.get('skipped', []))} skipped" if lr.get("skipped") else "") + f"  {dim(lr.get('log', ''))}")
        print()
        return 0
    if act == "disable":
        sched_disable()
        cfg_set("schedule", enabled=False)
        print(f"  {green('✔')} scheduled update is off.\n")
        return 0
    freq = "daily" if a.daily else "weekly"
    try:
        ok = sched_enable(freq, a.day, a.at, a.dry_run)
    except ValueError as e:
        print(red(f"  {e}"))
        return 1
    if not ok:
        print(red("  could not create the schedule."))
        return 1
    if a.dry_run:
        print()
        return 0
    cfg_set("schedule", enabled=True, freq=freq, day=a.day, at=a.at)
    print(f"\n  {green('✔')} azup will update your tools {freq}"
          + (f" on {a.day.capitalize()}s" if freq == "weekly" else "") + f" at {a.at}.")
    print(dim("    Unattended runs skip anything that needs a password (e.g. the macOS PowerShell cask) and write a log to "
              + _p("logs") + ".\n    Turn it off with: azup schedule disable\n"))
    return 0


def scheduled_update(a):
    """Unattended update: no prompts, no animation, skips anything that needs a password, logs to ~/.azup/logs."""
    global SCHEDULED
    SCHEDULED = True
    os.makedirs(_p("logs"), exist_ok=True)
    logp = _p("logs", time.strftime("update-%Y%m%d-%H%M%S.log"))
    lf = open(logp, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, lf)
    print(f"azup {VERSION} scheduled update · {time.ctime()}")
    st = detect(True)
    due = [t for t in TOOLS if st[t.key].outdated and st[t.key].managed]
    skipped = [t for t in due if t.kind == "pkg" and spec(t)[0] == "cask"]     # cask installers ask for a password
    todo = [t for t in due if t not in skipped]
    for t in skipped:
        print(f"skipping {t.name}: needs a password – run `azup update {t.key}` yourself")
    ok = execute([(t, "update") for t in todo], False, True) if todo else True
    res = list(LAST_RESULTS)
    jsave("last-run.json", {"at": time.time(), "log": logp, "seen": False,
                            "updated": [r[0].name for r in res if r[2]],
                            "failed": [r[0].name for r in res if not r[2]],
                            "skipped": [t.name for t in skipped]})
    lf.close()
    sys.stdout = sys.__stdout__
    logs = sorted(glob.glob(_p("logs", "update-*.log")))
    for old in logs[:-10]:                                  # keep the last 10 logs
        try:
            os.remove(old)
        except OSError:
            pass
    return 0 if ok else 1



def clippy_says(st):
    """Context-aware suggestion (message, index of the menu row to preselect)."""
    n_out = sum(s.outdated for s in st.values())
    miss = [t for t in TOOLS if t.core and not st[t.key].installed]
    if n_out:
        s_ = "s" if n_out != 1 else ""
        return (f"It looks like {n_out} tool{s_} {'are' if n_out != 1 else 'is'} outdated. "
                f"Would you like me to update {'them' if n_out != 1 else 'it'}?"), 1
    if miss:
        return (f"Your core toolbox is missing {len(miss)} piece{'s' if len(miss) != 1 else ''}. "
                f"Want me to install {'them' if len(miss) != 1 else 'it'}?"), 2
    return "Everything is up to date. Go deploy something!", 0


def cmd_joke(a):
    print()
    say(random.choice(JOKES), "think")
    print()
    return 0


def notifications_menu():
    while True:
        cfg = cfg_get()
        nudge_on, sc = cfg.get("nudge", {}).get("enabled"), cfg.get("schedule", {})
        sc_on = sc.get("enabled") and sched_active()
        rows = [("Terminal nudge", "ON – select to turn off" if nudge_on else "OFF – a one-line reminder when you open a terminal", False),
                ("Scheduled auto-update", (f"ON – {sc.get('freq')} {sc.get('at')} – select to turn off" if sc_on
                                           else "OFF – update automatically, weekly (Mon 09:00)"), False),
                ("Back", "", False)]
        c = pick("Notifications & auto-update", rows)
        if c is None or c == 2:
            return
        ns = argparse.Namespace(shell=None, yes=False, action="", daily=False, day="mon", at="09:00", dry_run=False)
        if c == 0:
            (nudge_disable if nudge_on else nudge_enable)(ns)
        else:
            if sc_on:
                ns.action = "disable"
            else:
                ns.action = "enable"
                if not confirm("Update your tools automatically every Monday at 09:00?"):
                    continue
            cmd_schedule(ns)
        pause()


def interactive():
    st = load(True)
    first = True
    while True:
        sys.stdout.write("\x1b[2J\x1b[H")
        banner(animate=first)
        first = False
        print_table(st)
        n_out = sum(s.outdated for s in st.values())
        msg, start = clippy_says(st)
        say(msg)
        print()
        menu = [("select", "Select components…", "tick what to install or update"),
                ("update", "Update everything", f"{n_out} outdated"),
                ("whatsnew", "What's new?", "release notes for what's outdated"),
                ("core", "Install core profile", "az, azd, bicep, PowerShell, Az, Graph, resource-graph"),
                ("doctor", "Doctor", "sign-in & health"),
                ("whoami", "Who am I?", "account + subscription in every tool"),
                ("ctx", "Switch subscription…", "az + azd + Az PowerShell together"),
                ("login", "Sign in…", "az / azd / PowerShell / Graph"),
                ("notify", "Notifications & auto-update…", "terminal nudge, scheduled updates"),
                ("selfupdate", "Update azup", "get the latest version of this tool"),
                ("refresh", "Refresh", "re-scan"),
                ("quit", "Quit", "")]
        ids = [m[0] for m in menu]
        c = pick("What now?", [(m[1], m[2], False) for m in menu], start=ids.index(["select", "update", "core"][start]))
        act = ids[c] if c is not None else "quit"
        if act == "quit":
            print()
            say("Bye! May all your deployments be green.")
            print()
            return 0
        if act == "select":
            select_flow(st)
        elif act == "update":
            acts = [(t, "update") for t in TOOLS if st[t.key].outdated and st[t.key].managed]
            if acts and proceed(acts, st):
                execute(acts)
            elif not acts:
                print(f"  {green('✔')} nothing to update.\n")
        elif act == "whatsnew":
            cmd_changes(argparse.Namespace(names=[]))
        elif act == "core":
            execute([(t, "install") for t in TOOLS if t.core and not st[t.key].installed])
        elif act == "doctor":
            cmd_doctor(None)
        elif act == "whoami":
            cmd_whoami(None)
        elif act == "ctx":
            cmd_ctx(argparse.Namespace(query=None, list=False))
        elif act == "login":
            s = pick("Sign in", [("Azure CLI", "az login", False), ("Azure Developer CLI", "azd auth login", False),
                                 ("Az PowerShell", "Connect-AzAccount", False), ("Microsoft Graph", "Connect-MgGraph", False)])
            if s is not None:
                cmd_login(argparse.Namespace(target=["az", "azd", "ps", "graph"][s]))
        elif act == "notify":
            notifications_menu()
            st = load(True)
            continue
        elif act == "selfupdate":
            cmd_self_update(None)
        if act != "refresh":
            pause()
        st = load(True)


def main():
    p = argparse.ArgumentParser(prog="azup", description="Install & update your Azure toolbox.")
    p.add_argument("-V", "--version", action="version", version=VERSION)
    p.add_argument("--no-anim", action="store_true", help="disable animations (or set AZUP_NO_ANIM=1)")
    sub = p.add_subparsers(dest="cmd")

    def flags(sp):
        sp.add_argument("-n", "--dry-run", action="store_true", help="show commands, change nothing")
        sp.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
        sp.add_argument("-j", "--jobs", type=int, metavar="N", default=None,
                        help="run up to N things in parallel (default 4; -j 1 = one at a time; env AZUP_JOBS)")

    s = sub.add_parser("status", aliases=["ls"], help="show installed / outdated")
    s.add_argument("--offline", action="store_true", help="skip latest-version lookup")
    s.set_defaults(fn=cmd_status)
    sl = sub.add_parser("select", aliases=["menu"], help="checklist to install/update components")
    flags(sl)
    sl.set_defaults(fn=cmd_select)
    i = sub.add_parser("install", aliases=["i"], help="install tools (no args = checklist)")
    i.add_argument("names", nargs="*")
    i.add_argument("--profile", choices=["core", "full", "ai"],
                   help="core | ai (core + Foundry / AI tooling) | full")
    i.add_argument("--all", action="store_true")
    flags(i)
    i.set_defaults(fn=cmd_install)
    u = sub.add_parser("update", aliases=["up"], help="update tools (default: all outdated)")
    u.add_argument("names", nargs="*")
    u.add_argument("--changes", action="store_true", help="show release notes first, then ask")
    u.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    flags(u)
    u.set_defaults(fn=cmd_update)
    nd = sub.add_parser("nudge", help="one-line 'tools are outdated' reminder when you open a terminal")
    nd.add_argument("action", nargs="?", default="show",
                    choices=["show", "enable", "disable", "status", "snooze", "refresh"])
    nd.add_argument("arg", nargs="?", help="snooze duration, e.g. 12h, 7d, 2w")
    nd.add_argument("--shell", choices=["zsh", "bash", "pwsh"], help="which shell profile to edit")
    nd.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    nd.set_defaults(fn=cmd_nudge)
    sc = sub.add_parser("schedule", help="update automatically on a schedule")
    sc.add_argument("action", choices=["enable", "disable", "status", "run"])
    sc.add_argument("--daily", action="store_true", help="every day (default: weekly)")
    sc.add_argument("--day", choices=DAYS, default="mon", help="weekday for weekly runs (default: mon)")
    sc.add_argument("--at", default="09:00", help="time of day, 24h HH:MM (default 09:00)")
    sc.add_argument("-n", "--dry-run", action="store_true", help="show what would be created, change nothing")
    sc.set_defaults(fn=cmd_schedule)
    ch = sub.add_parser("changes", aliases=["whatsnew"], help="release notes for what's outdated (or named tools)")
    ch.add_argument("names", nargs="*")
    ch.set_defaults(fn=cmd_changes)
    sub.add_parser("doctor", help="health & sign-in checks").set_defaults(fn=cmd_doctor)
    sub.add_parser("whoami", aliases=["who"], help="who am I signed in as, in every tool?").set_defaults(fn=cmd_whoami)
    cx = sub.add_parser("ctx", help="switch subscription in az, azd and Az PowerShell at once")
    cx.add_argument("query", nargs="?", help="subscription id or part of its name (omit for the picker)")
    cx.add_argument("--list", action="store_true", help="just list subscriptions")
    cx.add_argument("--all", action="store_true", help="include disabled subscriptions")
    cx.set_defaults(fn=cmd_ctx)
    l = sub.add_parser("login", help="sign in")
    l.add_argument("target", nargs="?", default="az", choices=["az", "azd", "ps", "graph", "all"])
    l.add_argument("--tenant", help="tenant id or domain to sign in to")
    l.set_defaults(fn=cmd_login)
    sub.add_parser("self-update", help="update azup itself").set_defaults(fn=cmd_self_update)
    sub.add_parser("joke", help="cheer me up").set_defaults(fn=cmd_joke)
    a = p.parse_args()
    try:
        if not a.cmd:
            return interactive() if interactive_ok() else (p.print_help() or 0)
        return a.fn(a)
    except KeyboardInterrupt:
        print("\n  cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
