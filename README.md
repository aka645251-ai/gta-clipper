# gta-clipper

Private project: turn very long game streams into short vertical clips
(facecam on top, gameplay below, auto captions), from a YouTube link.

## Phase 0: can the server do the basics?

Run the **Phase 0 probe** workflow (Actions tab, Run workflow). It checks, in order:

1. Can GitHub's server read the YouTube video?
2. Can it fetch the auto-captions (text only)?
3. Can it download only a short section, not the whole stream?
4. Can Gemini find the facecam box in one frame?

The result is in the run summary, and the clip, frame and `report.json`
are in the `phase0-output` artifact (kept for 3 days).

### Secrets (Settings, Secrets and variables, Actions)

| Name | Needed | What for |
|---|---|---|
| `GEMINI_API_KEY` | yes, for step 4 | Gemini API key (free tier is fine) |
| `YT_COOKIES` | only if YouTube blocks the server | Netscape-format cookies.txt. Use a spare Google account, not your main one |

Never paste a key into a chat or commit it to the repo.

### What the result means

- **All PASS:** the plan works; Phase 1 (the clipping engine) can start.
- **meta or clip FAIL with reason `bot_block`:** YouTube is blocking the server.
  Try the `YT_COOKIES` secret once; if it still fails, the engine has to run somewhere else.
- **gemini FAIL or `box_valid: false`:** facecam detection needs a better prompt or a different model.
