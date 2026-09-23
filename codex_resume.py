#!/usr/bin/env python3
"""Continue Codex chats in Claude Code.

Converts a Codex rollout (~/.codex/sessions/**/rollout-*.jsonl) into a native
Claude Code session file so it can be opened with `claude --resume`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

NAMESPACE = uuid.UUID("6f1c2b1e-3c1a-4d7e-9a57-2f0c0de5e5a1")
TOOL_INPUT_LIMIT = 1000
TOOL_OUTPUT_LIMIT = 2000
MESSAGE_LIMIT = 8000  # per user/assistant message
TOTAL_LIMIT = 400_000  # whole imported history; oldest turns are dropped beyond this
DIR_COLUMN_MAX = 24
PREVIEW_TURNS = 15
PREVIEW_CHARS = 600
LEAD_USER_TEXT = "[Продолжение чата из Codex]"
NOISE_PREFIXES = (
    "<environment_context>", "<app-context>", "<recommended_plugins>",
    "<guardian_tool_descriptions>", "<user_instructions>", "<INSTRUCTIONS>",
    "<skill>", "<turn_aborted>", "# AGENTS.md instructions",
)

# ---------------------------------------------------------------- parse


@dataclass
class Item:
    role: str  # "user" | "assistant" | "tool"
    text: str  # message text, or tool input for role == "tool"
    ts: str | None
    name: str = ""
    output: str | None = None


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    parts: list[str]
    ts: str | None

    @property
    def text(self) -> str:
        return "\n\n".join(self.parts)


def load_jsonl(path) -> tuple[list[dict], int]:
    records, bad = [], 0
    with open(path, "rb") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                bad += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                bad += 1
    return records, bad


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"…[обрезано, {len(s)} симв.]"


def _message_text(payload: dict) -> str:
    role = payload.get("role")
    parts = []
    for c in payload.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "input_image":
            parts.append("[изображение]")
            continue
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and text.lstrip().startswith(NOISE_PREFIXES):
            continue
        parts.append(text)
    return "\n\n".join(parts)


def _tool_input(payload: dict) -> str:
    if payload.get("type") == "custom_tool_call":
        return str(payload.get("input") or "")
    raw = payload.get("arguments") or ""
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return str(raw)
    if isinstance(args, dict):
        for key in ("cmd", "command", "code"):
            value = args.get(key)
            if isinstance(value, list):
                return " ".join(map(str, value))
            if isinstance(value, str):
                return value
    return str(raw)


def _tool_output(payload: dict) -> str:
    out = payload.get("output")
    if isinstance(out, list):
        return "\n".join(c.get("text", "") for c in out if isinstance(c, dict))
    if out is None:
        return ""
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def extract_items(records: list[dict]) -> list[Item]:
    items: list[Item] = []
    calls: dict[str, Item] = {}

    def add(payload: dict, ts: str | None) -> None:
        kind = payload.get("type")
        if kind == "message" and payload.get("role") in ("user", "assistant"):
            text = truncate(_message_text(payload), MESSAGE_LIMIT)
            if text:
                items.append(Item(payload["role"], text, ts))
        elif kind in ("function_call", "custom_tool_call"):
            item = Item("tool", _tool_input(payload), ts, name=str(payload.get("name") or "?"))
            items.append(item)
            if payload.get("call_id"):
                calls[payload["call_id"]] = item
        elif kind in ("function_call_output", "custom_tool_call_output"):
            item = calls.get(payload.get("call_id"))
            if item is not None:
                item.output = _tool_output(payload)

    # `compacted` records are ignored: their replacement_history is only the user
    # messages plus an encrypted summary, while the rollout keeps the full raw history.
    for rec in records:
        if rec.get("type") == "response_item" and isinstance(rec.get("payload"), dict):
            add(rec["payload"], rec.get("timestamp"))
    return items


def render_tool(item: Item) -> str:
    output = "(нет вывода)" if item.output is None else truncate(item.output, TOOL_OUTPUT_LIMIT)
    return f"[Codex tool: {item.name}]\n{truncate(item.text, TOOL_INPUT_LIMIT)}\n→ {output}"


def build_turns(items: list[Item], header: str | None = None, max_chars: int = TOTAL_LIMIT) -> list[Turn]:
    turns: list[Turn] = []
    for item in items:
        role = "user" if item.role == "user" else "assistant"
        text = render_tool(item) if item.role == "tool" else item.text
        if turns and turns[-1].role == role:
            turns[-1].parts.append(text)
        else:
            turns.append(Turn(role, [text], item.ts))
    dropped = 0
    total = sum(len(t.text) for t in turns)
    while len(turns) > 1 and total > max_chars:
        total -= len(turns.pop(0).text)
        dropped += 1
    if turns and turns[0].role == "assistant":
        turns.insert(0, Turn("user", [LEAD_USER_TEXT], turns[0].ts))
    if turns and dropped:
        turns[0].parts.insert(0, f"[Ранние ходы ({dropped}) не перенесены из-за размера — они остались в Codex.]")
    if turns and header:
        turns[0].parts.insert(0, header)
    return turns


def context_header(date: str, cwd: str) -> str:
    return (f"[Этот чат перенесён из Codex ({date}, cwd {cwd}). Ответы ассистента ниже "
            "написал агент Codex; блоки [Codex tool: …] — команды, которые он выполнил, "
            "и их вывод. Продолжай работу с учётом этой истории.]")


# ---------------------------------------------------------------- discover


@dataclass
class SessionInfo:
    id: str
    path: Path
    cwd: str
    started: str
    updated: float
    title: str
    user_turns: int
    from_claude: bool
    is_chat: bool


def one_line(s: str, n: int = 60) -> str:
    s = " ".join(s.split())
    return s[: n - 1] + "…" if len(s) > n else s


def is_subagent(meta: dict) -> bool:
    source = meta.get("source")
    return (isinstance(source, dict) and "subagent" in source) or meta.get("thread_source") == "guardian_review"


def _load_titles(codex_home: Path) -> dict[str, str]:
    path = codex_home / "session_index.jsonl"
    if not path.exists():
        return {}
    records, _ = load_jsonl(path)
    return {r["id"]: r["thread_name"] for r in records if r.get("id") and r.get("thread_name")}


def _load_claude_origin(codex_home: Path) -> dict[str, str]:
    data = read_json(codex_home / "external_agent_session_imports.json")
    return {r["imported_thread_id"]: r.get("title") or ""
            for r in data.get("records", []) if isinstance(r, dict) and r.get("imported_thread_id")}


def _session_files(codex_home: Path) -> list[Path]:
    return (sorted((codex_home / "sessions").glob("*/*/*/rollout-*.jsonl"))
            + sorted((codex_home / "archived_sessions").glob("rollout-*.jsonl")))


def _read_session(path: Path, titles: dict, origin: dict) -> SessionInfo | None:
    records, _ = load_jsonl(path)
    meta = next((r.get("payload") for r in records if r.get("type") == "session_meta"), None)
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    sid = meta["id"]
    user_texts = [i.text for i in extract_items(records) if i.role == "user"]
    title = titles.get(sid) or origin.get(sid) or (user_texts[0] if user_texts else "") or "(без названия)"
    return SessionInfo(
        id=sid, path=path, cwd=meta.get("cwd") or str(Path.home()),
        started=meta.get("timestamp") or "", updated=path.stat().st_mtime,
        title=one_line(title), user_turns=len(user_texts), from_claude=sid in origin,
        is_chat=not is_subagent(meta) and bool(user_texts),
    )


def discover(codex_home: Path, include_all: bool = False) -> list[SessionInfo]:
    titles, origin = _load_titles(codex_home), _load_claude_origin(codex_home)
    sessions = [s for p in _session_files(codex_home) if (s := _read_session(p, titles, origin))]
    if not include_all:
        sessions = [s for s in sessions if s.is_chat]
    return sorted(sessions, key=lambda s: s.updated, reverse=True)


def find_session(codex_home: Path, query: str) -> SessionInfo:
    """Resolve an explicit id. Rollout filenames contain the id, so only matching files are parsed."""
    titles, origin = _load_titles(codex_home), _load_claude_origin(codex_home)
    files = [p for p in _session_files(codex_home) if query in p.name]
    return resolve_id([s for p in files if (s := _read_session(p, titles, origin))], query)


def resolve_id(sessions: list[SessionInfo], query: str) -> SessionInfo:
    for s in sessions:
        if s.id == query:
            return s
    if len(query) < 6:
        raise LookupError("Укажи полный id или хотя бы 6 его символов")
    matches = [s for s in sessions if query in s.id]
    if len(matches) > 1:
        matches = [s for s in matches if s.is_chat] or matches
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"Нет сессии Codex с id, содержащим {query}")
    lines = "\n".join(f"  {s.id}  {s.title}" for s in matches)
    raise LookupError(f"Неоднозначный id {query}, подходят:\n{lines}")


# ---------------------------------------------------------------- write


@dataclass
class ImportResult:
    session_id: str
    path: Path
    cwd: str
    turns: int
    kept_previous: bool


def project_slug(cwd: str) -> str:
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in cwd)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def render_records(turns: list[Turn], session_id: str, cwd: str, version: str, title: str) -> list[dict]:
    records, parent = [], None
    for n, turn in enumerate(turns):
        rec_uuid = str(uuid.uuid5(NAMESPACE, f"{session_id}:{n}"))
        if turn.role == "user":
            message = {"role": "user", "content": turn.text}
        else:
            message = {"id": f"msg_codex_{n}", "type": "message", "role": "assistant",
                       "model": "codex-import", "content": [{"type": "text", "text": turn.text}],
                       "stop_reason": "end_turn", "stop_sequence": None,
                       "usage": {"input_tokens": 0, "output_tokens": 0}}
        records.append({"parentUuid": parent, "isSidechain": False, "type": turn.role,
                        "message": message, "uuid": rec_uuid, "timestamp": turn.ts or _now_iso(),
                        "userType": "external", "entrypoint": "cli", "cwd": cwd,
                        "sessionId": session_id, "version": version, "gitBranch": ""})
        parent = rec_uuid
    records.append({"type": "custom-title", "customTitle": f"Codex: {title}", "sessionId": session_id})
    return records


def _count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def import_session(info: SessionInfo, claude_dir: Path, state_path: Path, version: str) -> ImportResult:
    cwd = info.cwd if os.path.isdir(info.cwd) else str(Path.home())
    records, _ = load_jsonl(info.path)
    turns = build_turns(extract_items(records), context_header(info.started[:10], info.cwd))
    if not turns:
        raise ValueError("В этом чате нет сообщений для переноса")

    state = read_json(state_path)
    prev = state.get(info.id) or {}
    project = claude_dir / "projects" / project_slug(cwd)
    project.mkdir(parents=True, exist_ok=True)

    # Take the first free session id, or the one written last time if Claude hasn't grown it since.
    k = 0
    while True:
        sid = str(uuid.uuid5(NAMESPACE, info.id if k == 0 else f"{info.id}:{k}"))
        path = project / f"{sid}.jsonl"
        if not path.exists():
            break
        if prev.get("session_id") == sid and _count_lines(path) <= prev.get("lines_written", 0):
            break
        k += 1

    out = render_records(turns, sid, cwd, version, info.title)
    atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    state[info.id] = {"session_id": sid, "lines_written": len(out)}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=1))
    kept = k > 0 and prev.get("session_id") != sid
    return ImportResult(sid, path, cwd, len(turns), kept)


# ---------------------------------------------------------------- cli


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _state_path() -> Path:
    return Path(os.environ.get("CODEX_RESUME_STATE") or Path.home() / ".local/state/codex-resume/imports.json")


def _claude_version() -> str:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=15).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    return out[0] if out else "2.1.0"


def _short_cwd(cwd: str) -> str:
    home = str(Path.home())
    return "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + "/") else cwd


def _dir_name(cwd: str) -> str:
    if cwd == str(Path.home()):
        return "~"
    return Path(cwd).name or cwd


def rows(sessions: list[SessionInfo]) -> list[str]:
    """One line per chat: visible text, a tab, then the full id (hidden in fzf).

    The folder column appears only when the chats come from more than one folder."""
    show_dir = len({s.cwd for s in sessions}) > 1
    dirs = [one_line(_dir_name(s.cwd), DIR_COLUMN_MAX) for s in sessions]
    width = max(map(len, dirs), default=0)
    out = []
    for s, d in zip(sessions, dirs):
        cols = [dt.datetime.fromtimestamp(s.updated).strftime("%Y-%m-%d %H:%M")]
        if show_dir:
            cols.append(d.ljust(width))
        cols.append(s.title + (" ↩Claude" if s.from_claude else ""))
        out.append("  ".join(cols) + "\t" + s.id)
    return out


def fzf_args(scope: str, preview_cmd: str) -> list[str]:
    return ["fzf", "--delimiter", "\t", "--with-nth", "1", "--no-sort",
            "--header", f"Codex → Claude{scope} · Enter — открыть, Пробел — превью, Esc — выход",
            "--preview", f"{preview_cmd} {{2}}", "--preview-window", "right,55%,wrap,hidden",
            "--bind", "space:toggle-preview"]


def in_dir(sessions: list[SessionInfo], cwd: str) -> list[SessionInfo]:
    here = os.path.realpath(cwd)
    return [s for s in sessions if os.path.realpath(s.cwd) == here]


def _chats(global_: bool) -> list[SessionInfo]:
    """Chats to offer: all of them, or only those started in the current directory."""
    sessions = discover(_codex_home())
    return sessions if global_ else in_dir(sessions, os.getcwd())


def _empty_hint(global_: bool) -> str:
    if global_:
        return "Нет чатов Codex"
    return f"В этой папке ({_short_cwd(os.getcwd())}) нет чатов Codex. Все чаты: codex-resume global"


def pick(sessions: list[SessionInfo], scope: str) -> SessionInfo | None:
    if shutil.which("fzf"):
        preview_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.realpath(__file__))} preview"
        proc = subprocess.run(fzf_args(scope, preview_cmd), input="\n".join(rows(sessions)),
                              stdout=subprocess.PIPE, text=True)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        chosen_id = proc.stdout.strip().split("\t")[-1]
        return next(s for s in sessions if s.id == chosen_id)
    shown = sessions
    while True:
        for n, line in enumerate(rows(shown), 1):
            print(f"{n:>3}. " + line.split("\t")[0])
        try:
            answer = input("Номер или текст для фильтра (пусто — выход): ").strip()
        except EOFError:
            return None
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1]
        query = answer.lower()
        found = [s for s in sessions if query in f"{s.title} {s.cwd}".lower()]
        if found:
            shown = found
        else:
            print("Ничего не найдено")


def _preview(s: SessionInfo) -> None:
    records, _ = load_jsonl(s.path)
    print(f"{s.title}\n{_short_cwd(s.cwd)} · {s.started[:10]} · {s.user_turns} реплик\n")
    for turn in build_turns(extract_items(records))[:PREVIEW_TURNS]:
        print(f"── {turn.role} ──\n{truncate(turn.text, PREVIEW_CHARS)}\n")


def _do_import(s: SessionInfo) -> ImportResult:
    res = import_session(s, _claude_dir(), _state_path(), _claude_version())
    if res.cwd != s.cwd:
        print(f"⚠ Папки {s.cwd} больше нет — сессия создана в {res.cwd}", file=sys.stderr)
    if res.kept_previous:
        print("ℹ Этот чат уже продолжали в Claude — прежняя сессия сохранена, создана новая", file=sys.stderr)
    print(f"Импортировано: {s.title} ({res.turns} ходов)")
    print(f"Сессия Claude: {res.session_id}")
    print(f"Файл: {res.path}")
    print(f"Продолжить: cd {shlex.quote(res.cwd)} && claude --resume {res.session_id}")
    return res


def update(repo: Path) -> int:
    """git pull the tool's own checkout and re-run its installer."""
    if not (repo / ".git").exists():
        print(f"{repo} — не git-репозиторий, обновить через git нельзя", file=sys.stderr)
        return 1
    if subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "-q"]).returncode != 0:
        print("git pull не удался", file=sys.stderr)
        return 1
    return subprocess.run([str(repo / "install.sh")]).returncode


