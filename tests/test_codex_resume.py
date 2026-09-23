import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codex_resume as cr

TS = "2026-09-21T10:00:01.000Z"


def meta(id="01a0c409-0000-7000-8000-000000000001", cwd="/tmp", **kw):
    p = {"id": id, "cwd": cwd, "timestamp": "2026-09-21T10:00:00.000Z", "source": "vscode"}
    p.update(kw)
    return {"timestamp": p["timestamp"], "type": "session_meta", "payload": p}


def _item(payload, ts=TS):
    return {"timestamp": ts, "type": "response_item", "payload": payload}


def msg(role, *texts, ts=TS):
    kind = "output_text" if role == "assistant" else "input_text"
    return _item({"type": "message", "role": role,
                  "content": [{"type": kind, "text": t} for t in texts]}, ts)


def fcall(name, arguments, cid):
    return _item({"type": "function_call", "name": name, "arguments": arguments, "call_id": cid})


def fout(cid, output):
    return _item({"type": "function_call_output", "call_id": cid, "output": output})


def ccall(name, inp, cid):
    return _item({"type": "custom_tool_call", "name": name, "input": inp, "call_id": cid})


def cout(cid, texts):
    return _item({"type": "custom_tool_call_output", "call_id": cid,
                  "output": [{"type": "input_text", "text": t} for t in texts]})


def write_rollout(codex_home, records, day="2026/09/21", name=None):
    d = Path(codex_home) / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    sid = records[0]["payload"]["id"]
    p = d / (name or f"rollout-2026-09-21T10-00-00-{sid}.jsonl")
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return p


