---
description: Import a Codex chat into Claude Code so it can be resumed
argument-hint: "[global | search text | id]"
allowed-tools: Bash(codex-resume:*), AskUserQuestion
---

Import a Codex chat as a Claude Code session using the `codex-resume` CLI. The user picks the chat in an interactive selection window — always use the AskUserQuestion tool for choosing, never print a list and ask the user to type a number.

1. Get the chats (JSON array, newest first):
   - `$ARGUMENTS` contains `global` or `-g` → `codex-resume list --global --json`;
   - otherwise → `codex-resume list --json` (chats started in the current folder). If that is empty, use `codex-resume list --global --json` and tell the user the list covers all folders.
2. If `$ARGUMENTS` has other text, narrow the list to chats whose id contains it or whose title/cwd contains it (case-insensitive). If exactly one chat remains, skip to step 4.
3. Selection window (AskUserQuestion, header `Codex чат`, question `Какой чат Codex продолжить?`):
   - options = the next 3 chats of the list: label = title shortened to ≤ 40 chars, description = `<updated> · <folder name = last path component of cwd, ~ for home>`;
   - if more chats remain, add a 4th option `Ещё…` (description `следующие чаты`); choosing it shows the next 3 in a new window;
   - the built-in "Other" answer is search text: filter the whole list by it and show the window again (or go to step 4 if exactly one matches; say so if nothing matches).
4. Run `codex-resume import <full id>`.
5. Reply in Russian: the chat title and that it can be opened in two ways — `/resume` (it is listed as `Codex: <title>`) or in a terminal: `cd <cwd> && claude --resume <session id>` (show the exact line from the import output). The current session cannot switch to it by itself.
