# Coding Decision Engine

The coding decision engine replaces a universal fixed provider order with an explainable,
deterministic `profile → filter → score → rank` process.

## Inputs

1. **Task profile** — inferred locally from request vocabulary and length. It contains a
   `simple`, `standard`, or `complex` classification plus traits such as `data`,
   `debugging`, `migration`, `security`, and `testing`.
2. **Runner registry** — operator-owned YAML manifests in `coding-runners/` define each
   runner's priority, relative cost tier, reasoning tier, and strengths.
3. **Runtime state** — PostgreSQL-backed cooldown and failure state removes unavailable
   providers and applies a bounded penalty for recent failures.
4. **Configured allowlist** — `ASSISTANT_CODE_AGENT_PROVIDERS` still determines which
   adapters may run. The decision engine only ranks that configured subset.

No model chooses permissions, repositories, or executable names. The profiler does not
send the request to another provider, and its classification cannot bypass a cooldown or
enable a runner absent from operator configuration.

## Initial policy

- Simple work favors lower-cost runners.
- Standard work balances reasoning tier, cost tier, and matching strengths.
- Complex work gives more weight to reasoning tier.
- Each matching declared strength adds a task-fit bonus.
- Recent failures apply a bounded score penalty.
- Active cooldowns always sort after available providers, regardless of score.
- Manifest priority and configured order provide deterministic tie-breaking.

The initial manifests encode the intended preference: Kimi Code is favored for simple
work, Claude Code with MiniMax is competitive for data/debugging/refactoring work, and
Codex is favored when architecture, migrations, or security make the task complex. These
are editable policy values, not claims learned from hidden benchmarks.

## Auditability

The coding execution output includes `agent_decision`, containing the profile, every
candidate's score and reasons, availability, and final provider order. The PR body also
contains a compact decision note. Provider credentials and raw error responses are never
included.

Inspect the policy registry and runtime state with:

```bash
curl http://localhost:8000/coding-runners
curl http://localhost:8000/provider-states
```

Future outcome tracking can adjust policy from measured success, latency, and cost. Until
that evidence exists, the scoring remains explicit and deterministic rather than
pretending to be a learned optimizer.