class ParseTests(unittest.TestCase):
    def test_noise_and_developer_dropped(self):
        recs = [meta(), msg("developer", "system stuff"),
                msg("user", "<environment_context>\n<cwd>/x</cwd>"),
                msg("user", "# AGENTS.md instructions for /Users/x\n..."),
                msg("user", "привет"), msg("assistant", "здравствуй")]
        items = cr.extract_items(recs)
        self.assertEqual([(i.role, i.text) for i in items], [("user", "привет"), ("assistant", "здравствуй")])

    def test_image_placeholder(self):
        rec = msg("user", "смотри")
        rec["payload"]["content"].append({"type": "input_image", "image_url": "data:..."})
        self.assertEqual(cr.extract_items([rec])[0].text, "смотри\n\n[изображение]")

    def test_function_call_uses_cmd_and_pairs_output(self):
        items = cr.extract_items([fcall("shell", json.dumps({"cmd": "ls -la"}), "c1"), fout("c1", "a.txt")])
        self.assertEqual(cr.render_tool(items[0]), "[Codex tool: shell]\nls -la\n→ a.txt")

    def test_function_call_command_list(self):
        items = cr.extract_items([fcall("shell", json.dumps({"command": ["/bin/zsh", "-lc", "pwd"]}), "c1")])
        self.assertEqual(items[0].text, "/bin/zsh -lc pwd")

    def test_custom_call_output_list_and_missing_output(self):
        items = cr.extract_items([ccall("exec", "text(1)", "c1"), cout("c1", ["Script completed", "1"]),
                                  ccall("exec", "text(2)", "c2")])
        self.assertEqual(cr.render_tool(items[0]), "[Codex tool: exec]\ntext(1)\n→ Script completed\n1")
        self.assertEqual(cr.render_tool(items[1]), "[Codex tool: exec]\ntext(2)\n→ (нет вывода)")

    def test_orphan_output_dropped(self):
        self.assertEqual(cr.extract_items([fout("nope", "x")]), [])

    def test_truncate(self):
        self.assertEqual(cr.truncate("abc", 5), "abc")
        self.assertEqual(cr.truncate("абвгдеж", 3), "абв…[обрезано, 7 симв.]")

    def test_tool_output_truncated(self):
        items = cr.extract_items([fcall("shell", "{}", "c1"), fout("c1", "x" * 2500)])
        self.assertTrue(cr.render_tool(items[0]).endswith("…[обрезано, 2500 симв.]"))

    def test_compaction_keeps_full_raw_history(self):
        # Real Codex replacement_history holds only user messages + an encrypted summary,
        # while the raw pre-compaction records stay in the append-only rollout.
        compacted = {"timestamp": "2026-09-21T11:00:00.000Z", "type": "compacted",
                     "payload": {"message": "", "replacement_history": [
                         msg("user", "старое")["payload"],
                         {"type": "compaction", "encrypted_content": "gAAA"}]}}
        recs = [meta(), msg("user", "старое"), msg("assistant", "старый ответ"), compacted,
                msg("user", "новое")]
        self.assertEqual([i.text for i in cr.extract_items(recs)], ["старое", "старый ответ", "новое"])

    def test_long_message_truncated(self):
        items = cr.extract_items([msg("user", "я" * 9000)])
        self.assertTrue(items[0].text.endswith("…[обрезано, 9000 симв.]"))
        self.assertLess(len(items[0].text), 8100)

    def test_total_limit_drops_oldest_turns(self):
        recs = []
        for n in range(10):
            recs += [msg("user", f"q{n}" + "x" * 100), msg("assistant", f"a{n}" + "y" * 100)]
        turns = cr.build_turns(cr.extract_items(recs), header="HDR", max_chars=700)
        self.assertEqual(turns[0].role, "user")
        self.assertIn("не перенесены", turns[0].text)
        self.assertTrue(turns[-1].text.startswith("a9"))
        self.assertFalse(any("q0" in t.text for t in turns))
        self.assertLessEqual(sum(len(t.text) for t in turns[1:]), 700)

    def test_utf8_cut_mid_char_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            tail = '{"type": "response_item", "text": "Привет'.encode("utf-8")[:-1]
            p.write_bytes((json.dumps(meta()) + "\n").encode("utf-8") + tail)
            recs, bad = cr.load_jsonl(p)
            self.assertEqual((len(recs), bad), (1, 1))

    def test_build_turns_merges_and_alternates(self):
        items = cr.extract_items([msg("assistant", "a1"), fcall("shell", "{}", "c"), fout("c", "o"),
                                  msg("assistant", "a2"), msg("user", "u1"), msg("user", "u2")])
        turns = cr.build_turns(items, header="HDR")
        self.assertEqual([t.role for t in turns], ["user", "assistant", "user"])
        self.assertEqual(turns[0].text, "HDR\n\n" + cr.LEAD_USER_TEXT)
        self.assertEqual(turns[1].text, "a1\n\n[Codex tool: shell]\n{}\n→ o\n\na2")
        self.assertEqual(turns[2].text, "u1\n\nu2")

    def test_header_prefixes_first_user_turn(self):
        turns = cr.build_turns(cr.extract_items([msg("user", "q"), msg("assistant", "a")]), header="HDR")
        self.assertEqual(turns[0].text, "HDR\n\nq")

    def test_context_header_mentions_codex_and_cwd(self):
        h = cr.context_header("2026-09-21", "/tmp/x")
        self.assertIn("Codex", h)
        self.assertIn("/tmp/x", h)
        self.assertIn("2026-09-21", h)

    def test_malformed_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_text(json.dumps(meta()) + "\n" + '{"type": "respo', encoding="utf-8")
            recs, bad = cr.load_jsonl(p)
            self.assertEqual((len(recs), bad), (1, 1))


    def test_user_image_becomes_image_block(self):
        rec = msg("user", "смотри")
        rec["payload"]["content"].append({"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"})
        item = cr.extract_items([rec])[0]
        self.assertEqual(item.text, "смотри\n\n[изображение]")
        self.assertEqual(item.images, [{"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}])

    def test_unusable_images_stay_placeholders(self):
        rec = msg("user", "q")
        for url in ("data:...", "data:image/bmp;base64,Qk0=", "https://example.com/a.png"):
            rec["payload"]["content"].append({"type": "input_image", "image_url": url})
        item = cr.extract_items([rec])[0]
        self.assertEqual(item.images, [])
        self.assertEqual(item.text.count("[изображение]"), 3)

    def test_oversized_image_stays_placeholder(self):
        rec = msg("user", "q")
        rec["payload"]["content"].append({"type": "input_image", "image_url": "data:image/png;base64," + "A" * 64})
        old, cr.IMAGE_MAX_CHARS = cr.IMAGE_MAX_CHARS, 32
        try:
            self.assertEqual(cr.extract_items([rec])[0].images, [])
        finally:
            cr.IMAGE_MAX_CHARS = old

    def test_image_only_message_kept(self):
        rec = _item({"type": "message", "role": "user",
                     "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,/9j/"}]})
        items = cr.extract_items([rec])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].images[0]["source"]["media_type"], "image/jpeg")

    def test_tool_output_image_marked(self):
        out = _item({"type": "custom_tool_call_output", "call_id": "c1", "output": [
            {"type": "input_text", "text": "Output:"},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,/9j/", "detail": "original"}]})
        items = cr.extract_items([ccall("exec", "screenshot()", "c1"), out])
        self.assertEqual(items[0].output, "Output:\n[скриншот]")

    def test_images_travel_to_merged_user_turn(self):
        rec = msg("user", "два")
        rec["payload"]["content"].append({"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="})
        turns = cr.build_turns(cr.extract_items([msg("user", "раз"), rec, msg("assistant", "ок")]), header="HDR")
        self.assertEqual(len(turns[0].images), 1)
        self.assertEqual(turns[1].images, [])


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_excludes_subagents_and_noise_only(self):
        write_rollout(self.home, [meta(id="a" * 8 + "-real"), msg("user", "вопрос")])
        write_rollout(self.home, [meta(id="b" * 8 + "-guard", thread_source="guardian_review"), msg("user", "x")])
        write_rollout(self.home, [meta(id="c" * 8 + "-sub", source={"subagent": {"other": "guardian"}}), msg("user", "x")])
        write_rollout(self.home, [meta(id="d" * 8 + "-noise"), msg("user", "<environment_context>x")])
        self.assertEqual([s.id for s in cr.discover(self.home)], ["a" * 8 + "-real"])
        self.assertEqual(len(cr.discover(self.home, include_all=True)), 4)

    def test_cwd_from_last_turn_context(self):
        tc = lambda cwd: {"timestamp": TS, "type": "turn_context", "payload": {"cwd": cwd}}
        write_rollout(self.home, [meta(id="tc-0000001", cwd="/start"), tc("/start"), msg("user", "q"),
                                  tc("/moved"), msg("user", "q2")])
        self.assertEqual(cr.discover(self.home)[0].cwd, "/moved")

    def test_archived_included(self):
        d = self.home / "archived_sessions"
        d.mkdir(parents=True)
        (d / "rollout-x.jsonl").write_text(json.dumps(meta(id="arch-000001")) + "\n" + json.dumps(msg("user", "q")) + "\n")
        self.assertEqual([s.id for s in cr.discover(self.home)], ["arch-000001"])

    def test_title_priority(self):
        write_rollout(self.home, [meta(id="t1-0000001"), msg("user", "первый вопрос")])
        write_rollout(self.home, [meta(id="t2-0000002"), msg("user", "другое")])
        write_rollout(self.home, [meta(id="t3-0000003"), msg("user", "третье")])
        (self.home / "session_index.jsonl").write_text(
            json.dumps({"id": "t1-0000001", "thread_name": "Old"}) + "\n" +
            json.dumps({"id": "t1-0000001", "thread_name": "Индекс"}) + "\n")
        (self.home / "external_agent_session_imports.json").write_text(json.dumps(
            {"records": [{"imported_thread_id": "t2-0000002", "title": "Из Claude"}]}))
        by_id = {s.id: s for s in cr.discover(self.home)}
        self.assertEqual(by_id["t1-0000001"].title, "Индекс")
        self.assertEqual(by_id["t2-0000002"].title, "Из Claude")
        self.assertTrue(by_id["t2-0000002"].from_claude)
        self.assertEqual(by_id["t3-0000003"].title, "третье")

    def test_title_is_single_line(self):
        write_rollout(self.home, [meta(id="ml-0000001"), msg("user", "строка\tодна\nдве " + "x" * 100)])
        title = cr.discover(self.home)[0].title
        self.assertNotIn("\t", title)
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 60)

    def test_newest_first_and_counts(self):
        p1 = write_rollout(self.home, [meta(id="old-000001"), msg("user", "a"), msg("user", "b")])
        p2 = write_rollout(self.home, [meta(id="new-000002"), msg("user", "c")])
        os.utime(p1, (1_000_000, 1_000_000))
        os.utime(p2, (2_000_000, 2_000_000))
        found = cr.discover(self.home)
        self.assertEqual([s.id for s in found], ["new-000002", "old-000001"])
        self.assertEqual(found[1].user_turns, 2)
        self.assertEqual(found[0].cwd, "/tmp")

    def test_resolve_id(self):
        s = [cr.SessionInfo(id=i, path=Path("x"), cwd="/", started="", updated=0, title=i,
                            user_turns=1, from_claude=False, is_chat=True)
             for i in ("01a0c409-aaaa-111111", "01a0c409-bbbb-111111", "01a0c409-cccc-222222")]
        self.assertEqual(cr.resolve_id(s, "01a0c409-aaaa-111111").id, "01a0c409-aaaa-111111")
        self.assertEqual(cr.resolve_id(s, "222222").id, "01a0c409-cccc-222222")
        with self.assertRaisesRegex(LookupError, "Неоднозначный"):
            cr.resolve_id(s, "111111")
        with self.assertRaisesRegex(LookupError, "Нет сессии"):
            cr.resolve_id(s, "999999")
        with self.assertRaisesRegex(LookupError, "6"):
            cr.resolve_id(s, "2222")

    def test_resolve_id_by_prefix_or_substring(self):
        s = [cr.SessionInfo(id=i, path=Path("x"), cwd="/", started="", updated=0, title=i,
                            user_turns=1, from_claude=False, is_chat=True)
             for i in ("01a0c409-aaaa-111111", "01a0c40a-bbbb-111111")]
        self.assertEqual(cr.resolve_id(s, "01a0c40a").id, "01a0c40a-bbbb-111111")
        self.assertEqual(cr.resolve_id(s, "c409-aaaa").id, "01a0c409-aaaa-111111")


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.codex, self.claude, self.state = root / "codex", root / "claude", root / "state.json"
        self.cwd = root / "proj"
        self.cwd.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, cwd=None):
        write_rollout(self.codex, [meta(id="imp-000001", cwd=str(cwd or self.cwd)),
                                   msg("user", "вопрос"), msg("assistant", "ответ")])
        return cr.discover(self.codex)[0]

    def test_slug_non_ascii(self):
        self.assertEqual(cr.project_slug("/Users/alice/Documents/work/my-app"),
                         "-Users-alice-Documents-work-my-app")
        self.assertEqual(cr.project_slug("/Users/alice/Cowork проект"), "-Users-alice-Cowork-------")

    def test_render_records_chain_and_title(self):
        turns = [cr.Turn("user", ["q"], TS), cr.Turn("assistant", ["a"], None)]
        recs = cr.render_records(turns, "sid", "/w", "2.1.280", "Тема")
        self.assertIsNone(recs[0]["parentUuid"])
        self.assertEqual(recs[1]["parentUuid"], recs[0]["uuid"])
        self.assertEqual(recs[0]["message"], {"role": "user", "content": "q"})
        self.assertEqual(recs[1]["message"]["content"], [{"type": "text", "text": "a"}])
        self.assertEqual(recs[1]["message"]["role"], "assistant")
        self.assertTrue(recs[1]["timestamp"].endswith("Z"))
        self.assertEqual(recs[-1], {"type": "custom-title", "customTitle": "Codex: Тема", "sessionId": "sid"})
        for r in recs[:2]:
            self.assertEqual((r["sessionId"], r["cwd"], r["version"], r["isSidechain"]), ("sid", "/w", "2.1.280", False))

    def test_render_user_turn_with_images_as_block_list(self):
        blk = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
        recs = cr.render_records([cr.Turn("user", ["q"], TS, images=[blk])], "sid", "/w", "v", "T")
        self.assertEqual(recs[0]["message"]["content"], [{"type": "text", "text": "q"}, blk])

    def test_import_writes_session_and_state(self):
        res = cr.import_session(self.session(), self.claude, self.state, "2.1.280")
        self.assertEqual(res.path.parent, self.claude / "projects" / cr.project_slug(str(self.cwd)))
        recs, _ = cr.load_jsonl(res.path)
        self.assertEqual([r["type"] for r in recs], ["user", "assistant", "custom-title"])
        self.assertTrue(recs[0]["message"]["content"].startswith("[Этот чат перенесён из Codex"))
        self.assertEqual(json.loads(self.state.read_text())["imp-000001"]["session_id"], res.session_id)
        self.assertFalse(res.kept_previous)

    def test_reimport_overwrites_same_session(self):
        info = self.session()
        first = cr.import_session(info, self.claude, self.state, "v")
        second = cr.import_session(info, self.claude, self.state, "v")
        self.assertEqual(first.session_id, second.session_id)
        self.assertFalse(second.kept_previous)

    def test_reimport_after_continuation_keeps_old(self):
        info = self.session()
        first = cr.import_session(info, self.claude, self.state, "v")
        with open(first.path, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "продолжил"}}) + "\n")
        before = first.path.read_text()
        second = cr.import_session(info, self.claude, self.state, "v")
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertTrue(second.kept_previous)
        self.assertEqual(first.path.read_text(), before)
        third = cr.import_session(info, self.claude, self.state, "v")
        self.assertEqual(third.session_id, second.session_id)
        self.assertFalse(third.kept_previous)

    def test_missing_cwd_falls_back_home(self):
        res = cr.import_session(self.session(cwd="/nonexistent/dir"), self.claude, self.state, "v")
        self.assertEqual(res.cwd, str(Path.home()))
        self.assertEqual(res.path.parent.name, cr.project_slug(str(Path.home())))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.codex, self.claude, self.state = root / "codex", root / "claude", root / "state.json"
        (root / "w").mkdir()
        self.chats = [write_rollout(self.codex, [meta(id=f"syn-{n:08d}", cwd=str(root / "w")),
                                                 msg("user", f"вопрос {n}"), msg("assistant", "ответ")])
                      for n in range(3)]
        write_rollout(self.codex, [meta(id="grd-00000009", thread_source="guardian_review"), msg("user", "x")])
        self.version_calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def version(self):
        self.version_calls += 1
        return "v"

    def sync(self):
        return cr.sync(self.codex, self.claude, self.state, self.version)

    def test_first_sync_imports_every_chat_only(self):
        res = self.sync()
        self.assertEqual((res.imported, res.unchanged), (3, 0))
        self.assertEqual(len(list((self.claude / "projects").glob("*/*.jsonl"))), 3)
        self.assertEqual(self.version_calls, 1)

    def test_second_sync_parses_nothing(self):
        self.sync()
        original = cr._read_session
        cr._read_session = lambda *a: self.fail("unchanged rollout was parsed")
        try:
            res = self.sync()
        finally:
            cr._read_session = original
        self.assertEqual((res.imported, res.unchanged), (0, 4))
        self.assertEqual(self.version_calls, 1)

    def test_manual_import_then_continue_does_not_duplicate_on_sync(self):
        info = cr.find_session(self.codex, "syn-00000000")
        res = cr.import_session(info, self.claude, self.state, "v")
        with open(res.path, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "продолжил"}}) + "\n")
        self.sync()
        copies = [p for p in (self.claude / "projects").glob("*/*.jsonl")
                  if "вопрос 0" in p.read_text()]
        self.assertEqual(copies, [res.path])

    def test_changed_chat_is_reimported(self):
        self.sync()
        with open(self.chats[1], "a", encoding="utf-8") as f:
            f.write(json.dumps(msg("user", "новое сообщение")) + "\n")
        res = self.sync()
        self.assertEqual((res.imported, res.unchanged), (1, 3))
        info = cr.find_session(self.codex, "syn-00000001")
        recs, _ = cr.load_jsonl(self.claude / "projects" / cr.project_slug(info.cwd) /
                                f"{json.loads(self.state.read_text())[info.id]['session_id']}.jsonl")
        self.assertIn("новое сообщение", recs[-2]["message"]["content"])


