#!/usr/bin/env python3
"""azup - install, update and check your Azure engineering toolbox on Windows, macOS and Linux.

    azup                  interactive menu
    azup select           checklist: tick what you want, azup installs/updates it
    azup status           what is installed / outdated
    azup install [...]    install tools  (names, --profile core|full, --all; no args = checklist)
    azup update  [...]    update tools   (default: everything outdated)
    azup doctor           sign-in + health checks
    azup login [target]   az | azd | ps | graph | all
    azup self-update      update azup itself

Backends: Homebrew (macOS / Linux), winget (Windows), snap (PowerShell on Linux),
PowerShellGet (PowerShell modules) and `az extension` / `az bicep`.
Python 3.8+, standard library only.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import select
import shutil
import subprocess
import sys
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

VERSION = "2.0.0"
REPO = os.environ.get("AZUP_REPO", "saeid-adz/azup")   # set once after publishing
BRANCH = os.environ.get("AZUP_BRANCH", "main")

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"

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


def banner():
    art = [
        "   ___   _____  _   _ ____  ",
        "  / _ \\ |__  / | | | |  _ \\ ",
        " / /_\\ \\  / /  | | | | |_) |",
        "/ _____ \\/ /_  | |_| |  __/ ",
        "\\_/   \\_/____|  \\___/|_|    ",
    ]
    print()
    for line in art:
        print("  " + azure(line))
    print("  " + dim(f"Azure toolbox manager · v{VERSION} · {OS_NAME}"))
    print()


class Spinner:
    FR = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, msg):
        self.msg, self._stop = msg, threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        i = 0
        while not self._stop.is_set():
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
            sys.stdout.write("\r\x1b[K\x1b[?25h")
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
    cmd: str = ""             # binary name, used to spot installs made outside azup
    vcmd: Tuple[str, ...] = ()
    pkg: str = ""             # module / extension name for psmod / azext

    @property
    def short(self):
        return (self.brew or self.cask).split("/")[-1]


CATALOG: List[Tool] = [
    Tool("az", "Azure CLI", "CLI", "pkg", "az command line", True,
         brew="azure-cli", winget="Microsoft.AzureCLI", cmd="az"),
    Tool("azd", "Azure Developer CLI", "CLI", "pkg", "azd up / templates", True,
         brew="azd", winget="Microsoft.Azd", cmd="azd", vcmd=("azd", "version")),
    Tool("bicep", "Bicep", "CLI", "bicep", "IaC language (via az bicep)", True),
    Tool("func", "Functions Core Tools", "CLI", "pkg", "local Azure Functions",
         brew="azure/functions/azure-functions-core-tools@4",
         winget="Microsoft.Azure.FunctionsCoreTools", cmd="func"),
    Tool("kubelogin", "kubelogin", "CLI", "pkg", "Entra auth for AKS",
         brew="azure/kubelogin/kubelogin", winget="Azure.Kubelogin", cmd="kubelogin"),
    Tool("kubectl", "kubectl", "CLI", "pkg", "Kubernetes CLI",
         brew="kubernetes-cli", winget="Kubernetes.kubectl", cmd="kubectl",
         vcmd=("kubectl", "version", "--client")),
    Tool("azcopy", "AzCopy", "CLI", "pkg", "fast blob/file transfer",
         brew="azcopy", winget="Microsoft.Azure.AZCopy.10", cmd="azcopy"),
    Tool("sqlcmd", "sqlcmd (go)", "CLI", "pkg", "Azure SQL shell",
         brew="sqlcmd", winget="Microsoft.Sqlcmd", cmd="sqlcmd"),
    Tool("terraform", "Terraform", "CLI", "pkg", "IaC (azurerm)",
         brew="hashicorp/tap/terraform", winget="Hashicorp.Terraform", cmd="terraform"),
    Tool("pwsh", "PowerShell 7", "PowerShell", "pkg", "needed for the modules below", True,
         cask="powershell", winget="Microsoft.PowerShell", snap="powershell", cmd="pwsh"),
    Tool("Az", "Az PowerShell", "PowerShell", "psmod", "Azure PowerShell module", True, pkg="Az"),
    Tool("Microsoft.Graph", "Microsoft Graph SDK", "PowerShell", "psmod", "Graph API v1.0 cmdlets",
         True, pkg="Microsoft.Graph"),
    Tool("Microsoft.Graph.Beta", "Microsoft Graph Beta", "PowerShell", "psmod",
         "Graph API beta cmdlets", pkg="Microsoft.Graph.Beta"),
    Tool("Microsoft.Entra", "Microsoft Entra PS", "PowerShell", "psmod", "Entra ID admin cmdlets",
         pkg="Microsoft.Entra"),
    Tool("ext-graph", "ext: resource-graph", "az extensions", "azext",
         "Azure Resource Graph queries", True, pkg="resource-graph"),
    Tool("ext-devops", "ext: azure-devops", "az extensions", "azext", "Azure DevOps from az",
         pkg="azure-devops"),
    Tool("ext-aks", "ext: aks-preview", "az extensions", "azext", "AKS preview features",
         pkg="aks-preview"),
]


def spec(t: Tool) -> Optional[Tuple[str, str]]:
    """(manager, package-id) for a 'pkg' tool on this OS, else None."""
    if t.kind != "pkg":
        return None
    if IS_WIN:
        return ("winget", t.winget) if t.winget else None
    if IS_MAC:
        if t.cask:
            return ("cask", t.cask)
        return ("brew", t.brew) if t.brew else None
    if t.brew:
        return ("brew", t.brew)
    return ("snap", t.snap) if t.snap else None


TOOLS = [t for t in CATALOG if t.kind != "pkg" or spec(t)]
BY_KEY = {t.key.lower(): t for t in TOOLS}
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
            os.environ["PATH"] = os.pathsep.join(parts + [os.environ.get("PATH", "")])
        except ImportError:
            pass
    else:
        for d in ("/opt/homebrew/bin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin",
                  os.path.expanduser("~/.linuxbrew/bin")):
            if os.path.isdir(d) and d not in os.environ.get("PATH", "").split(":"):
                os.environ["PATH"] += ":" + d


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

    def bicep_v():
        if not have("az"):
            return ""
        rc, out = run(["az", "bicep", "version"], 30)
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        return m.group(1) if rc == 0 and m else ""

    def bin_version(t):
        rc, out = run(list(t.vcmd) or [t.cmd, "--version"], 15)
        m = re.search(r"\d+(\.\d+)+", out)
        return m.group(0) if m else "?"

    with cf.ThreadPoolExecutor(12) as ex:
        f_bl, f_bo = ex.submit(brew_list), ex.submit(brew_out)
        f_pi, f_ae, f_bv = ex.submit(ps_installed), ex.submit(az_exts), ex.submit(bicep_v)
        pm = {}
        for t in TOOLS:
            s = specs[t.key]
            if s and s[0] == "winget" and have("winget"):
                pm[t.key] = ex.submit(winget_state, s[1])
            elif s and s[0] == "snap":
                pm[t.key] = ex.submit(snap_state, s[1])
        bl, bo, pi, ae, bv = (f.result() for f in (f_bl, f_bo, f_pi, f_ae, f_bv))
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
            st[k].installed, st[k].managed, st[k].version = True, False, f.result()

    for t in TOOLS:
        s, sp = st[t.key], specs[t.key]
        if t.kind == "pkg" and sp:
            if sp[0] in ("brew", "cask") and t.short in bl:
                s.installed, s.version = True, bl[t.short]
                s.latest = bo.get(t.short, bl[t.short]) if check_latest else ""
            elif sp[0] in ("winget", "snap") and pm.get(t.key) and pm[t.key].result():
                v, avail = pm[t.key].result()
                s.installed, s.version = True, v
                s.latest = (avail or v) if (check_latest and sp[0] == "winget") else ""
        elif t.kind == "psmod" and t.pkg in pi:
            s.installed, s.version, s.latest = True, pi[t.pkg], pl.get(t.pkg, "")
        elif t.kind == "azext" and t.pkg in ae:
            s.installed, s.version, s.latest = True, ae[t.pkg], el.get(t.pkg, "")
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


# ───────────────────────────── execution ─────────────────────────────
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
            return ps_cmd("Set-PSRepository PSGallery -InstallationPolicy Trusted "
                          "-ErrorAction SilentlyContinue;"
                          f"Install-Module {t.pkg} -Scope CurrentUser -Force -AllowClobber "
                          "-ErrorAction Stop")
        return ps_cmd(f"Update-Module {t.pkg} -Force -ErrorAction Stop")
    if t.kind == "azext":
        return ["az", "extension", "add", "-n", t.pkg, "--upgrade", "-y"]
    return ["az", "bicep", "install" if inst else "upgrade"]


def requires(t: Tool):
    if t.kind == "pkg":
        return spec(t)[0]
    return "powershell" if t.kind == "psmod" else "az"


def has_requirement(req):
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
        rc = subprocess.call(["/bin/bash", "-c", 'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL '
                              'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'])
        refresh_path()
        return rc == 0 and have("brew")
    hints = {"winget": "winget ships with Windows 10/11 ('App Installer' in the Microsoft Store).",
             "snap": "snap is not available – install PowerShell via "
                     "https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux"}
    print(red(f"  '{mgr}' not found. ") + hints.get(mgr, ""))
    return False


def stream(cmd, dry):
    print("    " + dim("$ " + " ".join(cmd)))
    if dry:
        return True
    try:
        return subprocess.call(resolve_exe(cmd)) == 0
    except OSError as e:
        print(red(f"    {e}"))
        return False


Action = Tuple[Tool, str]


def execute(actions: List[Action], dry=False, yes=False):
    if not actions:
        print(f"  {green('✔')} nothing to do.\n")
        return True
    rank = {"pkg": 0, "bicep": 2, "psmod": 1, "azext": 2}
    actions = sorted(actions, key=lambda a: rank[a[0].kind])
    for mgr in sorted({requires(t) for t, _ in actions if t.kind == "pkg"}):
        if not ensure_manager(mgr, dry, yes):
            print(red(f"  cannot continue without {mgr}.\n"))
            return False
    if any(v == "update" and t.kind == "pkg" and spec(t)[0] in ("brew", "cask")
           for t, v in actions):
        print(f"\n  {azure('▶')} {bold('Refreshing Homebrew index')}")
        stream(["brew", "update"], dry)
    results = []
    for i, (t, verb) in enumerate(actions, 1):
        req = requires(t)
        refresh_path()
        if not dry and not has_requirement(req):
            results.append((t, verb, False, 0.0, f"needs {req} – install it first"))
            print(f"\n  {red('✘')} {t.name}: needs {bold(req)} – install it first")
            continue
        label = "Installing" if verb == "install" else "Updating"
        print(f"\n  {azure('▶')} {bold(f'[{i}/{len(actions)}] {label} {t.name}')}")
        t0 = time.time()
        ok = stream(build_cmd(t, verb), dry)
        results.append((t, verb, ok, time.time() - t0, ""))
    print("\n  " + bold("Summary"))
    for t, verb, ok, dt, note in results:
        print(f"   {green('✔') if ok else red('✘')} {pad(t.name, 24)}{pad(dim(verb), 10)}"
              f"{dim(f'{dt:4.0f}s')}  {red(note) if note else ''}")
    bad = [r for r in results if not r[2]]
    print()
    if bad:
        print(f"  {red(str(len(bad)))} failed – scroll up for the error output.\n")
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


def pick(title, rows: List[Row], multi=False, preselect=(), extra: Optional[Dict[bytes, Tuple[str, Callable]]] = None):
    """Arrow-key menu.  Returns an index (single), a sorted list (multi) or None if cancelled."""
    sel = set(preselect)
    n = len(rows)
    sel_idx = [i for i, r in enumerate(rows) if not r[2]]
    cur = sel_idx[0]
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
            out.append(f"\x1b[K   {ptr} {box}{pad(bold(lab) if i == cur else lab, 24)} {hint}")
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
    with Spinner("Inspecting your toolbox…"):
        return detect(check)


def resolve(names):
    out = []
    for n in names:
        t = BY_KEY.get(n.lower()) or next((x for x in TOOLS if x.name.lower() == n.lower()), None)
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


def select_flow(st=None, dry=False, yes=False):
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
                b"c": ("core", lambda: pick_where(lambda s, t: t.core))})
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
    if not confirm("Proceed?", yes):
        print("  cancelled.\n")
        return True
    return execute(actions, dry, yes)


def cmd_status(a):
    banner()
    print_table(load(not a.offline))
    return 0


def cmd_select(a):
    return 0 if select_flow(dry=a.dry_run, yes=a.yes) else 1


def cmd_install(a):
    if not (a.names or a.all or a.profile):
        return cmd_select(a)
    st = load(False)
    tools = [t for t in TOOLS if t.core] if a.profile == "core" else \
        list(TOOLS) if (a.all or a.profile == "full") else resolve(a.names)
    todo = [(t, "install") for t in tools if not st[t.key].installed]
    skipped = [t.name for t in tools if st[t.key].installed]
    if skipped:
        print(dim("  already installed: " + ", ".join(skipped)))
    return 0 if execute(todo, a.dry_run, a.yes) else 1


def cmd_update(a):
    st = load(True)
    tools = [t for t in resolve(a.names) if st[t.key].installed] if a.names \
        else [t for t in TOOLS if st[t.key].outdated]
    ext = [t.name for t in tools if not st[t.key].managed]
    if ext:
        print(yellow("  skipping (not managed by azup): " + ", ".join(ext)))
    return 0 if execute([(t, "update") for t in tools if st[t.key].managed], a.dry_run, a.yes) else 1


def cmd_login(a):
    pw = ps_exe() or "pwsh"
    steps = {"az": ["az", "login"], "azd": ["azd", "auth", "login"],
             "ps": [pw, "-NoProfile", "-Command", "Connect-AzAccount"],
             "graph": [pw, "-NoProfile", "-Command",
                       "Connect-MgGraph -Scopes 'User.Read.All','Group.Read.All','Directory.Read.All'"]}
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


def cmd_self_update(a):
    here = os.path.abspath(__file__)
    if "site-packages" in here or "dist-packages" in here:
        print(yellow("  azup was installed with pip/pipx – run:  pipx upgrade azup"))
        return 0
    if REPO.startswith("saeid-adz"):
        print(red("  REPO is not configured. Set AZUP_REPO=owner/repo or edit REPO in azup.py."))
        return 1
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


def interactive():
    st = load(True)
    while True:
        sys.stdout.write("\x1b[2J\x1b[H")
        banner()
        print_table(st)
        n_out = sum(s.outdated for s in st.values())
        rows = [("Select components…", "tick what to install or update", False),
                ("Update everything", f"{n_out} outdated", False),
                ("Install core profile", "az, azd, bicep, PowerShell, Az, Graph, resource-graph", False),
                ("Doctor", "sign-in & health", False),
                ("Sign in…", "az / azd / PowerShell / Graph", False),
                ("Update azup", "get the latest version of this tool", False),
                ("Refresh", "re-scan", False),
                ("Quit", "", False)]
        c = pick("What now?", rows)
        if c is None or c == 7:
            print()
            return 0
        if c == 0:
            select_flow(st)
        elif c == 1:
            execute([(t, "update") for t in TOOLS if st[t.key].outdated and st[t.key].managed])
        elif c == 2:
            execute([(t, "install") for t in TOOLS if t.core and not st[t.key].installed])
        elif c == 3:
            cmd_doctor(None)
        elif c == 4:
            s = pick("Sign in", [("Azure CLI", "az login", False), ("Azure Developer CLI", "azd auth login", False),
                                 ("Az PowerShell", "Connect-AzAccount", False), ("Microsoft Graph", "Connect-MgGraph", False)])
            if s is not None:
                cmd_login(argparse.Namespace(target=["az", "azd", "ps", "graph"][s]))
        elif c == 5:
            cmd_self_update(None)
        if c != 6:
            pause()
        st = load(True)


def main():
    p = argparse.ArgumentParser(prog="azup", description="Install & update your Azure toolbox.")
    p.add_argument("-V", "--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd")

    def flags(sp):
        sp.add_argument("-n", "--dry-run", action="store_true", help="show commands, change nothing")
        sp.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    s = sub.add_parser("status", aliases=["ls"], help="show installed / outdated")
    s.add_argument("--offline", action="store_true", help="skip latest-version lookup")
    s.set_defaults(fn=cmd_status)
    sl = sub.add_parser("select", aliases=["menu"], help="checklist to install/update components")
    flags(sl)
    sl.set_defaults(fn=cmd_select)
    i = sub.add_parser("install", aliases=["i"], help="install tools (no args = checklist)")
    i.add_argument("names", nargs="*")
    i.add_argument("--profile", choices=["core", "full"])
    i.add_argument("--all", action="store_true")
    flags(i)
    i.set_defaults(fn=cmd_install)
    u = sub.add_parser("update", aliases=["up"], help="update tools (default: all outdated)")
    u.add_argument("names", nargs="*")
    flags(u)
    u.set_defaults(fn=cmd_update)
    sub.add_parser("doctor", help="health & sign-in checks").set_defaults(fn=cmd_doctor)
    l = sub.add_parser("login", help="sign in")
    l.add_argument("target", nargs="?", default="az", choices=["az", "azd", "ps", "graph", "all"])
    l.set_defaults(fn=cmd_login)
    sub.add_parser("self-update", help="update azup itself").set_defaults(fn=cmd_self_update)
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
