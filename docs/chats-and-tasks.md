# Chats and tasks

Chats are where you think and communicate; tasks are work launched from them. The two are
linked by id, never by copying content, so task status is never duplicated or stale and
the task event stream stays authoritative.

Posting a message stores the turn and says what the server did with it (`decision`):
usually "main agent", and the console then queues the chat agent's reply as a task, which
links it to the message it came from (`origin_message_id`):

```bash
MESSAGE=$(curl -s -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages \
  -H 'content-type: application/json' \
  -d '{"content":"How much did I spend at Lidl last month?"}')
```

```bash
MESSAGE_ID=$(echo "$MESSAGE" | jq -r .message.id)
curl -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages/$MESSAGE_ID/task \
  -H 'content-type: application/json' -d '{}'
```

Asking for the same message's task twice returns the original task rather than starting
the work again. Coding work is started only by the chat agent's `coding_start` tool, and
only ever ends in a draft pull request.

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

- **Run by the chat agent.** Each run goes to the chat agent, with its tools (Klarna, the
  stores, research, coding runs). Routines saved before 2026-10-25 may name a specialist
  in `capabilities`; they still go there.
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

## Context from other Android apps

Both ways in are started by the user, and nothing is sent until they send it.

- **Share to Assistant.** Any app's Share menu, and "Ask Assistant" on selected text.
  Links and text go into the chat box to edit. A shared text file is uploaded into the
  chat (`POST /chat-sessions/{chat}/files`), and the upload target that recognizes it
  (`detect` in `app/chat/uploads.py`, today the Klarna export) handles it. A cold start
  keeps the item until the page says hello.
- **The phone's assistant.** Once picked under Default apps → Digital assistant app,
  long-press power opens the console. When "Use text from screen" is on, the screen's
  visible text is attached (password fields are skipped, and secure windows such as
  banking apps share nothing; `ScreenText.kt`). It shows as a removable chip and goes with
  the next message as an `attachment` block.
- **Where it goes.** The chat agent and research get the attachment as untrusted data.
  Coding agents never get it (`UNTRUSTED_CONTEXT_EXCLUDED`).
- The assistant role requires a speech recognizer in the app. `AssistantRecognitionService`
  forwards to Google's, so making the app the assistant doesn't break voice input
  elsewhere.

## Charts and tables in replies

A reply can carry `chart` and `table` blocks (`app/chat/blocks.py`): data only (kind, title,
axes, series of `[x, y]` points), never markup or script. The console draws them itself
(`app/web/static/charts.js`: hover or tap for values, legend toggles, a hidden table for
screen readers), and the Android shell shows them because it wraps the console. The native
avatar app ignores blocks and shows the reply text.

- Only tools run by the server create them: a tool result may carry a `blocks` list, and
  `take_tool_blocks` validates it, attaches it to the reply and strips it from what the model
  sees. A model's own JSON can never ask for a chart (`model_blocks`).
- Limits: 6 series, 200 points per series, 50 table rows, 24 kB per block.
- Today `klarna.spending` builds one: bars per month (3 or more months) or per merchant
  (top 12). To chart something else, build the block in that tool the same way.