class AutosyncTests(unittest.TestCase):
    CMD = "/opt/bin/codex-resume sync --quiet"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def hooks(self):
        return json.loads(self.settings.read_text())["hooks"]["SessionStart"]

    def test_on_creates_async_session_start_hook(self):
        self.assertTrue(cr.set_autosync(self.settings, True, self.CMD))
        [entry] = self.hooks()
        self.assertEqual(entry["hooks"], [{"type": "command", "command": self.CMD, "async": True}])
        self.assertTrue(cr.autosync_enabled(self.settings))

    def test_on_is_idempotent_and_keeps_other_settings(self):
        other = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}
        self.settings.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [other]}}))
        cr.set_autosync(self.settings, True, self.CMD)
        self.assertFalse(cr.set_autosync(self.settings, True, self.CMD))
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(len(data["hooks"]["SessionStart"]), 2)
        self.assertIn(other, data["hooks"]["SessionStart"])

    def test_off_removes_only_our_hook(self):
        other = {"hooks": [{"type": "command", "command": "echo hi"}]}
        self.settings.write_text(json.dumps({"hooks": {"SessionStart": [other]}}))
        cr.set_autosync(self.settings, True, self.CMD)
        self.assertTrue(cr.set_autosync(self.settings, False, self.CMD))
        self.assertEqual(self.hooks(), [other])
        self.assertFalse(cr.autosync_enabled(self.settings))

    def test_off_cleans_up_empty_hooks(self):
        cr.set_autosync(self.settings, True, self.CMD)
        cr.set_autosync(self.settings, False, self.CMD)
        self.assertEqual(json.loads(self.settings.read_text()), {})

    def test_unparseable_settings_left_untouched(self):
        self.settings.write_text("{ // comment\n}")
        with self.assertRaises(ValueError):
            cr.set_autosync(self.settings, True, self.CMD)
        self.assertEqual(self.settings.read_text(), "{ // comment\n}")

    def test_status_without_settings_file(self):
        self.assertFalse(cr.autosync_enabled(self.settings))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {"CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"),
                    "CODEX_RESUME_STATE": str(root / "state.json")}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        write_rollout(root / "codex", [meta(id="cli-00000001", cwd=str(root)),
                                       msg("user", "как дела"), msg("assistant", "норм")])
        write_rollout(root / "codex", [meta(id="grd-00000002", thread_source="guardian_review"),
                                       msg("user", "approve?")])
        (root / "other").mkdir()
        (root / "empty").mkdir()
        write_rollout(root / "codex", [meta(id="oth-00000003", cwd=str(root / "other")),
                                       msg("user", "другая папка"), msg("assistant", "ок")])
        self.root = root
        self.old_cwd = os.getcwd()
        os.chdir(root)

    def tearDown(self):
        os.chdir(self.old_cwd)
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cr.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_list(self):
        code, out, _ = self.run_main("list")
        self.assertEqual(code, 0)
        self.assertIn("как дела", out)
        self.assertIn("cli-00000001", out)
        self.assertNotIn("grd-00000002", out)

    def test_list_only_current_dir(self):
        _, out, _ = self.run_main("list")
        self.assertIn("cli-00000001", out)
        self.assertNotIn("oth-00000003", out)

    def test_list_global_flag(self):
        for flag in ("--global", "-g"):
            _, out, _ = self.run_main("list", flag)
            self.assertIn("cli-00000001", out)
            self.assertIn("oth-00000003", out)

    def test_list_global_shows_dir_name_not_full_path(self):
        _, out, _ = self.run_main("list", "-g")
        line = next(l for l in out.splitlines() if "oth-00000003" in l)
        shown = line.split("\t")[0]
        self.assertIn("other", shown)
        self.assertIn("другая папка", shown)
        self.assertNotIn(str(self.root), out)

    def info(self, cwd, title, turns=5):
        return cr.SessionInfo(id=f"id-{title}-000000", path=Path("x"), cwd=cwd, started="", updated=0,
                              title=title, user_turns=turns, from_claude=False, is_chat=True)

    def test_rows_local_are_date_and_title_then_id(self):
        [line] = cr.rows([self.info("/a/proj", "Тема")])
        shown, sid = line.split("\t")
        self.assertEqual(sid, "id-Тема-000000")
        self.assertTrue(shown.endswith("  Тема"))
        self.assertNotIn("proj", shown)
        self.assertNotIn("5", shown)  # no message-count column
        self.assertNotIn("   ", shown)  # no wide gaps

    def test_rows_global_align_titles_after_dir(self):
        lines = cr.rows([self.info("/a/my-app", "Первая"), self.info(str(Path.home()), "Вторая")])
        shown = [l.split("\t")[0] for l in lines]
        self.assertIn("my-app", shown[0])
        self.assertIn("  ~  ", shown[1])
        self.assertEqual(shown[0].index("Первая"), shown[1].index("Вторая"))

    def test_fzf_preview_hidden_until_space(self):
        args = cr.fzf_args(" · x", "codex-resume preview")
        self.assertIn("space:toggle-preview", args[args.index("--bind") + 1])
        self.assertIn("hidden", args[args.index("--preview-window") + 1])
        self.assertEqual(args[args.index("--with-nth") + 1], "1")
        self.assertIn("preview {2}", args[args.index("--preview") + 1])

    def test_empty_dir_hints_global(self):
        os.chdir(self.root / "empty")
        code, out, err = self.run_main("list")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("codex-resume global", err)
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("codex-resume global", err)

    def test_global_subcommand_with_no_chats(self):
        os.environ["CODEX_HOME"] = str(self.root / "empty")
        code, _, err = self.run_main("global")
        self.assertEqual(code, 1)
        self.assertIn("Нет чатов Codex", err)

    def test_update_pulls_repo_and_reinstalls(self):
        import subprocess
        origin, clone = self.root / "origin", self.root / "clone"
        git = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
        origin.mkdir()
        git("init", "-q", "-b", "main", cwd=origin)
        (origin / "install.sh").write_text("#!/bin/sh\necho installed > \"$(dirname \"$0\")/marker\"\n")
        (origin / "install.sh").chmod(0o755)
        git("add", ".", cwd=origin)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=origin)
        git("clone", "-q", str(origin), str(clone), cwd=self.root)
        (origin / "new.txt").write_text("v2")
        git("add", ".", cwd=origin)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v2", cwd=origin)
        code = cr.update(clone)
        self.assertEqual(code, 0)
        self.assertTrue((clone / "new.txt").exists())
        self.assertEqual((clone / "marker").read_text().strip(), "installed")

    def test_update_outside_git_fails(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cr.update(self.root / "empty"), 1)
        self.assertIn("git", err.getvalue())

    def test_top_level_global_flag_same_as_global(self):
        os.environ["CODEX_HOME"] = str(self.root / "empty")
        for argv in (["-g"], ["--global"]):
            code, _, err = self.run_main(*argv)
            self.assertEqual(code, 1)
            self.assertIn("Нет чатов Codex", err)

    def test_explicit_id_prefers_visible_chats(self):
        # "0000000" matches the chat, the other chat and the hidden guardian session;
        # a suffix unique among visible chats must not be made ambiguous by hidden ones.
        write_rollout(self.root / "codex", [meta(id="grd-99990001", thread_source="guardian_review"),
                                            msg("user", "approve?")])
        code, out, _ = self.run_main("preview", "990001")
        self.assertEqual(code, 0)
        write_rollout(self.root / "codex", [meta(id="vis-99990001"), msg("user", "видимый")])
        code, out, _ = self.run_main("preview", "990001")
        self.assertEqual(code, 0)
        self.assertIn("видимый", out)

    def test_global_flag_before_subcommand(self):
        _, out, _ = self.run_main("-g", "list")
        self.assertIn("oth-00000003", out)

    def test_list_json_empty_is_quiet(self):
        os.chdir(self.root / "empty")
        code, out, err = self.run_main("list", "--json")
        self.assertEqual((code, json.loads(out), err), (0, [], ""))

    def test_sync_quiet_prints_nothing(self):
        code, out, err = self.run_main("sync", "--quiet")
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, _ = self.run_main("sync")
        self.assertIn("без изменений", out)

    def test_list_json(self):
        code, out, _ = self.run_main("list", "--json")
        data = json.loads(out)
        self.assertEqual([d["id"] for d in data], ["cli-00000001"])
        self.assertEqual(set(data[0]), {"id", "title", "cwd", "updated", "user_turns", "from_claude"})

    def test_import_prints_resume_command(self):
        code, out, _ = self.run_main("import", "00000001")
        self.assertEqual(code, 0)
        self.assertIn("claude --resume", out)
        self.assertEqual(len(list((self.root / "claude" / "projects").glob("*/*.jsonl"))), 1)

    def test_preview(self):
        code, out, _ = self.run_main("preview", "cli-00000001")
        self.assertEqual(code, 0)
        self.assertIn("как дела", out)
        self.assertIn("норм", out)

    def test_unknown_id(self):
        code, _, err = self.run_main("import", "zzzzzzzz")
        self.assertEqual(code, 2)
        self.assertIn("Нет сессии", err)


