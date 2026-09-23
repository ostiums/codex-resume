# codex-resume

Continue a Codex chat in Claude Code. The tool converts a Codex session
(`~/.codex/sessions/**/rollout-*.jsonl`) into a regular Claude Code session and
opens it with `claude --resume` in the same folder.

The CLI's own messages are in Russian.

## Installation

Requirements: macOS (or Linux) with zsh, `python3` ≥ 3.9, Codex and Claude Code.

```sh
git clone https://github.com/ostiums/codex-resume ~/.local/share/codex-resume && ~/.local/share/codex-resume/install.sh
```

The installer:
- creates `~/.local/bin/codex-resume`;
- copies the `/codex-import` slash command to `~/.claude/commands/`;
- installs `fzf` via Homebrew if brew is available (without fzf, chats are picked from a numbered list);
- adds `~/.local/bin` to PATH via `~/.zshrc` if it isn't there yet.

Update: `codex-resume update`.

## Usage

```sh
codex-resume                     # Codex chats from the CURRENT folder: pick one in fzf and open it in Claude
codex-resume global              # same, across all folders (-g / --global is a synonym)
codex-resume resume <id>         # open a specific chat (looked up across all folders)
codex-resume list [-g] [--json]  # list chats: folder, title; current folder only by default
codex-resume import <id>         # convert only, print the command to continue
codex-resume preview <id>        # show the beginning of a chat
```

`<id>` is a full Codex session id or any unique part of it (6+ characters).

The chat always opens in Claude in the folder where it ran in Codex, even if
`codex-resume global` was started somewhere else. Folders are compared after
resolving symlinks.

In the current-folder list you see the date and the chat title; in `global`
mode the folder name is shown between them (`~` for your home folder). The
preview is hidden; **Space** shows and hides it. Because of that you can't type
a space in the fzf search field, so search by a single word. The full path is
shown in the preview.

Inside Claude Code: `/codex-import [global | search text | id]`. It opens an
interactive picker (3 chats per page plus `Ещё…` for the next page; the free-text
answer works as search), imports the chosen chat and tells you how to open it.
A running session can't switch to another one by itself.

Imported chats appear in the regular `claude --resume` list as `Codex: <title>`.

## How the history is carried over

- User and assistant messages are carried over. Codex tool calls become text
  blocks `[Codex tool: …]`: input is truncated to 1000 characters, output to 2000.
- Codex system inserts (environment_context, AGENTS.md, etc.), developer
  messages, encrypted reasoning and internal reviewer sessions are dropped.
- A note that the chat was moved from Codex is added at the beginning.
- A single message is truncated to 8000 characters. If the whole history is
  longer than 400,000 characters, the oldest turns are not carried over (a note
  at the beginning says so).
- Codex context compaction is ignored: the full original history is carried over.
- The `↩Claude` mark in the list means Codex itself once imported that chat
  from Claude.

## Re-importing

The Claude session id is derived from the Codex session id, so re-importing
updates the same file. If the imported session has already been continued in
Claude (the file has grown), it is left untouched: a new session is created and
a warning is printed. State is kept in `~/.local/state/codex-resume/imports.json`.

The tool only reads Codex data.

## Uninstall

```sh
rm ~/.local/bin/codex-resume ~/.claude/commands/codex-import.md
rm -rf ~/.local/state/codex-resume ~/.local/share/codex-resume
```

## Tests

```sh
python3 -m unittest discover -s tests
```
