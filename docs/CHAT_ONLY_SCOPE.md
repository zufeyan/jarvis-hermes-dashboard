# Restricted Hermes conversation pilot

## Scope
The Dashboard can send explicitly confirmed text to the configured default Hermes model and display its actual response. It is **chat-only**, not a full task-executing agent. Local Whisper dictation and optional online Edge Niwat playback remain separate. No microphone recording is automatically submitted to a model, no automatic playback, no wake-word/continuous listening, no LAN listener.

## Enforced boundary
`agent_worker.py` constructs installed `run_agent.AIAgent` in the managed Hermes Python runtime, with `enabled_toolsets=[]`, `skip_memory`, `skip_background_review`, `skip_context_files`, no session DB and no checkpoints/trajectory persistence. It checks the FINAL constructed `tools=[]` and `valid_tool_names=set()` after initialization.

Empty tool schemas alone do **not** enforce the native dispatcher. A bounded inert source experiment showed that the native executor could process a forged terminal call despite empty schemas; its absent command was rejected by input validation. Therefore this pilot does not rely on native valid-name filtering or on a fail-open plugin hook. Before any model request, process-local instance gates replace `_execute_tool_calls`, sequential/concurrent executors, `_invoke_tool`, and `_dispatch_delegate_task` with a `BaseException` abort. Six forged requests exercise these gates during readiness and every request. If final tools are nonempty or the gates do not deny, the run fails closed. Unsupported external ACP/Codex app-server agents, which may have their own tools outside Python, are rejected. Installed Hermes source/config/plugins/approval policy are not changed.

This is application-level capability isolation, **not an OS sandbox**, not a guarantee that native initialization makes no internal auth/cache/log writes, and not general per-action approval. General automation endpoints remain disabled. Provider-specific native changes must be re-reviewed after an update.

## State and cancellation
Loopback Host, matching Origin, per-launch token, strict JSON/body limits and no-store remain required. `/api/run` additionally requires `confirmed:true`, a bounded message, per-tab UUID `client_id`, per-run UUID `request_id`. Slash/shell commands are rejected. One run at a time; a 120-second timeout terminates the managed process group. The last 512 request IDs cannot be replayed. Per-client history is volatile, bounded to ten exchanges and sixteen clients. Restart/eviction discards history; this is not Desktop session continuity or durable cross-device identity.

`/api/cancel` matches both owner and run, invalidates the result immediately, kills that process group when started, and returns observed `stopped` rather than claiming remote rollback. Provider computation/billing already performed is not reversible. `/api/new` cancels only that client's active request and invalidates its history generation; other clients are not reset. Stale or expired results cannot append history.

## Privacy
Explicitly submitted text and bounded conversation history are sent to the configured LLM provider. Text selected for speech goes to online Edge TTS. Do not send secrets or personal data. Voice STT stays local; the local server does not log prompts/transcripts/audio/tokens or return raw provider errors. This policy does not override the model provider's retention policy or the native runtime's internal diagnostic/auth behavior.

## Chat route and failure reporting
By default the worker resolves the configured Hermes default (`model.default`/`model.provider`). `start-thai.py` may additionally read a **pilot-only** `chat_route.json` (`provider` + `model`, no keys) and export `JARVIS_CHAT_PROVIDER`/`JARVIS_CHAT_MODEL` into the worker environment. A client request can never choose the provider or model, and nothing is written to `~/.hermes/config.yaml`, auth stores or global routing. Delete `chat_route.json` (or export `JARVIS_CHAT_PROVIDER=...` before launch) to fall back to the configured default.

Provider/turn failures are classified in the worker and returned as an honest status with a Thai-prefixed, token-redacted message (429 for quota/rate limits, 503 for credential/billing/other), instead of an opaque `HTTP 503`. Tool refusal, safety-proof drift and unexpected worker crashes stay generic 503 fail-closed. Observed: the configured default `openai-codex` was rate-limited (HTTP 429, usage limit resets ~93h), so the pilot ships with `chat_route.json` set to `xiaomi / mimo-v2.6-flash`, which answered the real end-to-end Thai chat smoke in 9.4s. Reverting to the default therefore surfaces `HTTP 429` with the reset time, not `503`.

## Verification
- `python3 -m pytest -q tests/test_agent_bridge.py tests/test_chat_api.py tests/test_voice_backend.py tests/test_voice_bridge.py tests/agent_discovery/test_approval_transport_proof.py`
- Actual managed-runtime construction and six gate denials are exercised without inference.
- Actual process cancellation, wrong-owner rejection, replay lock release, isolated history, stale-generation rejection and safety drift are exercised.
- One real default-Hermes IP Address response returned in 7.23 seconds with tools empty before/after. This is one smoke sample, not a latency benchmark or proof of all Thai speech accuracy.

## Run
`python3 start-thai.py --no-browser`, then http://127.0.0.1:8730. Restart only the standalone Dashboard process to load backend changes; do not restart Hermes Gateway or change global routing. See `CHAT_UI.md` for explicit send/read controls.
