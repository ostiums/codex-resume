# codex-resume

Continue a Codex chat in Claude Code. The tool converts a Codex session
(`~/.codex/sessions/**/rollout-*.jsonl`) into a regular Claude Code session and
opens it with `claude --resume` in the same folder.

The CLI's own messages are in Russian.

## Installation

Requirements: macOS (or Linux) with zsh, `git`, `python3` ≥ 3.9, Codex and Claude Code.

```sh
curl -fsSL https://raw.githubusercontent.com/ostiums/codex-resume/main/install.sh | zsh
```

That's it. The installer:
- clones the repo into `~/.local/share/codex-resume` and links `~/.local/bin/codex-resume`;
- copies the `/codex-import` slash command to `~/.claude/commands/`;
- turns on **autosync**: an async `SessionStart` hook in `~/.claude/settings.json`
  runs `codex-resume sync --quiet` in the background at every Claude start, so
  every Codex chat shows up in Claude's own `/resume` picker as `Codex: <title>`
  (it never delays startup; a no-op sync takes ~0.04 s even with 150+ chats);
- runs the first sync right away, so chats are in `/resume` immediately;
- installs `fzf` via Homebrew if brew is available (without fzf, chats are picked from a numbered list);
- adds `~/.local/bin` to PATH via `~/.zshrc` if it isn't there yet.

Without autosync: `curl -fsSL …/install.sh | zsh -s -- --no-autosync`.
Turn it on or off later with `codex-resume autosync on|off` — updates never
switch it back on.

Update: `codex-resume update` (or run the same curl line again).

## Usage

```sh
codex-resume                     # Codex chats from the CURRENT folder: pick one in fzf and open it in Claude
codex-resume global              # same, across all folders (-g / --global is a synonym)
codex-resume resume <id>         # open a specific chat (looked up across all folders)
codex-resume list [-g] [--json]  # list chats: folder, title; current folder only by default
codex-resume import <id>         # convert only, print the command to continue
codex-resume preview <id>        # show the beginning of a chat
codex-resume sync                # import all new/changed chats so they appear in /resume
codex-resume autosync on|off     # run sync in the background at every Claude start
```

`<id>` is a full Codex session id or any unique part of it (6+ characters).

The chat always opens in Claude in the folder where it last ran in Codex (the
latest `turn_context`, falling back to where it started), even if
`codex-resume global` was started somewhere else. Folders are compared after
resolving symlinks.

In the current-folder list you see the date and the chat title; in `global`
mode the folder name is shown between them (`~` for your home folder). The
preview is hidden; **Space** shows and hides it. Because of that you can't type
a space in the fzf search field, so search by a single word. The full path is
shown in the preview.

Inside Claude Code: `/codex-import` syncs all chats. Then pick one in the
built-in `/resume` picker: type `Codex` to filter (imported chats are titled
`Codex: <title>`), `Ctrl+A` shows chats from all folders, `Space` previews,
`Enter` opens.

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
- Images you attached in Codex (PNG, JPEG, GIF, WebP up to 5 MB) are carried
  over as real image blocks, so Claude sees them; each costs roughly 1–1.5k
  tokens per request. Screenshots taken by Codex tools are not embedded — the
  tool output shows `[скриншот]` instead.
- Codex context compaction is ignored: the full original history is carried over.
- The `↩Claude` mark in the list means Codex itself once imported that chat
  from Claude.

## Re-importing

The Claude session id is derived from the Codex session id, so re-importing
updates the same file. If the imported session has already been continued in
Claude (the file has grown), it is left untouched: a new session is created and
a warning is printed. `sync` only re-imports a chat when its Codex file has
changed since the last import, so continuing a chat in Claude never produces
duplicates by itself. State is kept in `~/.local/state/codex-resume/imports.json`.

The tool only reads Codex data.

## Uninstall

```sh
codex-resume autosync off
rm ~/.local/bin/codex-resume ~/.claude/commands/codex-import.md
rm -rf ~/.local/state/codex-resume ~/.local/share/codex-resume
```

## Tests

```sh
python3 -m unittest discover -s tests
```
