# Thai voice and chat-only pilot

Run `python3 start-thai.py --no-browser` from this checkout, then open http://127.0.0.1:8730. HTTP runs on host standard-library Python. Speech providers and chat run in separate subprocesses using the installed managed Hermes runtime. Bind is loopback only. No changes to global model routing, installed Hermes source, Gateway or approval policy.

## Voice
`/api/listen` accepts raw audio MIME payloads up to 4 MiB with a bounded 31-second decode, while the UI stops recording at 29 seconds. It uses local Whisper medium/th and deletes temporary audio after completion/timeout. `/api/speak` accepts JSON `{"text":"..."}` up to 2,000 characters and returns online Edge Niwat MP3; unique temporary audio is removed. Microphone transcription is reviewed before any send. Speech generation/playback is explicit. UI abort does not guarantee immediate STT/TTS provider termination.

## Restricted conversation
If real construction/gate readiness succeeds, explicitly confirmed `/api/run` invokes a Hermes model with **no tools**. The route comes from `chat_route.json` (pilot-only `provider`/`model`, currently `xiaomi / mimo-v2.6-flash`) or, without that file, from the configured Hermes default. No keys, no `~/.hermes/config.yaml` edits, no client-selectable routing. It sends text/history to that provider; do not submit secrets. Replies are displayed safely, with a separate optional read button, and `/api/status` reports `chat_provider`/`chat_model`. Chat is not arbitrary command execution: slash/shell commands and `/api/jobs` are disabled. All file/system changes, email and other external actions remain out of scope; use Hermes Desktop for actual tasks. Provider quota/credential failures come back as `429`/`503` with a Thai message instead of an opaque `503`. See `CHAT_ONLY_SCOPE.md` for the process-local fail-closed boundary and its limitations.

## Request safety and lifecycle
POST requests require loopback Host, same-origin Origin and the per-launch page token. Responses are no-store. Server request logs are disabled; raw errors, credentials and traces are not returned. Chat requires confirmation plus per-tab/per-run UUIDs; history is volatile, isolated and bounded. Cancellation invalidates stale results and kills the local chat process group when it exists; it cannot undo remote provider computation/billing. Starting a new chat resets only that client's history.

Restart the standalone Dashboard to load changes and refresh the browser to receive its new token. Do not restart Gateway. Existing voice acceptance applies to the voice-only version; the new send/reply/read flow requires a user acceptance test. For rollback see `ROLLBACK.md`.
