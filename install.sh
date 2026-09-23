#!/usr/bin/env zsh
# Installs codex-resume. Safe to re-run (also used by `codex-resume update`).
#
#   curl -fsSL https://raw.githubusercontent.com/ostiums/codex-resume/main/install.sh | zsh
#   curl -fsSL …/install.sh | zsh -s -- --no-autosync     # don't sync Codex chats at Claude start
#
# Run through a pipe, it clones (or fast-forwards) the repo into ~/.local/share/codex-resume and
# continues from there. Autosync is switched on only on the first install, so an update never
# turns it back on after `codex-resume autosync off`.
set -euo pipefail

autosync=1
for arg in "$@"; do
  case $arg in
    --no-autosync) autosync=0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null; then
  echo "python3 is required (xcode-select --install or brew install python)" >&2
  exit 1
fi

if [[ ${0:t} == install.sh && -f ${0:A:h}/codex_resume.py ]]; then
  here=${0:A:h}
else
  here="$HOME/.local/share/codex-resume"
  repo=${CODEX_RESUME_REPO:-https://github.com/ostiums/codex-resume.git}
  if ! command -v git >/dev/null; then
    echo "git is required (xcode-select --install)" >&2
    exit 1
  fi
  if [[ -d $here/.git ]]; then
    git -C "$here" pull --ff-only -q
  else
    mkdir -p "${here:h}"
    git clone -q "$repo" "$here"
  fi
fi

bin_dir="$HOME/.local/bin"
commands_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/commands"
link="$bin_dir/codex-resume"
first_install=0
[[ -e $link ]] || first_install=1

chmod +x "$here/codex_resume.py" "$here/install.sh"
mkdir -p "$bin_dir" "$commands_dir"
ln -sf "$here/codex_resume.py" "$link"
cp "$here/commands/codex-import.md" "$commands_dir/codex-import.md"

if ! command -v fzf >/dev/null; then
  if command -v brew >/dev/null; then
    brew install fzf
  else
    echo "fzf not found and no brew — chats will be picked from a numbered list (you can install fzf later)"
  fi
fi

path_line='export PATH="$HOME/.local/bin:$PATH"'
if [[ ":$PATH:" != *":$bin_dir:"* ]] && ! grep -qxF "$path_line" "$HOME/.zshrc" 2>/dev/null; then
  echo "$path_line" >> "$HOME/.zshrc"
  echo "Added ~/.local/bin to PATH (~/.zshrc) — open a new terminal window"
fi

if (( first_install && autosync )); then
  "$link" autosync on  # also runs the first sync
fi

echo "Done: codex-resume  +  /codex-import in Claude Code"
