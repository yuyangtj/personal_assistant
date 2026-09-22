# Persistent Provider Runtime State

Provider availability is operational state, not model judgment. Coding runner failures
are stored in PostgreSQL under scoped keys such as `coding:kimi`,
`coding:minimax-claude`, and `coding:codex`.

Each row records:

- cooldown expiry;
- last sanitized error category;
- consecutive failure count;
- last success and failure times;
- last update time.

When a coding provider reports a rate limit or exhausted quota, the fallback runner writes
the configured cooldown before trying the next provider. A new worker process reads the
same row and skips that provider until the cooldown expires. Successful execution clears
the cooldown and failure count. Credentials, prompts, model output, and raw provider
responses are never stored in this table.

The configured durations remain:

| Variable | Default |
| --- | --- |
| `ASSISTANT_CODE_AGENT_RATE_LIMIT_COOLDOWN_SECONDS` | `300` |
| `ASSISTANT_CODE_AGENT_QUOTA_COOLDOWN_SECONDS` | `3600` |

Operational state is visible without credentials:

```bash
curl http://localhost:8000/provider-states
```

Expired rows may remain for audit and operational visibility; `available` is calculated
from the current time and cooldown expiry. This first integration controls coding runners.
The same store is intentionally generic so conversation, manager, speech, and tool
providers can adopt it without sharing cooldowns across scopes.
