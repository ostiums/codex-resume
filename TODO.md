# TODO

- [x] **Images as real image blocks.** Codex `input_image` used to become the text `[image]`,
  so the model never saw the screenshot. Now `data:image/...;base64,...` is carried over as a
  Claude `{"type": "image", "source": {"type": "base64", ...}}` block in the user message
  (same approach as transession). Cost: ~1–1.5k tokens per image per request.
- [x] **cwd from the latest `turn_context`** instead of `session_meta` (as in the
  PavelCz/cli-continues fork) — correct when a chat was moved to another folder.
- [ ] *(optional)* A flag to carry full tool output instead of truncating it to 2000 characters.
