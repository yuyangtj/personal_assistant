# Assistant design: one character, many workers

Where the assistant is heading and the rules that get it there. It is the reference for new
features: when one does not fit here, change this document first.

## The goal

One assistant you talk to, eventually as a 3D character with a voice, that does things itself
and hands bigger work to other agents. It must know, and say truthfully, **what it can do**,
**what it may do without asking**, and **how well each thing usually goes**.

## The shape

```
You ── voice / 3D character / chat ──►  THE ASSISTANT  (one voice, one memory)
                                          │  knows what exists, what it may do,
                                          │  and how well each thing works
                                          ▼
                                ABILITIES (one registry)
              ┌────────────────────┼──────────────────────┐
        quick tools             background jobs           never
   read a repository,       coding agent, research,    merge, deploy, spend,
   notes, Klarna, search    shopping, routines         message people
   (answered in the turn)   (run, report progress,     (without you)
                             finish with a summary)
```

- **Only the assistant talks to you.** Workers return structured results and the assistant
  tells you about them, so there is one voice and one memory.
- **Two levels: the assistant, then workers.** A worker may use helpers inside (Claude Code
  has subagents), but to the assistant it is one job. A middle layer is added only for a
  domain with its own tools and memory (shopping, say), and it still looks like one worker.
  Every extra level adds delay, loses context and blurs who did what.
- **Every job has the same lifecycle:** proposed → approved (only if its band says so) →
  running, with progress → done or failed, with a summary and its artifacts. One card in the
  UI fits every kind of job. Coding runs already work this way.
- **The 3D character is a client.** It renders the same replies (text, emotion, choices,
  cards) the console does; the design does not depend on it.

## Boundaries: what it can and may do

Each ability is one entry in `abilities/*.yaml`: what it does (in words the assistant uses),
whether it is a quick tool or a background job, what it can touch, and its **band**:

| Band | Meaning | Examples |
|---|---|---|
| `free` | Done without asking | reading, search, notes, memories, lists, reminders |
| `review` | Done, and you review the result before it takes effect | a coding run ends as a pull request you merge |
| `ask` | You are asked first | a significant change to the outside world: calling an external API that changes something, a purchase, a message to a person |
| `never` | Not done by the assistant | merging or deploying without your approval token |

- The assistant's description of itself ("what I can do") is **generated from the
  registry**, never written by hand in a prompt. An ability that is not listed does not exist:
  the assistant says so and offers the nearest thing instead of inventing a step.
- Bands are enforced in code (tools, approval gates, the coding workflow), not by asking the
  model nicely. The prompt only explains them.
- Bands start conservative and move toward `free` as an ability proves reliable.

### Adding an ability

1. Add `abilities/<id>.yaml`: `name`, `summary` (what it does, as the assistant would say
   it, a full sentence; quote it if it contains `#`), `example`, `kind` (`quick` or `job`),
   `band`, and `requires` if it needs a key (`github_token`, `web_search`).
2. Make the code do it, with the band enforced there (an approval gate for `ask`, the pull
   request for `review`).
3. Add a scenario to `scripts/chat_eval`.

The assistant's description of itself (`abilities_prompt` in `app/abilities/`) and the
Stats page pick it up; an ability whose `requires` is not met is not offered.

## Quality: how well it does it

"Can it?" is a lookup; "how well?" is measured:

- **Track record**: per ability, how often it succeeds and how long it takes (`GET /stats`
  today; per ability next).
- **Scenarios**: each ability ships with a few conversations that exercise it
  (`scripts/chat_eval`). An ability's band is raised only when its scenarios pass.
- The assistant uses the track record to set expectations ("this usually takes about five
  minutes") and the dispatcher uses it to pick a worker or model.

## Context: one conversation, many topics

You talk to one character in one continuous conversation, but its working context is
organised by **topic**, like an assistant with one desk and many folders. Work items
(goals, lists, routines, each with a brief) are the folders.

Every turn the prompt is rebuilt from layers, never from the whole history:

1. **Who it is and what it can do**: the persona and the abilities registry.
2. **Who you are**: your curated memories (always loaded, kept small).
3. **The topic in focus**: that work item's brief, plus repositories when relevant.
4. **The last few exchanges** of this thread.

After the turn the topic's brief is updated (the brief writer does this today), so the next
turn and the workers get a short, current summary instead of a long transcript.

- **Focus**: explicit cues first (`#tag`, the item open on screen, "back to the Copenhagen
  trip"); otherwise the assistant asks. It always shows or says what it is talking about, so
  "no, the other thing" fixes a wrong guess in one sentence. Recovering cheaply matters more
  than guessing perfectly.
- **Workers get a job spec, not the chat**: target, change, constraints, and how to tell it is
  done (the coding spec writer does this today). This keeps workers focused and keeps the
  conversation (and anything injected into it) out of their reach.
- **Several topics at once**: jobs in different folders run in parallel, and "how is it
  going?" is answered across them.

| Kind of memory | Loaded into the prompt? |
|---|---|
| Persona and abilities | Always |
| Your memories | Always (small, curated) |
| Work item briefs | When that item is in focus |
| Recent turns of this thread | Always (a few) |
| Old transcripts | No; searched when needed |
| Notes | **Never** sent to a model unless you allow it |

## Speed

About 5 seconds per reply is acceptable at first. A tool call costs a model round trip, so
quick tools stay quick, anything slow becomes a background job, and the assistant says
"let me check" rather than going silent. Voice comes on top of this later.

## How the code gets there

| Step | State |
|---|---|
| Work items with briefs, focus, `#tag` | Done |
| Coding as a background job (spec, live progress, review, PR) | Done |
| Stats (reply times, failures) | Done, per kind of work |
| Chat agent told about repositories | Done (README and files of the selected repo) |
| Abilities registry with bands, generating "what I can do" (`abilities/`, shown on the Stats page) | Done |
| Read-only repository tools (list, read, search) in the chat agent, with each lookup shown on the reply's card | Done |
| More tools in the same agent (notes, memories, lists, reminders, routines, research, shopping, coding runs) | Done; research runs in the background, shopping is the agent's own tools (2026-10-25) |
| Providers' own tool calling | Done (2026-10-25). Main-agent eval: DeepSeek Flash 11/11, median 2.3–2.6 s, p90 under 5.3 s; MiniMax 11/11, median 4.8–6.2 s |
| One assistant for every message (triage, offers and the phone's router retired) | Done (2026-10-25): the chat agent is the only decider; only "note: …" is instant. Gate: `scripts/chat_eval/run.py --repeat 3` |
| Per-ability track record and scenarios; bands raised by evidence | Then |
| Voice and the 3D character on the same API | Later |

## Open questions

- Which external APIs count as a "significant change" (`ask`) and which are `free`; decided
  per ability as they are added.
- Whether notes become searchable by the assistant: needs your explicit permission first.
