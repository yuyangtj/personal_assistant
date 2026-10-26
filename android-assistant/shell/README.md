# Android app

The phone app is the assistant's web console in a native shell. Every console feature
reaches the phone with each server deploy, and the app adds only what a phone can do,
offered to the console through a small bridge (`ConsoleBridge`):

- **Voice in and out**: a microphone button in the composer (on-device speech recognition
  when available); spoken questions get spoken answers.
- **Phone actions**: timers and calendar drafts, always confirmed on screen first.
- **Reminders and alarms ring from the app**: it fetches the coming two weeks from the
  server (`GET /phone/alarms`, `AlarmSync`) when it opens, after each reply and after each
  ring, and sets them with Android's exact alarms (`AlarmScheduler`), so they ring with the
  app closed and again after a reboot. An alarm rings full screen with the alarm sound until
  dismissed or snoozed; a reminder is a notification with sound. The app tells the server
  which ones it has (`POST /phone/alarms/ack`), and the server sends no ntfy push for those.
- **No decisions on the phone**: every message goes to the server, whose chat agent sees
  the whole conversation. The phone only listens, speaks and runs confirmed actions.
- **Login**: the console's sign-in is asked once and kept encrypted with a key in the
  phone's keystore.
- **Links**: push notifications and links into the console open in the app; other links
  open in the browser.

The bridge is registered only for the console's own origin, and the page can never
describe a phone action itself. When a server reply proposes one (a timer or a
calendar draft), the page names only the reply's card; the app reads the action from the
server with the console login (`ServerActions`), checks it, and shows its own label to
confirm. The page then refers to it only by the id the app gave it.

## Build and run

```shell
zsh ../tools/build_shell_android.sh                      # console at https://assistant.yangyu.se
zsh ../tools/build_shell_android.sh -PconsoleUrl=http://10.0.2.2:8765   # local server, emulator
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The server address can also be changed in the app when it can't reach the console.
Debug builds expose the WebView to Chrome DevTools (`chrome://inspect`).
