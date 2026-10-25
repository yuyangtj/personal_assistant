# Smoke test on the phone

About ten minutes, after a deploy that changes how the assistant answers. It covers what
the automated tests can't: the real phone, voice, notifications, the share sheet, and the
real stores and Klarna data. The chat eval (`scripts/chat_eval/run.py --main-agent
--repeat 3`) and the browser tests (`uv run pytest -m ui`) should already be green.

Open the app on the Pixel and start a new chat. Send each message, and check that the
reply does what the right-hand column says. Note anything that's off, with the time, so
the run can be found on the Stats page.

| # | Say or do | It should |
|---|---|---|
| 1 | "remember I like oat milk, add it to groceries, and remind me tomorrow at 9 to buy it" | Do all three in one reply. Memories, the #groceries list and a reminder for 09:00 tomorrow all exist |
| 2 | "add eggs and butter to groceries" | Add two entries, not one called "eggs and butter" |
| 3 | "How has my Klarna spending changed over the last months?" | Answer in a few seconds with a monthly chart. Saying how far the data goes is fine |
| 4 | "pls do" (after the answer offers one next step) | Do that step, without asking which one |
| 5 | "what's on offer at Willys this week?" | Use the store tools (the reply card shows "Looking at Willys") and name real offers |
| 6 | "note: the cabin wifi password is on the fridge" | Save it at once, with an "Open Notes" link |
| 7 | Hold the mic and ask "what's the time?" | Answer on the phone itself, spoken, without waiting for the server |
| 8 | "set a timer for 2 minutes" | Show a card with Do it / Cancel. After Do it the phone's timer runs, and it rings at the end |
| 9 | Share a web page to the assistant from Chrome and ask "what is this about?" | Answer from the shared page |
| 10 | "how are my coding runs going?" | List the runs and their state, or say there are none |

Then two things that take time:

- **The reminder from step 1** arrives as a notification tomorrow at 09:00. Tapping it opens
  the chat.
- **A routine:** "every day at 18:00, tell me what I spent with Klarna today" sends its first
  report at 18:00. Stop it afterwards with "stop the Klarna routine".

## When something is off

- A wrong answer or a wrong tool: note the message, and the reply card's steps if it shows
  them.
- A slow reply (well over 10 s for 1–6): check the Stats page for that kind of work.
- Nothing happens: check that the server is up (`/health`) and that the app reaches it.
  The console says "Updating, back in a moment…" during a deploy.
