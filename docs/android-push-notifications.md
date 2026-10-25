# Investigation: reminder push notifications missing on Android

A user reported that a reminder notification did not show up on their phone. This
traces the whole delivery path from the server to the Android client, cites the
exact code on both ends, and lists the failure causes found while reading it.

## The push path

**Service: [ntfy](https://ntfy.sh) (self-hosted or ntfy.sh), not FCM.** The server
makes one HTTP POST per push, to a topic URL, no persistent connection or Google
service involved.

- `Notifier` protocol, `NullNotifier` (no-op when unconfigured) and `NtfyNotifier`
  (the real sender): `app/notify.py:18-60`.
- `NtfyNotifier.send` posts to `self.topic_url` with `Title`, `Priority` and
  (if `public_url` is set) `Click` headers: `app/notify.py:42-60`.
- `build_notifier` returns `NullNotifier` unless a topic URL is given:
  `app/notify.py:67-70`.
- Configuration: `Settings.ntfy_topic_url` / `ntfy_token` / `public_url`
  (`app/config.py:36,45-46`), read from `ASSISTANT_NTFY_TOPIC_URL`,
  `ASSISTANT_NTFY_TOKEN`, `ASSISTANT_PUBLIC_URL` (`app/config.py:215,243-244`).
  Wired into the `worker`, `api` and `coding-worker` containers in
  `compose.remote.yaml:67,144-146,205-206`.
- Senders: reminders fire from `Scheduler._fire` (`app/schedules.py:203-220`,
  `abilities/reminders.yaml` is the catalog entry the chat agent uses to create
  them). The same notifier also pushes for routine runs, PRs ready for review,
  failed coding runs and finished deployments (`app/worker.py:605-646,689-691`,
  built in `app/worker.py:923-925`).

## What the Android app does with it

**The app does not subscribe to ntfy (or anything else) itself.** A full search of
`android-assistant/shell` turns up no ntfy client code, no Firebase/FCM dependency
(`android-assistant/shell/app/build.gradle` has no `firebase`/`messaging`
dependency), and no `NotificationManager`, `NotificationChannel` or
`POST_NOTIFICATIONS` permission anywhere under
`android-assistant/shell/app/src/main/`.

What the app *does* have is a deep link handler for when a notification is tapped:

- `AndroidManifest.xml:38-43` declares an `autoVerify` intent filter for
  `https://${consoleHost}/*`, so Android opens `MainActivity` (not a browser) for
  links that match the console's host — this is what makes a tapped push notification
  (or any other link into the console) land in the app.
- `MainActivity.onNewIntent` (`MainActivity.kt:163-169`) and the cold-start path in
  `onCreate` (`MainActivity.kt:157-160`) load that link via
  `ConsoleUrls.startUrl`/`sameOrigin` (`ConsoleUrls.kt:17,28-31`).
- `ConsoleBridge.kt`, `ServerActions.kt` and `AssistantService.kt` (the voice-interaction
  service) have no notification- or push-related code at all; they handle the console
  bridge, phone actions and the assistant role respectively.

This matches `docs/personal-assistant-target-architecture.d2:20`, which models
"Push notifications / ntfy" as a client distinct from the "Android app" box — i.e. by
design, something other than this repo's app is expected to hold the ntfy
subscription on the phone (in practice, a separate, third-party ntfy client app the
user installs and subscribes to the same topic by hand). There is no setup
instructions for this anywhere in the repo; `android-assistant/README.md` and
`android-assistant/shell/README.md` only say that "push notifications and links into
the console open in the app," without saying what delivers them.

## Failure causes found in the code

1. **No in-app push reception (biggest, unaddressed).** Since the Android app in
   this repo has no ntfy/FCM client, a reminder notification only reaches the phone
   if a separate ntfy client app is installed, subscribed to the exact topic/token
   the server is configured with, holds Android's notification permission, and is
   not killed by battery/background restrictions. Any of those being wrong or absent
   produces exactly the symptom reported — the server believes the push succeeded,
   nothing in this repo is wrong, and still nothing appears on the phone. Fixing this
   for real means building (or documenting the manual setup of) a push subscriber,
   which is a new feature/redesign and out of scope for this change; see
   "Not addressed" below.
2. **Misconfiguration is invisible.** If `ASSISTANT_NTFY_TOPIC_URL` is unset,
   `build_notifier` returns `NullNotifier`, whose `send()` logs only at `DEBUG`
   (`app/notify.py:22-25`) — invisible at any normal production log level, so a
   deployment with push accidentally turned off gives no signal anywhere.
3. **A failed push for a specific reminder was untraceable (fixed here).** A
   transient ntfy failure (network error, non-2xx) is caught and logged at
   `WARNING` in `NtfyNotifier.send` (`app/notify.py:50-59`), but that log line names
   neither the reminder nor the schedule, and the only caller for reminders,
   `Scheduler._fire`, discarded the boolean return value entirely
   (`app/schedules.py:218` before this change) — so a lost reminder push left no way
   to tell which reminder it was from the logs.
4. **Reminder pushes don't deep-link anywhere specific.** Unlike other pushes
   (e.g. `app/worker.py:609-610,624` pass `path=f"/?task={task.id}"`), the reminder
   push in `app/schedules.py:218` passes no `path`, so a tapped reminder opens the
   console root rather than the originating chat. This doesn't explain a missing
   notification, just a rough edge in the same code path; left as-is since it needs
   a frontend route (`app/web`, out of scope for this change) to be useful.

## The fix in this change

`Scheduler._fire` (`app/schedules.py:218-219`) now logs a `WARNING` naming the
schedule id when `notifier.send` returns `False` for a reminder, so a failed push
is traceable to the specific reminder instead of vanishing into an unlabeled log
line from `app/notify.py`. Covered by
`tests/test_schedules.py::test_failed_reminder_push_is_logged_with_the_schedule_id`,
which fails without the fix (no warning is logged) and passes with it.

This does not fix cause 1 or 2 above — both would need either a design decision
(ship an in-app push subscriber, or document/verify the external ntfy client setup)
or a behavior change to logging defaults outside the scope of "smallest obvious
fix," so they're left as follow-up work.

## Remaining causes not addressed

- No in-app ntfy/FCM subscription — the Android app cannot receive pushes itself
  (cause 1).
- Silent `DEBUG`-level logging when push is unconfigured (cause 2).
- No retry after a single failed delivery attempt (`app/notify.py:42-60` makes one
  HTTP POST and gives up).
- No deep link path for reminder pushes (cause 4).