def main(argv: list[str] | None = None) -> int:
    def add_global_flag(p: argparse.ArgumentParser, default) -> argparse.ArgumentParser:
        p.add_argument("-g", "--global", dest="global_", action="store_true", default=default,
                       help="чаты из всех папок (то же, что codex-resume global)")
        return p

    # -g works before or after the subcommand; SUPPRESS keeps a subparser from resetting it.
    parser = add_global_flag(argparse.ArgumentParser(
        prog="codex-resume", description="Продолжить чат из Codex в Claude Code"), False)
    sub = parser.add_subparsers(dest="cmd")
    p_list = add_global_flag(sub.add_parser("list", help="список чатов Codex"), argparse.SUPPRESS)
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--all", action="store_true", help="включая служебные сессии")
    sub.add_parser("import", help="конвертировать чат").add_argument("id")
    add_global_flag(sub.add_parser("resume", help="конвертировать и открыть в claude"),
                    argparse.SUPPRESS).add_argument("id", nargs="?")
    sub.add_parser("global", help="выбрать чат из всех папок и открыть в claude").add_argument("id", nargs="?")
    sub.add_parser("preview", help="показать начало чата").add_argument("id")
    sub.add_parser("update", help="обновить codex-resume (git pull + install.sh)")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "list":
            sessions = discover(_codex_home(), include_all=True) if args.all else _chats(args.global_)
            if not sessions and not args.json:
                print(_empty_hint(args.global_), file=sys.stderr)
            if args.json:
                print(json.dumps([{"id": s.id, "title": s.title, "cwd": s.cwd,
                                   "updated": dt.datetime.fromtimestamp(s.updated).isoformat(timespec="minutes"),
                                   "user_turns": s.user_turns, "from_claude": s.from_claude}
                                  for s in sessions], ensure_ascii=False, indent=1))
            else:
                for line in rows(sessions):
                    print(line)
            return 0
        if args.cmd == "update":
            return update(Path(os.path.realpath(__file__)).parent)
        if args.cmd == "preview":
            _preview(find_session(_codex_home(), args.id))
            return 0
        if args.cmd == "import":
            _do_import(find_session(_codex_home(), args.id))
            return 0
        # resume (default) / global
        chosen_id = getattr(args, "id", None)
        if chosen_id:
            chosen = find_session(_codex_home(), chosen_id)
        else:
            global_ = args.cmd == "global" or args.global_
            sessions = _chats(global_)
            if not sessions:
                print(_empty_hint(global_), file=sys.stderr)
                return 1
            chosen = pick(sessions, " · все папки" if global_ else f" · {_dir_name(os.getcwd())}")
        if chosen is None:
            return 130
        res = _do_import(chosen)
        os.chdir(res.cwd)
        os.execvp("claude", ["claude", "--resume", res.session_id])
    except LookupError as e:
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
