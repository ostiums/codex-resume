#!/usr/bin/env zsh
# Installs codex-resume from this checkout. Safe to re-run (also used by `codex-resume update`).
set -euo pipefail
here=${0:A:h}
bin_dir="$HOME/.local/bin"
commands_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/commands"

if ! command -v python3 >/dev/null; then
  echo "Нужен python3 (xcode-select --install или brew install python)" >&2
  exit 1
fi

chmod +x "$here/codex_resume.py" "$here/install.sh"
mkdir -p "$bin_dir" "$commands_dir"
ln -sf "$here/codex_resume.py" "$bin_dir/codex-resume"
cp "$here/commands/codex-import.md" "$commands_dir/codex-import.md"

if ! command -v fzf >/dev/null; then
  if command -v brew >/dev/null; then
    brew install fzf
  else
    echo "fzf не найден и brew нет — выбор чата будет нумерованным списком (fzf можно поставить позже)"
  fi
fi

if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
  echo "Добавил ~/.local/bin в PATH (~/.zshrc) — открой новое окно терминала"
fi

echo "Готово: codex-resume  +  /codex-import в Claude Code"
