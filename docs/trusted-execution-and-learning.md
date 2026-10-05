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

Long-term memory is explicit and inspectable. Nothing is extracted automatically: a memory
is saved by "remember that …" in chat or on the console's **Memories** page, and removed by
"forget …" or on that page (`POST`, `GET`, `PATCH /memories/{id}`, `DELETE` archives).

| | Given to |
|---|---|
| Shared memory | the conversation model, all of them newest first within about 6,000 characters, so the model judges relevance (synonyms, Swedish) |
| Shared memory tagged `food` (tagged automatically when plainly about food, editable) | also the grocery agent |
| Private memory | nothing: it stays on the server and is only shown on the Memories page |

Coding agents, the supervisor and on-phone answers get no memories. "Remember privately …" in
chat is handled before the message is stored or any model sees it: the memory is saved as
private and the transcript only keeps a placeholder, so later turns can't pass it on.
"Forget …" only matches shared memories, since its reply repeats what was forgotten.

Provider runtime state now retains lifetime success and failure counts. After three observations,
the coding decision engine applies a bounded score adjustment of at most ten points. Capability,
cost, current cooldown, and task complexity therefore remain stronger controls than history.
