# `lazyclaw/android/` — Android device control (no root)

Starting point for giving the agent control of a physical Android phone, the
same way `lazyclaw/computer/` gives it control of the host machine. Built on
**ADB only** — no root, no bootloader unlock. `adb` already grants screen
capture, the UI hierarchy, and input injection as the `shell` user, which is
everything an LLM needs to operate the phone like a human.

> Verified live on a Xiaomi 15 (HyperOS 3 / Android 16): `screenshot`,
> `ui_dump`, `tap`, `current_app`, `list_packages` all working over USB.

## Files

| File | Role |
|------|------|
| `executor.py` | `AndroidExecutor` — ADB primitives, standard `{"success","data"/"error"}` envelope. |
| `security.py` | `AndroidSecurity` — immutable validator: protected-package + destructive-command blocklists. |

## The agent loop (what you'll build next session)

```
   ┌─────────────────────────────────────────┐
   │  screenshot()  +  ui_dump()              │  ← SEE (pixels + element tree)
   ├─────────────────────────────────────────┤
   │  LLM picks one action from the UI tree   │  ← THINK
   ├─────────────────────────────────────────┤
   │  tap() / swipe() / input_text() /        │  ← ACT
   │  key_event() / launch_app()              │
   └──────────────┬──────────────────────────┘
                  └── repeat until goal met
```

`ui_dump()` returns XML where every node carries `text`, `resource-id`, and
`bounds="[x1,y1][x2,y2]"`. Feed that tree to the brain; to tap an element,
compute the bounds center and call `tap(cx, cy)`. This is far cheaper and more
reliable than asking the model to point at raw pixels — same philosophy as the
browser module's "Semantic Snapshots" (accessibility tree over screenshots).

## Quick start

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"   # adb on PATH
```

```python
import asyncio
from lazyclaw.android import AndroidExecutor

async def demo():
    phone = AndroidExecutor()                  # serial=... if multiple devices
    print((await phone.devices())["data"])
    shot = await phone.screenshot()            # {"image_base64","format":"png"}
    tree = await phone.ui_dump()               # {"xml": "<hierarchy>…"}
    await phone.launch_app("com.android.settings")
    await phone.tap(540, 1200)

asyncio.run(demo())
```

## Integration TODOs (deferred to your next session)

1. **Manager + connector** — add an `AndroidManager` mirroring
   `computer/manager.py`, and optionally expose it over the existing
   `computer/connector_server.py` WebSocket so the phone can be driven remotely
   through the FRP tunnel you already built (`docs/REMOTE_ACCESS.md`).
2. **Skills** — register thin skills (`android_screenshot`, `android_tap`,
   `android_ui_dump`, `android_launch_app`, …) in the skill registry so the
   brain can discover them via `search_tools()`. Keep them in a new
   `android` permission category (default `ask` for taps that mutate state).
3. **Cordless control** — pair once with Android 11+ **Wireless debugging**
   (`adb pair <ip:port>` then `adb connect <ip:port>`), pass the serial to
   `AndroidExecutor(serial=...)`, and the cable is no longer needed.
4. **Richer text input** — `input_text` only handles ASCII; install
   **ADBKeyboard** for unicode/emoji, or push text via the clipboard.
5. **scrcpy (optional)** — `brew install scrcpy` for a live mirror window while
   you develop, so you watch the agent act in real time.

## Why no root is needed

Root would only add: reading *other apps'* private files, true background
daemons, and system-image modification. UI automation — see the screen, read the
element tree, tap/type — is fully available to the `shell` user. This is exactly
how Appium and `uiautomator2` work.
