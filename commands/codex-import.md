---
description: Import a Codex chat into Claude Code so it can be resumed
argument-hint: "[global | search text | id]"
allowed-tools: Bash(codex-resume *), AskUserQuestion
---

Codex chats started in the current folder (newest first; each line is `date  [folder]  title<TAB>id`):

!`codex-resume list 2>&1`

Codex chats from all folders (same format):

!`codex-resume list --global 2>&1`

Arguments: `$ARGUMENTS`

Help the user pick one of these chats and import it. Your FIRST action must be the AskUserQuestion selection window — do not run commands, read files or write any text before it. Never print the list or ask the user to type a number.

1. Which list to offer: if the arguments contain `global` or `-g`, or the current-folder list has no chats, use the all-folders list (and mention in the question that it covers all folders); otherwise use the current-folder list.
2. If the arguments contain other text, narrow the list to chats whose id, title or folder contains it (case-insensitive). If exactly one chat remains, skip the window and go to step 4.
3. Selection window (AskUserQuestion, header `Codex чат`, question `Какой чат Codex продолжить?`):
   - options = the next 3 chats: label = title shortened to ≤ 40 chars, description = date, plus the folder name if the line shows one;
   - if more chats remain, add a 4th option `Ещё…` (description `следующие чаты`); choosing it opens a new window with the next 3;
   - the free-text "Other" answer is search text: filter the whole list by it and open the window again (or go to step 4 if exactly one matches; say so if nothing matches).
4. Run `codex-resume import <id>` with the chosen chat's full id (the text after the tab).
5. Reply in Russian: the chat title and that it can be opened in two ways — `/resume` (listed as `Codex: <title>`) or in a terminal with the exact `cd … && claude --resume …` line from the import output. The current session cannot switch to it by itself.
