#!/usr/bin/env sh
# azup installer for macOS and Linux.
#   curl -fsSL https://raw.githubusercontent.com/saeid-adz/azup/main/install.sh | sh
set -eu

REPO="${AZUP_REPO:-saeid-adz/azup}"
BRANCH="${AZUP_BRANCH:-main}"
DIR="$HOME/.azup"
BIN="$HOME/.local/bin"

say() { printf '  \033[38;5;39m▶\033[0m %s\n' "$1"; }
die() { printf '  \033[31m✘\033[0m %s\n' "$1" >&2; exit 1; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  case "$(uname -s)" in
    Darwin) die "Python 3.8+ not found. Run: xcode-select --install   (or: brew install python)";;
    *)      die "Python 3.8+ not found. Run: sudo apt install python3   (or dnf/pacman equivalent)";;
  esac
fi

say "Downloading azup from github.com/$REPO ($BRANCH)"
mkdir -p "$DIR" "$BIN"
curl -fsSL "https://raw.githubusercontent.com/$REPO/$BRANCH/azup.py" -o "$DIR/azup.py" \
  || die "download failed – check AZUP_REPO / network"

PYPATH="$(command -v "$PY")"
cat > "$BIN/azup" <<SHIM
#!/usr/bin/env sh
exec "$PYPATH" "$DIR/azup.py" "\$@"
SHIM
chmod +x "$BIN/azup"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh)  RC="$HOME/.zshrc" ;;
      */bash) RC="$HOME/.bashrc"; [ "$(uname -s)" = Darwin ] && RC="$HOME/.bash_profile" ;;
      *)      RC="$HOME/.profile" ;;
    esac
    grep -qs '.local/bin' "$RC" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
    say "Added ~/.local/bin to PATH in $RC – open a new terminal (or: export PATH=\"\$HOME/.local/bin:\$PATH\")"
    ;;
esac

printf '\n  \033[32m✔\033[0m azup installed.  Run: \033[1mazup\033[0m\n\n'
