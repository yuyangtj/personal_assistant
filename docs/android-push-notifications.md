# How push notifications reach the phone

The server pushes through [ntfy](https://ntfy.sh), not Firebase. The Android app in
this repo does not receive pushes itself: the separate ntfy app does, subscribed to the
same topic. Tapping a notification opens its link, which the Assistant app claims
because it handles links to the console's host.

- **Sender:** `app/notify.py`. One HTTP POST per push to `ASSISTANT_NTFY_TOPIC_URL`
  (with `ASSISTANT_NTFY_TOKEN` if the topic is protected). When `ASSISTANT_PUBLIC_URL`
  is set, a `Click` link into the console is added. With no topic URL, pushes are off.
- **What pushes:** reminders (`app/schedules.py`), and the worker's results, PRs ready
  for review, failed runs and deployments (`app/worker/`).
- **Setup:** see `.env.remote.example`. Install the ntfy app, subscribe to a
  hard-to-guess topic, and put its full URL in the server's environment.

## When a push doesn't show up

1. **On the server:** check that `ASSISTANT_NTFY_TOPIC_URL` is set in the worker's
   environment. A failed reminder push logs `Reminder push failed for schedule <id>`,
   next to `Push failed: <reason>` from `app/notify.py`. There is no retry.
2. **On the phone:** check that the ntfy app is subscribed to that exact topic, may
   post notifications, and is not limited by battery saving.
