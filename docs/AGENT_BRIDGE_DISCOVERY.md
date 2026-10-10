# Hermes agent bridge discovery (bounded, read-only)

## Outcome
No safe supported one-process bridge was found that gives a host callback veto over **every** tool dispatch (including arbitrary file writes/email) while preserving the user's default model and managed session. `AIAgent` accepts callback hooks, but those are progress/notification hooks, not universal pre-dispatch authorization. `enabled_toolsets=[]` selects an empty initial tool name set in `model_tools._select_tool_names` (`model_tools.py:315-337`), but that alone is not proof: `AIAgent` initialization and final tool construction must be reviewed for late-added tools; do not run a model against this as a safety boundary.

## Native approval surface
Installed source: `/Users/sufeyanwaedueraman/.hermes/hermes-agent` (dirty; inspected only).

- `tools.terminal_tool.set_approval_callback(cb)` (`terminal_tool.py:102-104`) stores a thread-local callback. `tools.approval_context._resolve_cli_approval_callback(approval_callback=None)` prefers an explicit callback then resolves this slot (`approval_context.py:194-202`). Terminal dangerous-command checks use it through `_check_all_guards` (`terminal_tool.py:148-153`). This is **selective dangerous-command classification**, not a pre-dispatch hook for every terminal call. Safe-looking commands may pass without callback.
- `hermes_cli.approval_transport.ApprovalRequest.create(*, command, description, pattern_key, pattern_keys, session_key, surface, allow_session, allow_permanent, timeout_seconds=300)` creates immutable request id + SHA-256 digest, bound to session key. `request.respond(choice)` returns `ApprovalDecision(request_id, request_digest, choice)`.
- `invoke_approval_transport(present, request, *, timeout_seconds, poll_interval=1.0, on_poll=None, is_interrupted=None)` bounds worker count/time, validates exact request id/digest, choice, and deadline; returns deny on callback exception, invalid, stale, timeout, interruption, capacity/start failure (`approval_transport.py:103-185`). This is a correlated transport utility, **not** itself a universal dispatch gate. Approval integration is only reached from terminal dangerous approval flow (`tools/approval_prompt.py:223-246`).
- ACP edit approval (`acp_adapter/edit_approval.py`, `acp_adapter/server.py`) is scoped to ACP file/edit protocol; it is not a general JSON subprocess bridge and does not gate arbitrary `write_file`/email tools.
- `tools/write_approval.py` governs memory/skill cross-session writes when enabled; it is configuration-dependent and does not gate general workspace file writes. No email-wide approval hook identified.

## Tool-off conclusion
`model_tools._select_tool_names(enabled_toolsets, disabled_toolsets, quiet_mode)` starts empty when `enabled_toolsets is not None`, then selects listed toolsets and subtracts disabled ones. Empty list is only one stage. `AIAgent.__init__(..., enabled_toolsets=None, disabled_toolsets=None, ... tool_start_callback=None, ...)` forwards to `agent.agent_init.init_agent`; callbacks listed on the constructor are lifecycle/UI hooks, not authorizer signatures. Consequently no LLM “no-tools” greeting was run: final-tool disabling was not verified end-to-end and a bounded inference was not needed to establish that.

## Recommendation
Use a dedicated host-owned bridge adapter only after upstream exposes/accepts a universal pre-dispatch authorization hook (or an isolated tool registry that provably contains no unrestricted side-effect tools). Keep the callback per invocation/session, bind immutable request id + digest + session id, expire decisions, and deny on transport/validation errors. Until then, do not claim native approval transport gates all side effects. The existing CLI dangerous-command approval is useful defense-in-depth for classified terminal commands, not a full bridge authorization policy.

## Proof
Run from the dashboard repo:

```sh
python3 -m unittest discover -s tests/agent_discovery -v
```

The fixture imports the installed approval transport module and verifies deny/once, expired response, wrong digest, wrong request id, and callback exception, without invoking Hermes, an LLM, or a real command/write. No project runtime/backend/UI code was changed. Proof location: `tests/agent_discovery/`.
