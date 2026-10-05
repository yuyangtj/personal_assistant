# Android

Two Android clients for the Personal Assistant:

| Folder | What it is | Status |
|---|---|---|
| [`shell/`](shell/README.md) | The app: the web console in a native shell, plus voice, phone actions (timers, alarms, calendar drafts) and Gemini Nano for small requests | **In use** |
| [`avatar/`](avatar/README.md) | The 3D-character client: Kotlin host + Unity avatar with lip-sync, its design sources and tools | Paused; returns once the backend is in good shape |

Build the app:

```shell
zsh tools/build_shell_android.sh            # console at https://assistant.yangyu.se
adb install -r shell/app/build/outputs/apk/debug/app-debug.apk
```
