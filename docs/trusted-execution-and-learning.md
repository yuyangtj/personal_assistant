# Trusted execution and learning

## Repository validation

Each repository manifest selects an operator-owned validation profile from
`validation-profiles/`. The worker runs its argument-vector commands without a shell after the
agent edits the isolated worktree and before it commits or pushes anything. A failed required
step stops publication; optional failures remain visible in the task output.

Profiles are discoverable at `GET /validation-profiles`.

## Review and revision

While a coding task waits for pull-request approval, the UI can create a revision child task.
`POST /tasks/{task_id}/pull-request-revision` requires the separate approval token, revision
instructions, and the exact reviewed head SHA. The worker fetches the registered PR branch and
refuses to edit it if that SHA has changed. A successful revision pushes a descendant commit to
the same branch and creates a new approval gate; the old SHA cannot be merged through the API.

## Deployment registry

Deployment destinations are operator-owned manifests in `deployment-targets/` and are listed by
`GET /deployment-targets`. The `assistant-deployment` workflow accepts only a registered target
and an immutable lowercase Git SHA. It records approval and intended stages, but deliberately has
no remote-shell executor yet. Adding one requires a target-specific credential, health check, and
rollback implementation rather than granting an LLM general server access.

## Memory and historical learning

Long-term memory is explicit and inspectable through `POST /memories`, `GET /memories`, and
`DELETE /memories/{id}` (archive). Nothing is extracted automatically. Relevant active memories
are selected lexically and supplied only to conversational tasks.

Provider runtime state now retains lifetime success and failure counts. After three observations,
the coding decision engine applies a bounded score adjustment of at most ten points. Capability,
cost, current cooldown, and task complexity therefore remain stronger controls than history.
