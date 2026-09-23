# Controlled Workflows

The workflow layer separates model reasoning from system authority. A model or user may
propose a workflow run, but only application code can validate its manifest, persist its
state, and accept an authenticated decision.

## Built-in workflows

| Workflow | Purpose | Privileged boundary |
| --- | --- | --- |
| `coding-change` | Change an already registered repository | Review the exact PR head before trusted merge |
| `repository-onboarding` | Add a repository to the trusted registry | Approve registry and permission scope before activation |
| `assistant-deployment` | Deploy a reviewed immutable assistant commit | Separate deployment approval before migration or promotion |

The manifests in `workflows/` define the ordered stages. They are operator-controlled
code, not model output. Every enabled manifest must contain an explicit approval stage.

## Current lifecycle

All transitions are stored in `workflow_run_events`. A proposal follows either
`proposed → rejected` or `proposed → approved → running → completed/failed/cancelled`.
Approval only authorizes the proposal; an explicit start is still required.

`coding-change` has a trusted start adapter. Starting it creates exactly
one idempotent task requiring `coding` and `pull_request_creation`, fixed to the registered
repository in the approved input. Its workflow stage is synchronized from persisted task
state: execution maps to `implement`, validation to `validate`, PR approval to `review`,
and a completed merge to `merge`. While at `review`, marking a draft ready and approving
its merge remain two distinct, separately audited actions gated by exact-SHA CI results.
An explicitly authorized revision supersedes the previous task, transfers the workflow to
a child task on the same PR branch, and loops the run back through implement, validate,
CI, and review.

`assistant-deployment` is the explicit post-merge adapter. A completed coding task with
a trusted merge artifact can propose one idempotent deployment run. Approval uses the
separate assistant approval token. Start verifies that the immutable merge SHA is still
the exact head of the registered base branch, then dispatches the deployment target's
registered GitHub Actions workflow. Sync records the matching Actions run as completed
or failed. The API never receives a Docker socket or arbitrary SSH destination.

Create a proposal:

```bash
curl -X POST http://localhost:8000/workflow-runs \
  -H 'content-type: application/json' \
  -d '{
    "workflow_id":"coding-change",
    "input":{
      "repository_id":"analytics-agent-playground",
      "request":"Add a cohort-retention example and tests"
    }
  }'
```

A consequential coding request can also be proposed directly from its chat message. The
endpoint is idempotent, keeps the workflow linked to the chat, and links the original
message to the task only after the approved workflow is explicitly started:

```bash
curl -X POST \
  http://localhost:8000/chat-sessions/CHAT_ID/messages/MESSAGE_ID/workflow-run \
  -H 'content-type: application/json' \
  -d '{"repository_id":"analytics-agent-playground"}'
```

Approve it with the separate approval credential:

```bash
curl -X POST http://localhost:8000/workflow-runs/WORKFLOW_RUN_ID/decision \
  -H 'content-type: application/json' \
  -H 'X-Assistant-Approval-Token: YOUR_SEPARATE_APPROVAL_SECRET' \
  -d '{"decision":"approve"}'
```

Then explicitly start the approved coding workflow:

```bash
curl -X POST http://localhost:8000/workflow-runs/WORKFLOW_RUN_ID/start
```

After an approved merge, propose the registered deployment directly from its task:

```bash
curl -X POST http://localhost:8000/tasks/TASK_ID/deployment-workflow \
  -H 'content-type: application/json' \
  -d '{"deployment_target_id":"personal-assistant-production"}'
```

Approve that returned workflow run with `/decision`, then call `/start`. The task panel
provides the same **Propose deployment → Approve deployment** sequence and polls the
GitHub Actions result.

The worker synchronizes linked workflow state automatically. The sync endpoint is also
available for recovery and reconciliation:

```bash
curl -X POST http://localhost:8000/workflow-runs/WORKFLOW_RUN_ID/sync
```

Inspect durable state and the audit trail:

```bash
curl http://localhost:8000/workflows
curl http://localhost:8000/workflow-runs
curl http://localhost:8000/workflow-runs/WORKFLOW_RUN_ID/events
```

Workflow runs may link to a chat session and task. Those records, messages, tasks, and
events all live in PostgreSQL, so replacing API or worker containers does not erase the
conversation. Deployments must preserve the database volume and run ordered migrations.

Repository onboarding remains proposal-only until its trusted activation adapter is
implemented. Production rollback remains manual: a failed deployment is recorded and
the existing release is left for the operator to inspect or redeploy explicitly.
