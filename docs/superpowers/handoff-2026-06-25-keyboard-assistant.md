# Next-session handoff — LazyClaw on-device AI (keyboard + voice assistant)

Paste this whole file as the first message of the next session.

---

## Who I am / the vision
I'm building **LazyClaw as an on-device Android AI ecosystem** on my **Xiaomi Mi 15** (Snapdragon 8 Elite, 8 big cores, 16 GB RAM, Android 16 / HyperOS). Everything AI runs **locally on the phone** — private, offline. Three pieces:
1. **LazyClaw Flutter app** (`~/Desktop/Code_Projects/lazyclaw/mobile/`) — runs a local GGUF LLM on-device via **llamadart**, has local chat + (NEW) a **"Hey Lazy" voice assistant**.
2. **LazyKeyboard** (`~/Desktop/Code_Projects/private-ai-keyboard/`, branch `lazyclaw-fork`) — a LeanType/HeliBoard GPL fork with on-device **Writing Tools** + **Lazy AI** + **Lazy voice**, sharing the same kind of GGUF model. Package `com.leanbitlab.leantype.offline.debug` (debuggable).
3. **The real LazyClaw server** (the big repo) — FastAPI + ECO router + MiniMax/Claude, internet + tools. The hybrid endgame is the on-device AI **delegating complex/internet tasks to this server**.

I prefer momentum (build, don't over-ask), no popup questions, and I test on my phone (it's USB-connected; unlock PIN **159000**).

## What was built last session (all committed + on my phone)
**Keyboard (build 3886, `private-ai-keyboard` head `b32ab73`):**
- 9-action Gboard-style **Writing Tools** menu, **branded** (emerald `#10B981` pills + per-action icons + "✨ Lazy AI" badge).
- Fixed THREE chained load bugs: pre-check `getModelPath()`→`resolveActiveModelPath()`; model-load failed because the llama.rn binding's `isGGUF` opens `model` via ContentResolver → bare path = "No content provider" → fix = pass catalog paths as **`file://`**; plus mmap + `<think>` stripping (Qwen3 is a thinking model).
- **Preload** model on keyboard-open; **6-of-8 threads** (was capped at 4).
- **Lazy AI** (✨ badge): field text = a request → generate/answer → **editable review card** (Replace/Discard + **refine chips**: 🎤 Edit / Shorter / Formal / Fix that re-run the AI in place).
- **Lazy voice** (🎤 Speak): on-device STT (Android `SpeechRecognizer`) → Lazy AI → review card; **"Done" button** to finish talking.
- Command-vs-dictation prompt: dictated sentence → corrected; instruction → carried out.
- OTA: `lazyclaw/mobile/dist/keyboard.apk` + `keyboard-version.json`, served by the gateway, app "AI Keyboard → Install/Update".

**App (build v1.21.8+68, `lazyclaw` repo, `mobile/`):**
- **Hey Lazy voice assistant**: `lib/assistant/lazy_assistant_controller.dart` + `lib/screens/assistant/lazy_assistant_screen.dart`. Mic → `speech_to_text` (on-device) → the **local LLM** (`localLlmEngineProvider`, same llamadart engine as local chat) → `flutter_tts`. Route `/assistant`, entry = ✨ in the **Home** app bar, `ASSIST` intent-filter + `RECORD_AUDIO`. Last fix: strip emojis/`*smiles*`/markdown before speaking + a plainer system prompt.
- App OTA: `mobile/dist/app-release.apk` + `version.json` (served via ngrok `https://detoxify-culinary-resonant.ngrok-free.dev`).

## Critical gotchas (don't relearn these)
- **MIUI run-as CANNOT create files** in the app data dir (can read + modify existing, e.g. `sed -i` the prefs to switch `offline_active_model_id`). So you **cannot side-load a GGUF over USB** — only the app's own in-app downloader writes there. Add models via the keyboard's Models screen.
- **An IME cannot type into its own popup** → no typed input boxes in keyboard popups; use voice + preset buttons.
- **Models:** Qwen3-4B-Instruct-2507 = reliable+slow (non-thinking); Qwen3-1.7B = fast but THINKING (unreliable for the keyboard); **Qwen2.5-1.5B-Instruct = the sweet spot (non-thinking + fast + reliable)** — I still need to download it from the keyboard's Models screen.
- **Deploy = build + adb install + force-stop + copy to `mobile/dist`.** Keyboard build: `cd private-ai-keyboard && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ANDROID_HOME=~/Library/Android/sdk ./gradlew :app:assembleOfflineDebug`. Bump `versionCode`/`versionName` each time. App build: `flutter build apk --release --target-platform android-arm64` (always `flutter clean` first — incremental reverts the CPU-only/Vulkan-off native-assets config). App is debuggable for run-as; keyboard too.
- The keyboard's review card / writing-tools UI is built **programmatically** in `SuggestionStripView.kt`. The card popup = `WritingToolReviewPopup.kt`. Prompts = `WritingTool.kt` (`SYSTEM_PROMPT`, `ASK_PROMPT`, per-action few-shot). Generation = `ProofreadService.kt` (`proofread()` builds `Instruction:/Input:/Output:` framing + stop sequences).
- On-device test is flaky (screen locks, MIUI shade sticks). `adb exec-out screencap -p` captures the keyboard/app (not the Flutter SurfaceView when black). PIN 159000.

## ROADMAP — what I asked for, in priority order
1. **VERIFY the assistant speech fix** (build v1.21.8+68 just shipped) — it should no longer say "*smiles*" / read emojis aloud, and give better answers.
2. **Download Qwen2.5-1.5B** in the keyboard's Models screen + select it (fast + reliable). (Side-load is blocked by MIUI.)
3. **🌐 Give the on-device AI internet access via the phone** — web search / fetch tools the local LLM can call (so it can answer current/factual questions). Likely a tool-calling loop on top of llamadart + an HTTP client; the phone has connectivity.
4. **🔀 Hybrid local↔cloud delegation logic (THE BIG ONE)** — the on-device AI handles simple tasks locally, but for **complex tasks or when it needs the internet/tools**, it **asks/escalates to the REAL LazyClaw server AI** (ECO router → MiniMax/Claude, which already has web + tools + MCP). Design the decision logic: when does local handle it vs. hand off? The app already talks to the server (Dio + session cookie + WS) — wire the assistant/keyboard to optionally route a turn to the server, with the local model deciding (or a heuristic) whether it needs help. This is the headline feature: a tiered on-device→cloud assistant.
5. **🗂️ Chat history + 🕶️ incognito + 🔔 notifications** — research **how Google (Gboard / Assistant) handles** these (history storage, incognito = no-save mode, assistant notifications) and implement for the Lazy assistant/keyboard: persist conversations, an incognito toggle that doesn't save, and notification handling.
6. **"Hey Lazy" wake-word** (always-listening, the true Hey-Google replacement) — needs a wake-word engine (openWakeWord / Vosk / Porcupine) + a foreground mic service. Big.
7. **Assist-gesture auto-route** — the `ASSIST` intent currently lands on Home (tap ✨); make the assist gesture open `/assistant` directly (go_router `initialLocation` is hardcoded `/home`; honor the platform default route or push past the auth redirect).
8. **Live "watch it write" streaming** in the keyboard review card (pipe the token stream into the card) + **whisper.cpp** for fully-ours private STT (instead of Google's on-device SpeechRecognizer).

Start by confirming #1 works, then let's design **#4 (hybrid delegation)** + **#3 (internet access)** — those two are the most important to me.
