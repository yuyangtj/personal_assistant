# Chats and tasks

Chats are where you think and communicate; tasks are work launched from them. The two are
linked by id, never by copying content, so task status is never duplicated or stale and
the task event stream stays authoritative.

Talking in a chat launches nothing. Posting a message stores the turn and, when it reads
as a request for work, returns a `proposal` the client offers as "create a task?":

```bash
MESSAGE=$(curl -s -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages \
  -H 'content-type: application/json' \
  -d '{"content":"Add authentication to the API"}')
echo "$MESSAGE" | jq .proposal
```

A proposal is advisory. Work starts only when the message is confirmed, which links the
task to the message it came from (`origin_message_id`):

```bash
MESSAGE_ID=$(echo "$MESSAGE" | jq -r .message.id)
curl -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages/$MESSAGE_ID/task \
  -H 'content-type: application/json' -d '{}'
```

Confirming the same message twice returns the original task rather than starting the work
again. A proposal marked `consequential` — anything touching a repository or a deployment
— is the case clients must confirm before a conversation launches costly work.

Going the other way, `POST /tasks/TASK_ID/chat-session` opens a task's conversation
(backfilling one for older tasks) and posts a reference to the task: its status, result
and artifacts. A follow-up continues the work as a new task that records its
`parent_task_id`:

```bash
curl http://localhost:8000/tasks/TASK_ID/context
curl -X POST http://localhost:8000/tasks/TASK_ID/follow-up \
  -H 'content-type: application/json' \
  -d '{"request":"Finish the Android part"}'
```

`GET /tasks/TASK_ID/context` is the curated view that follow-up prompts and chat
references are built from: the original request, goal, status, final answer, validation
verdict, artifact and pull-request references, and the failure message. Polling events,
raw tool responses, diffs, execution inputs and token usage stay in the event stream and
never reach a model prompt. Attach anything more by hand.

Inspect it using the returned ID:

```bash
curl http://localhost:8000/tasks/TASK_ID
curl http://localhost:8000/tasks/TASK_ID/events
curl http://localhost:8000/tasks/TASK_ID/pending-approval
```

The pending-approval endpoint returns the latest approval request while the task is
waiting for approval, or `404` when no approval is pending.

Inspect the capability registry:

```bash
curl http://localhost:8000/capabilities
curl 'http://localhost:8000/capabilities?include_disabled=false'
```

Inspect the repository registry:

```bash
curl http://localhost:8000/repositories
```

The built-in UI presents these repositories when creating a task. API callers select one
by its stable ID (or registered alias):

```bash
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{
    "request":"Add a cohort-retention example and tests",
    "repository_id":"analytics-agent-playground",
    "required_capabilities":["coding","pull_request_creation"]
  }'
```

A caller may request capabilities without naming an executor:

```bash
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{
    "request":"Inspect this repository",
    "required_capabilities":["repository_analysis"]
  }'
```

That example routes to the coding PR adapter only when its operator configuration is
enabled. Otherwise it fails safely; no fallback executor silently receives work it
cannot perform.

## Routines: repeatable jobs

A routine is a saved instruction the assistant carries out on a schedule, or on demand:
"every month on the 1st at 9, analyze my Klarna spending", or "every Monday at 8, tell
me when Zoégas coffee is under 50 kr at Willys". It is the assistant's equivalent of a
skill; there is no separate workflow engine (`app/chat/routines.py`).

- **Routed once.** When a routine is saved, its instruction is triaged like a chat
  message. The route is stored on the schedule (`capabilities`): Klarna and the stores go to
  the shopping agent, web questions to research, coding work to the supervisor. Every run
  goes there. Routines saved before this use the keyword rules when they fire.
- **Compares with itself.** Each run gets the last three results of the same routine
  (`routine_results`), so it can say "up 18% on last month" or "down from 54 to 45 kr".
- **Delivers.** The result posts in the routine's chat and is pushed to the phone. A
  **watch** (the instruction says "tell me when/if …" or "only if …") still posts every
  result, but only pushes when the agent marks it `"notable": true`.
- **From chat:** "what routines do I have?" lists them with Run and Stop choices; "run my
  Klarna review now" runs one in the current chat; "stop the coffee watch" cancels one,
  asking which when several match.
- **Prices outside the grocery stores** need the research agent, so a Tavily or Brave
  search key has to be configured.
