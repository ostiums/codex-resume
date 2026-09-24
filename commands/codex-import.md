---
description: Sync Codex chats into Claude Code's /resume list
allowed-tools: Bash(codex-resume *)
---

Sync result:

!`codex-resume sync 2>&1`

!`codex-resume autosync status 2>&1`

Do not run any tools. Reply in Russian in at most 4 short lines:
- the sync result above (how many chats were imported);
- how to pick one: run `/resume`, type `Codex` to filter (imported chats are titled `⬡ Codex: <title>`), `Ctrl+A` shows chats from all folders, `Space` previews, `Enter` opens;
- only if autosync is off: `codex-resume autosync on` keeps the list up to date automatically at every Claude start.
