# Frontend tests

Run from the repository root:

```sh
node --test tests/ui/*.test.js ui/*.test.js
node --check ui/app.js
node --check ui/voice-core.js
```

`ui/voice-core.js` contains pure helpers and is safe to import from Node. `ui/app.js` is browser-only and is exercised by `tests/ui/voice-integration.test.js` in a small DOM/API harness that executes the real application script (not source-text matching).

The voice UI is intentionally SAFE VOICE-ONLY: it uses `/api/status` capability flags, local `/api/listen` transcription, and Edge `/api/speak` audio. It does not invoke `/api/run`, cloud speech fallback, or agent/tools. Voice transcripts remain editable and require a separate user action to read aloud; there is no auto-submit path. Human acceptance still needs a real browser/device check for microphone permission, Thai transcription quality, audio playback/cancel behavior, keyboard/screen-reader usability, and mobile layout.
