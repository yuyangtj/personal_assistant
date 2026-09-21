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

The current foundation supports `proposed → approved` and `proposed → rejected`. It
stores every transition in `workflow_run_events`. Approval only authorizes the proposal;
it does **not** execute GitHub, shell, migration, or deployment operations yet. This is
intentional: each privileged stage will be connected to a narrowly scoped trusted
executor separately.

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

Approve it with the separate approval credential:

```bash
curl -X POST http://localhost:8000/workflow-runs/WORKFLOW_RUN_ID/decision \
  -H 'content-type: application/json' \
  -H 'X-Assistant-Approval-Token: YOUR_SEPARATE_APPROVAL_SECRET' \
  -d '{"decision":"approve"}'
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

## Next execution increment

The next increment should connect only the `coding-change` workflow to the existing
coding task and PR machinery. Advancing a stage must be conditional on the preceding
stage's persisted evidence. Repository activation and production deployment remain
disabled until their own trusted executors, health checks, and rollback behavior exist.