class InstallerTests(unittest.TestCase):
    """install.sh run the way users run it: `curl … | zsh` with a throwaway HOME."""

    def setUp(self):
        import shutil, subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        (self.home / ".codex").mkdir(parents=True)
        repo = Path(__file__).resolve().parent.parent
        self.origin = self.root / "origin"
        self.origin.mkdir()
        for name in ("codex_resume.py", "install.sh"):
            shutil.copy2(repo / name, self.origin / name)
        shutil.copytree(repo / "commands", self.origin / "commands")
        git = lambda *a: subprocess.run(["git", *a], cwd=self.origin, check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.env = {**os.environ, "HOME": str(self.home), "CODEX_RESUME_REPO": str(self.origin)}
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CODEX_RESUME_STATE"):
            self.env.pop(k, None)
        self.installer = (repo / "install.sh").read_text()

    def tearDown(self):
        self.tmp.cleanup()

    def pipe_install(self, *args):
        import subprocess
        return subprocess.run(["zsh", "-s", "--", *args], input=self.installer, text=True,
                              capture_output=True, cwd=self.root, env=self.env)

    def settings(self):
        p = self.home / ".claude" / "settings.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def autosync_on(self):
        return cr.AUTOSYNC_MARK in json.dumps(self.settings())

    def test_piped_install_clones_links_and_enables_autosync(self):
        r = self.pipe_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        clone = self.home / ".local/share/codex-resume"
        self.assertTrue((clone / ".git").is_dir())
        link = self.home / ".local/bin/codex-resume"
        self.assertEqual(Path(os.path.realpath(link)), (clone / "codex_resume.py").resolve())
        self.assertTrue((self.home / ".claude/commands/codex-import.md").exists())
        self.assertTrue(self.autosync_on())

    def test_reinstall_does_not_reenable_autosync(self):
        import subprocess
        self.assertEqual(self.pipe_install().returncode, 0)
        link = self.home / ".local/bin/codex-resume"
        subprocess.run([str(link), "autosync", "off"], env=self.env, check=True, capture_output=True)
        self.assertFalse(self.autosync_on())
        r = subprocess.run([str(self.home / ".local/share/codex-resume/install.sh")],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.autosync_on())
        self.assertEqual(self.pipe_install().returncode, 0)  # piped re-run = update of the clone
        self.assertFalse(self.autosync_on())

    def test_no_autosync_flag(self):
        r = self.pipe_install("--no-autosync")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.autosync_on())
        self.assertTrue((self.home / ".local/bin/codex-resume").exists())


if __name__ == "__main__":
    unittest.main()
