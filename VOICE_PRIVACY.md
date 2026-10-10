# Voice privacy

- Dictation audio is handled by local faster-whisper through the existing managed Hermes runtime; it is never uploaded by this dashboard.
- Synthesized speech uses Edge TTS with the configured Thai voice Niwat. Text goes to the Edge speech provider; do not enter confidential material unless that provider's privacy terms are acceptable.
- Audio is held in unique temporary files only while processing and deleted in `finally`/context cleanup. Audio request cap: 4 MiB and 30 seconds. TTS text cap: 2,000 characters.
- No browser microphone capture is started automatically. The explicit dictation control captures audio; transcript remains for user review. TTS runs only on the explicit test/speak action.
- Dashboard binds to 127.0.0.1 and enforces Host, Origin, and per-page token checks. Responses are no-store. No `.env` parsing, credential display, raw exception echo, or transcript logging.
- Agent execution, commands, background jobs, and Hermes chat are disabled pending verified approval integration; use Hermes Desktop for actual tasks.
