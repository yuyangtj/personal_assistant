# Android

The Android app for the Personal Assistant, in [`shell/`](shell/README.md): the web
console in a native shell, plus voice and phone actions (timers, alarms, calendar drafts).
(The paused Unity avatar client was removed; see the main README.)

Build the app:

```shell
zsh tools/build_shell_android.sh            # console at https://assistant.yangyu.se
adb install -r shell/app/build/outputs/apk/debug/app-debug.apk
```
