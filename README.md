# ◆ Kestrel

> An AI-powered desktop automation system. Talk to your computer like a person. It talks back - and acts.

**v0.2.1** · [GPL v3 / Commercial](LICENSE) · [Features](CHANGELOG.md) · Windows only

📖 **[kestrel website](https://ibrahim12012.github.io/Kestrel/)** · 🧑‍💻 **[github.com/ibrahim12012/Kestrel](https://github.com/ibrahim12012/Kestrel)**

The site is served from this repository's `docs/` folder. See
[Publishing the website](#publishing-the-website).

Kestrel combines a Gemini-powered chatbot with real OS control. Ask it questions and it answers from its own knowledge. Ask it to do something on your computer and it does it - step by step, watching the screen as it goes.

Bring your own API key. There is no account, no plan, no server of ours in the middle: whichever provider you pick, Kestrel talks to it directly with the key you paste into Settings, stored in your OS credential store.

---

## Windows only, by construction

Kestrel runs on Windows and nowhere else. This is not an oversight or a
porting backlog item, it is what the app is built out of:

- **Reading the screen's structure** goes through Windows UI Automation
  (`uiautomation`), which is how the mapping engine can identify a button
  by name instead of guessing from pixels. macOS Accessibility and Linux
  AT-SPI are different APIs with different models, not a different import.
- **Launching and focusing apps** uses `AppOpener` and `pywin32`, both
  Windows-only, plus `SetCurrentProcessExplicitAppUserModelID` for taskbar
  identity.
- **Speech playback** uses `winsound`, which ships with Python on Windows
  and exists nowhere else.
- **Global hotkeys** are registered through `keyboard`, which needs root on
  Linux and does not work on macOS at all.
- **Revealing a generated file** shells out to Explorer.

Roughly a third of what Kestrel does would need rewriting per platform, not
recompiling. Running it elsewhere is not supported and will fail at import
or at first use.

The parts that are portable are kept that way on purpose, because it makes
them testable: the mapping traversal (`ai/discovery/mapper.py`) is written
against an interface and tested with an in-memory fake, and the whole test
suite runs on Linux. That is a testing property, not a hint that a port is
close.

---

## What It Does

- **Chat** - Ask anything. Kestrel answers from its knowledge first, then offers to act if relevant.
- **Autonomous Mode** - Kestrel proposes it when a task needs multiple steps ("search lo-fi music on YouTube"); you confirm in a dialog, then it watches the screen and executes step by step.
- **Macros** - Every autonomous task gets saved as a reusable one-click function. Build a library of macros over time.
- **No-Code Recorder** - Record clicks and keystrokes manually to create macros without AI, from a dedicated sidebar page or straight from the Macros page. Global hotkeys: F6 = right-click, F7 = click, F8 = toggle typing mode, F9 = Enter, F10 = stop.
- **Voice Assistant** - Click the 🎤 button in the chat input bar, talk, get a spoken reply back - routed through the same chat/tools/autonomous-mode pipeline as typed messages. Shows "🎤 Listening…" while listening and "💭 Kestrel is thinking…" once your audio is captured and Gemini is working on a reply, so it's always clear which phase you're in. Click the mic again while Kestrel is talking to interrupt it. (`ai/main.py` also exposes a continuous, terminal-only push-to-talk loop via `run_voice_assistant()`, for hands-free use outside the app window.)
- **Seven languages** - English, plus Arabic, Chinese, French, Hindi, Portuguese and Spanish. Pick one in Settings, or leave it to follow your OS. Right-to-left layout is handled for Arabic. Anything not yet translated falls back to English rather than going blank; `tools/extract_strings.py` regenerates the catalogs in `app/locale/`.
- **Live Console** - Every `print()` from every tool shows up in real time in the UI.

---

## Project Structure

```
Kestrel/
│
├── main.py                             # Entry point - run this
├── paths.py                            # Single source of truth for BASE_PATH (source vs. PyInstaller-frozen)
├── Kestrel.spec                           # PyInstaller build spec (see "Building a Windows Executable")
├── .env                                # GEMINI_API_KEY=your_key
│
├── ai/
│   ├── main.py                         # chat + autonomous mode + recorder + voice assistant
│   ├── actions.py                      # All tool_* functions live here
│   ├── macro_generalizer.py            # Turns literal recorded macros into parameterized ones
│   ├── compaction.py                   # Summarises a long conversation to stay within the model's context
│   ├── web_reader.py                   # Reading and crawling web pages (robots.txt, no CAPTCHA bypass)
│   ├── web_render.py                   # Off-screen Edge/Chrome for pages built with JavaScript
│   ├── web_search.py                   # tool_web_search
│   ├── sounds.py                       # Sound effects (assets/sounds)
│   ├── run_screenshots.py              # Keeps each autonomous step's screenshot
│   ├── discovery/screen_map.py         # Names and coordinates of the controls on screen
│   ├── discovery/                      # Mapping an app's screens and controls (see "Mapping an App")
│   ├── automation_lock.py              # One automation at a time: AI run, macro or mapping run
│   ├── instructions.txt                # Main chatbot system prompt
│   ├── autonomous_instructions.txt     # AUTONOMOUS MODE instructions
│   └── generalizer_instructions.txt    # Macro generalizer system prompt
│
├── app/                                # UI engine
│   ├── main_window.py
│   ├── chat_widget.py
│   ├── sidebar_widget.py
│   ├── macros_widget.py
│   ├── recorder_widget.py              # No-Code Recorder page
│   ├── overlay_widget.py
│   ├── chat_database.py
│   ├── chat_context.py                 # What the model is given of a saved chat
│   ├── image_viewer.py                 # Full-size screenshot viewer
│   ├── icons.py                        # SVG icons tinted to the theme (assets/icons)
│   ├── single_instance.py              # One running Kestrel at a time
│   ├── dpi_guard.py                    # Keeps the window drawn at the scale Windows has it at
│   ├── ui/                             # UI files (made with QT Designer)
│   │   ├── main_window.ui
│   │   ├── chat_widget.ui
│   │   ├── macros_widget.ui
│   │   └── sidebar_widget.ui
│   └── workers/                        # Background QThread workers
│      ├── ai_worker.py                 # typed chat messages
│      ├── voice_worker.py              # mic button
│      ├── autonomous_worker.py         # confirmed autonomous-mode runs
│      ├── recorder_worker.py           # global hotkeys for the recorder
│      └── generalizer_worker.py        # manual "Generalize" button (Recorder page)
│
├── installer/                          # Native setup program (see installer/README.md)
│
├── tools/make_sounds.py                # Generates assets/sounds
│
├── tests/                              # pytest suite (see Testing section)
│
├── data/                    
│   ├── chats.db
│
└── assets/                             # logo (icon/), icons/ (SVG), sounds/ (WAV)
```

---

## Installation

### 1. Prerequisites

- Python 3.10 or higher
- A Google Gemini API key → [Get one here](https://aistudio.google.com/app/apikey)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Or with a virtual environment (recommended):

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
```

> The voice assistant (`SpeechRecognition` + `PyAudio`) is optional - everything else (chat, autonomous mode, recorder) works without it. `PyAudio` can be tricky to install on some platforms; skip it if you don't need voice mode.

### 3. Create your `.env` file

Copy `.env.example` to `.env` and fill in your real key:

```bash
cp .env.example .env
```

```
GEMINI_API_KEY=your_api_key_here
```

### 4. Run

```bash
python main.py
```

The window opens. The console panel will show:
```
[Kestrel] System initializing...
[Kestrel] Loading tool registry...
[Kestrel] Ready. 6 tools loaded.
```

---

## How the AI Decides What to Do

Kestrel follows a strict decision tree on every message:

```
User message received
        │
        ▼
Can I answer this from my own knowledge?
        │
       YES → Answer directly, then offer to act if relevant
        │
        NO ↓
        │
Is the answer on the web?
        │
       YES → Search, read or crawl it myself (no browser, no macro)
        │
        NO ↓
        │
Is this a simple 1-step action or an existing macro?
        │
       YES → Execute directly (tool_open_application, play macro, etc.)
        │
        NO ↓
        │
Does this require multiple steps / screen navigation?
        │
       YES → Launch Autonomous Mode automatically
```

**Examples:**
| What you say | What it does |
|---|---|
| "What is Reddit?" | Answers from knowledge, offers to open it |
| "What's the weather in Paris?" | Calls tool_web_search directly |
| "Summarise this article: <link>" | Calls tool_read_url directly |
| "What plans does acme.com offer?" | Calls tool_crawl_site directly |
| "Open Spotify" | Calls tool_open_application("spotify") directly |
| "Play my chill playlist macro" | Runs the saved macro directly |
| "Search for lo-fi on YouTube" | Launches autonomous mode |
| "Send an email to John saying hello" | Launches autonomous mode |
| "What Python version do I have" / "install requests with pip" | Calls tool_run_command directly |

---

## The Macro System

Every task the AI completes in autonomous mode gets saved as a reusable function in `ai/actions.py`. This is the core power of Kestrel - over time you build a personal library of macros.

Macros are for doing things on screen. Looking something up is not one of them: the AI searches (`tool_web_search`), reads pages (`tool_read_url`) and crawls sites (`tool_crawl_site`) itself, directly, the way ChatGPT or Claude read the web. Nothing opens on screen, nothing is recorded, and a run that only read the web saves no macro. Crawls follow `robots.txt`, requests say they come from `Kestrel-User`, pages built with JavaScript are read in an off-screen browser (Edge or Chrome, headless), and a CAPTCHA or bot check stops the read: nothing tries to get past one.

**How macros are saved:**

When autonomous mode finishes a task, it calls `tool_name_action("macro_name")`. This automatically writes a new function to `ai/actions.py`:

```python
def tool_open_youtube_lofi():
    print('Executing function: open_youtube_lofi')
    play_actions([("firefox", "open"), ("youtube.com", "write"), ...])
```

Next time you say "play lo-fi on YouTube", the AI finds this macro and runs it instantly - no autonomous mode needed.

**Generalizing macros:**

A literal macro like the one above is tied to exactly what was recorded - a macro born from "search Soprano on YouTube" only ever searches "Soprano". `ai/macro_generalizer.py` turns it into a reusable, parameterized one instead: `tool_search_youtube_and_play_first(query)`, with clicks pointed at *described* UI elements (e.g. "YouTube search bar") via the vision tool `click_at_target_or_position`, which tries `get_position_object_and_click` first and falls back to the originally recorded `(x, y)` if vision can't find it - so it stays exact-and-working even without the AI, but adapts if the layout shifted a little.

- **Autonomous mode**: this runs automatically right after a macro is saved. If the AI isn't confident the task actually generalizes (e.g. "open Notepad and write my grocery list" - nothing to generalize), the literal macro is kept exactly as-is; generalizing never breaks a working macro.
- **Recorder**: click **✨ Generalize this macro** after saving. You'll be asked for a one-line description of what the macro does (helps the AI pick good parameters), then the same process runs.

This runs as its own dedicated Gemini chat session with its own system prompt (`ai/generalizer_instructions.txt`) - the same pattern as the main chat (`ai/instructions.txt`) and autonomous mode (`ai/autonomous_instructions.txt`). It's a separate "sub-agent" call, not a step inside the main conversation, and the AI is never allowed to write raw code straight into `ai/actions.py` - it returns strict, validated JSON describing the parameters and step sequence, and the actual Python source is generated (and syntax-checked) on our side. See `ai/macro_generalizer.py` for the full validation rules.

Generalized steps can be a left click, a right click (context menu), typed text, Enter, a new browser tab, or opening/focusing an application. Whenever a macro's task involves a specific app, it's required to open/focus that app as its very first step - even if the original recording started with it already open - since re-opening an already-running app just brings it to the front rather than erroring, and that's what makes the macro reliable the next time it runs.

**Viewing your macros:**

Click the **Tools** tab in the sidebar. All `tool_*` functions are listed with their parameters and a Run button.

---

## ✦ FIRST-TIME SETUP GUIDE

> Follow these steps when you first launch Kestrel. The goal is to teach the AI your setup and build a set of foundational macros it will use for everything going forward.

---

### Step 1 - Tell the AI your preferred browser

Start by telling the AI which browser you use. This single piece of information affects every web navigation task.

**Type in the chat:**
```
My preferred browser is Firefox (or Chrome / Edge - whatever you use)
```

The AI will save this preference. From now on, every autonomous web task will open your browser automatically.

---

### Step 2 - Create your first web navigation macros

These are the most important macros to have. The AI will use them as building blocks for almost every web task.

**Type each of these one at a time. After each one, wait for autonomous mode to finish.**

#### Open YouTube
```
Go to YouTube
```
The AI will open your browser, navigate to youtube.com, and save a macro called `tool_go_to_youtube` (or similar). Next time you say "go to YouTube" it runs instantly.

#### Open Google
```
Go to Google
```
Saves a macro for opening google.com on screen. You don't need it to look things up: just ask, and the AI searches and reads the web itself.

#### Open your music app
```
Go to Deezer
```
or
```
Go to Spotify
```
Pick whichever you use. This creates a macro for your music service homepage.

#### Open Gmail (if you use it)
```
Go to Gmail
```

#### Your most-visited site
```
Go to [your most visited website]
```

**After this step you should have 4-5 navigation macros saved in your Tools tab.**

---

### Step 3 - Build search macros

Now teach the AI how to search on the platforms you use on screen, to play or open what it finds. These become reusable templates. (Searching the web for information needs no macro: the AI does that itself.)

#### YouTube search macro
```
Search for "test" on YouTube
```

#### Deezer / Spotify search macro
```
Search for "test" on Deezer
```

**After this step the AI knows how to search on every platform you use on screen - without constructing URLs.**

---

### Step 4 - Create your daily workflow macros

Think about what you do every day on your computer and create macros for each habit.

**Examples to try:**

#### Morning routine
```
Open my morning apps: Firefox, Spotify, and Discord
```

#### Music habits
```
Play lofi hip hop on YouTube
```
```
Play my liked songs on Spotify
```
```
Play jazz playlist on Deezer
```

#### Work setup
```
Open VS Code and Firefox side by side
```

#### Communication
```
Open WhatsApp Web
```
```
Open my Gmail inbox
```

**Keep going until you have a macro for every repeated task you do.**

---

### Step 5 - Test your macro library

Once you have 8-10 macros saved, test that the AI uses them correctly:

**Say each of these and verify the AI runs the macro directly (no autonomous mode):**
```
Go to YouTube
```
```
Open Spotify
```
```
Play my lofi music
```

You should see in the Console panel:
```
[tool] Executing function: go_to_youtube
```
...instead of the step-by-step autonomous mode log. That means the macro is being used.

---

### Step 6 - Refine with the No-Code Recorder

For tasks that are too precise or complex for the AI to get right autonomously (specific UI elements, multi-step forms, unusual apps), use the **No-Code Recorder**.

1. Click **Recorder** in the sidebar (or **⏺ Record** at the top of the Macros page - both open the same page)
2. Click **⏺ Start recording**
3. Perform the task yourself, anywhere on screen - these are global hotkeys, they work even while Kestrel isn't focused:
   - **F6** = record a right-click at current mouse position
   - **F7** = record a click at current mouse position
   - **F8** = toggle typing mode (press F8 or Esc again to stop typing and commit the text)
   - **F9** = record an Enter key press
   - **F10** = stop recording
4. Type a name and click **Save macro**
5. It's saved to `ai/actions.py` automatically, and the AI's tool list refreshes immediately - no restart needed

The AI can now call this macro just like any other tool.

---

### Step 7 - Tell the AI your preferences

Have a short conversation to set up your profile. These get saved in `ai/instructions.txt` automatically.

**Suggested messages to send:**

```
I prefer dark mode on all websites when possible
```
```
For news, prefer French sources
```
```
My default music app is Deezer / Spotify
```
```
I work in Python. When I ask about code, assume Python unless I say otherwise
```
```
I speak French / English (your language preference)
```

Each response will append the new preference to the bottom of `ai/instructions.txt` - existing ones are never removed or rewritten, only added to. If two preferences ever genuinely conflict (e.g. you first say you prefer Chrome, then later say you prefer Edge), the AI is told to follow whichever one is listed *last*, so the most recent thing you said always wins without erasing the history.

---

### Recommended Macro Library After Setup

After completing the steps above, you should have macros covering:

| Category | Macros to have |
|---|---|
| Navigation | go_to_youtube, go_to_gmail, go_to_deezer |
| Search | search_youtube, search_deezer |
| Music | play_lofi_youtube, play_liked_songs, play_jazz_deezer |
| Daily apps | open_discord, open_vscode, open_whatsapp_web |
| Work | morning_routine, open_work_setup |

With these in place, most of your daily tasks become instant one-step calls instead of full autonomous sessions. Web lookups never needed a macro: those are answered directly.

---

## How Autonomous Mode Works

The AI has no direct way to start autonomous mode itself - there's no tool call for it. When it decides (per its instructions) that a task needs multiple steps:

1. It replies normally, explaining what it's about to do and asking for confirmation
2. It appends a `[[AUTONOMOUS_SUGGEST: <goal>]]` tag on the last line of its reply
3. The app catches that tag and shows a confirmation dialog with the goal
4. Only if you click **Lancer** does autonomous mode actually start:
   - Takes a screenshot of your screen
   - Looks at the current state
   - Calls exactly ONE tool
   - Takes a new screenshot
   - Repeats until the goal is complete
   - Saves everything as a macro via `tool_name_action` (a run that did nothing on screen saves none)

**Your mouse stays yours.** The AI's clicks don't move your pointer (`ai/direct_input.py`). A click goes to the control through Windows UI Automation (a button is pressed, a checkbox ticked, a tab or list item selected, a menu opened), or as a window message to a classic Windows control. Typing goes into the window the AI clicked even when that window isn't in front, if the field is a standard Windows text box; anything else, and key combinations such as Ctrl+T, bring that window to the front first, once you pause typing. If you click into another window while it types, it stops there and doesn't press Enter. Only an app that reacts to nothing but a real click gets one: Kestrel moves the mouse, clicks, and puts it back where it was. **Settings > Mouse** turns that off, and those clicks are then skipped with the reason given to the AI. Moving your mouse into a corner of the main screen still stops a run.

**Rules the AI follows:**
- Never uses the Windows search bar
- Never constructs direct URLs (`?q=`, `?search=`, etc.)
- Always navigates step by step: browser → domain → site's search bar → query
- Calls one tool per turn, always checks the screen after
- Never emits the suggestion tag without asking in plain language first

---

## Mapping an App

Mapping reads an open app's screens and controls through Windows UI
Automation and saves them as a map, so the AI can press a control by
its name (`tool_use_mapped_control`) instead of reading a screenshot for
every click. Start it from the Mapping page, or ask in the chat.

- The window is found by its program (notepad.exe for "notepad"), then
  its title. Kestrel's own window, the taskbar, the desktop and windows
  on another virtual desktop are never mapped.
- The run clicks through every screen it can reach. Buttons that delete,
  send, pay, post, reply, call or log out are recorded and never
  pressed, and only empty one-line fields get its test value.
- The app's window is maximized for the run and put back as it was
  afterwards.
- A finished map is reused; one from a run that was stopped or hit a
  limit is mapped again, and never replaces a finished one. The Mapping
  page shows saved maps again and deletes them.
- When the AI presses a mapped control, the control is looked up in the
  window as it is now. If it isn't on screen, nothing is clicked.
- One automation at a time: while an AI run, a macro or a mapping run
  is going, the others are refused.

---

## Adding Your Own Tools

Any function in `ai/actions.py` that starts with `tool_` is automatically detected and shown in the Tools panel.

```python
# ai/actions.py

def tool_volume_up():
    """Increase system volume by 10%."""
    from ctypes import cast, POINTER
    # your implementation
    print("[tool] Volume increased")

def tool_open_project(project_name: str):
    """Open a project folder in VS Code."""
    import subprocess
    subprocess.Popen(["code", f"C:/Projects/{project_name}"])
    print(f"[tool] Opening project: {project_name}")
```

Click **Refresh** in the Tools tab to reload. The new tools appear instantly with auto-generated input fields.

In the installed app, console programs a tool starts (cmd.exe, PowerShell, a script) run without a console window, so nothing flashes on screen. For one you want to see or type into, pass `creationflags=subprocess.CREATE_NEW_CONSOLE` to `subprocess.Popen`.

---

## How Print Capture Works

Every `print()` call anywhere in your code shows up in the Console panel in real time - including inside tools, inside autonomous mode, inside threading.

```
print("hello")          →   captured by StreamRedirector
                        →   Qt signal emitted (thread-safe)
                        →   LogRouter adds timestamp
                        →   ConsolePanel displays it with color coding
```

Color coding:
- **White** - normal output
- **Red** - errors (stderr)
- **Cyan** - tool calls (lines containing "tool_" or "[tool]")
- **Green** - system messages (lines containing "[Kestrel]" or "[system]")

---

## Troubleshooting

**Window doesn't open:**
Run the step-by-step import test:
```bash
python -c "from app.main_window import MainWindow; print('OK')"
```

**Kestrel.exe closes on its own, or never shows a window:**
Start it once more, then look in `%LOCALAPPDATA%\Kestrel\logs`.
`crash.log` says how the previous run ended: how far it got, its last
log lines, and, if Windows recorded a crash, the DLL it happened in.
`kestrel.previous.log` is that run's full log. Those two files are what
to attach to a bug report.

**AI not responding:**
Check your `.env` file has `GEMINI_API_KEY=your_key` with no spaces or quotes around the key.

**Tools not showing in Tools tab:**
Make sure your functions start exactly with `tool_` (lowercase) and are in the module passed to `load_from_module()`. Click Refresh.

**Autonomous mode not saving macros:**
Check that `tool_name_action` is in your tools list and that `ai/actions.py` is writable.

**The window is drawn twice, or at the wrong size, after Alt+Tab:**
Kestrel redraws it at the right scale when you come back to it, and
`kestrel.log` gets a `[window]` line saying what it found. The
`[startup] Environment` line should say "DPI awareness per-monitor v2";
if it says something else, attach the log to a bug report.

**Clicks landing in the wrong place:**
If you have multiple monitors, the AI looks at and clicks on the main one. Set the display you want it to use as the main display in Windows display settings.

---

## Key Concepts

| Term | Meaning |
|---|---|
| **Macro** | A saved sequence of actions recorded as a Python function |
| **Macro Generalization** | Turns a literal macro into a reusable, parameterized one (`ai/macro_generalizer.py`) - automatic after autonomous mode, manual button on the Recorder page |
| **Autonomous Mode** | AI proposes it via `[[AUTONOMOUS_SUGGEST: goal]]`, user confirms, then it takes screenshots and acts step by step to complete a goal |
| **tool_function** | Any function starting with `tool_` - auto-detected as a tool |
| **App focus, not relaunch** | `tool_open_application` checks if the app is already running (via `psutil` + `pywin32`) and brings it to the foreground **maximized to fullscreen** instead of launching a duplicate; only calls AppOpener's `open()` when nothing is found, then waits for the new window and maximizes it too. For browsers specifically, an already-running one gets a **fresh Ctrl+T tab** instead of reusing the current one, so automation never touches the user's open tabs. |
| **No-Code Recorder** | Manual recording mode using F6/F7/F8/F9/F10 hotkeys, reachable from the sidebar or the Macros page |
| **Voice Assistant** | 🎤 button in the chat input bar: one-shot mic recording routed through the main chat/tools pipeline, spoken reply via Gemini TTS. `run_voice_assistant()` offers a continuous, terminal-only F8 push-to-talk loop with its own system prompt but the same tools (`ai/actions.py`). |
| **StreamRedirector** | System that captures all print() and shows it in the UI |
| **LogRouter** | Adds timestamps and routes captured output to the Console panel |

---

## Requirements

Windows 10 or 11, and Python 3.11+. See "Windows only, by construction"
above for why there is no other option.

```
PyQt6>=6.4.0
google-genai>=1.0.0
python-dotenv>=1.0.0
pyautogui>=0.9.54
keyboard>=0.13.5
pyperclip>=1.8.0
Pillow>=10.0.0
AppOpener>=1.7

# Optional - voice assistant (see ai/main.py)
SpeechRecognition>=3.10.0
PyAudio>=0.2.13
```

---

## Testing

`tests/` covers the logic that doesn't need a display, a mouse, or Windows:
`ai/actions.py` (file utilities, tool auto-discovery, macro playback dispatch,
macro-name sanitization, the vision/fallback click helper), `ai/macro_generalizer.py`
(JSON validation and code generation - including tests that AI-authored text can't
break out of the generated Python, e.g. quote/code injection attempts), `app/chat_database.py`
(schema, chat/message CRUD, connection handling), `paths.py`, and `ai/main.py`'s
`AUTONOMOUS_SUGGEST` protocol parser. GUI smoke tests (`test_smoke.py`) construct
the real Qt widgets and `MainWindow` using PyQt6's offscreen platform plugin.
`pyautogui`/`keyboard`/`AppOpener`/`google.genai` are stubbed out in
`tests/conftest.py`, so the suite runs anywhere, including CI.

Not covered (needs real hardware/OS hooks, not meaningfully unit-testable):
`app/workers/recorder_worker.py`'s global keyboard hooks, and anything that
makes a real Gemini API call (`ai/macro_generalizer.py`'s `_call_generalizer`,
the vision call in `get_position_object_and_click`, etc.).

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

There's no CI running the test suite automatically yet - `.github/workflows/tests.yml` doesn't actually exist in this repo despite an earlier version of this doc claiming it did. Worth setting up before distributing an installer widely, so a regression on `main` doesn't ship silently.

---

## Building a Windows Executable

`Kestrel.spec` builds a standalone `dist/Kestrel/Kestrel.exe` with PyInstaller.

**Must be run on Windows** - PyInstaller doesn't cross-compile, and Kestrel itself is Windows-only anyway (`AppOpener`, `taskkill`/`tasklist`, `winsound`).

```bash
pip install -r requirements.txt pyinstaller
pyinstaller Kestrel.spec
```

This produces a `dist/Kestrel/` folder (not a single `.exe` - `--onedir` starts much faster than `--onefile`, which re-extracts everything to a temp folder on every launch). To ship it:

1. The app now reads/writes `.env` under `%LOCALAPPDATA%\Kestrel\.env` (via `paths.USER_DATA_PATH`) rather than next to `Kestrel.exe` - `dist/Kestrel/` (and `Program Files`, if you install there) is commonly not writable by a standard non-admin user once actually installed, which is exactly why this changed. Simplest first run: just launch `Kestrel.exe` and paste the key into Settings - it'll create the file in the right place itself. If you'd rather pre-seed it (e.g. for a scripted install), write it to `%LOCALAPPDATA%\Kestrel\.env` directly.
2. Zip the whole `dist/Kestrel/` folder, or build the real installer described below.

### Building the installer

`installer/build_installer.py` turns `dist/Kestrel/` into one setup program:

```bash
pyinstaller Kestrel.spec
python installer/build_installer.py
```

Output lands in `dist/KestrelSetup-<version>.exe`, with the version taken from `paths.APP_VERSION`. The setup is a small native program written in C (`installer/setup/`): per-user install with no administrator rights, Start menu and optional desktop and sign-in shortcuts, an entry in Settings > Apps, updates that cannot leave a half-installed app, an uninstaller that removes everything and asks before deleting your chats and settings, and a command line for unattended installs (`/S`, `/D=<folder>`, ...). **[`installer/README.md`](installer/README.md)** has the details, the options and how to rebuild the stub.

The old Inno Setup script, `installer/Kestrel.iss`, still works (`iscc installer\Kestrel.iss`) if you need it.

### Icons and sounds

The app's icons are SVG files in `assets/icons/`, drawn for Kestrel on a 24 px grid and tinted to the theme at run time (`app/icons.py`); add one by dropping in a file and asking for it by name. The sound effects are WAV files in `assets/sounds/`, generated by `tools/make_sounds.py` (needs numpy); a WAV with the same name replaces one.

### Antivirus warnings

Expect them, and read **[`docs/ANTIVIRUS.md`](docs/ANTIVIRUS.md)** before shipping a build.

Kestrel installs global keyboard and mouse hooks, injects synthetic input, captures the screen, runs shell commands and sends screenshots to a remote API. That is the product, and it is also an exact description of a remote access trojan; no scanner can tell the two apart from the outside. On top of that, an unsigned executable gets SmartScreen's "Unknown publisher" prompt regardless of what it does.

The build side is already handled: no UPX packing, a real version resource, an explicit `asInvoker` manifest, nothing written into the install directory at runtime, and no screen-capture evasion. What remains needs a code-signing certificate (only an EV one clears SmartScreen immediately) plus a false-positive submission to Microsoft per release. `docs/ANTIVIRUS.md` has the full split between what was fixed, what is inherent, and what actually ends the detections - including a release checklist and suggested wording for telling users about the warning honestly.

`paths.py` is what makes this work: every module resolves file paths (the `.ui` files, `assets/`, `ai/instructions.txt`) through a single `BASE_PATH`, which points at `sys._MEIPASS` when frozen instead of relying on `__file__` - frozen Python modules don't reliably report a real file path, which used to make relative-path lookups unpredictable once packaged. `Kestrel.spec`'s `datas` list mirrors the source tree exactly so those lookups keep resolving to the right place.

`Kestrel.spec` uses `assets/icon/Kestrel.ico` - a multi-resolution `.ico` generated from `Purple-transparent-1000.png` - for the `.exe`'s icon (a `.ico` gives a much crisper taskbar/exe icon on Windows than a plain `.png`).

---

## Publishing the website

The project website lives in this repository, under `docs/`. Hand-written HTML with one shared
stylesheet: no build step, no dependencies, no generator. Open `docs/index.html` in a browser
straight from a clone and it renders exactly as it will when hosted.

| File | What it is |
|---|---|
| `index.html` | Landing page: what the app is, the feature grid, how it decides what to do, safety, FAQ |
| `docs.html` | The documentation, one page with a sticky section rail |
| `licensing.html` · `privacy.html` · `terms.html` · `contact.html` | Supporting pages |
| `styles.css` | Every rule the site uses. Its tokens are `app/theme.py`'s dark palette, so the site and the app are the same colours by construction |
| `script.js` | One thing only: highlighting the active section in the docs rail on scroll |

To serve it free from GitHub, in the repository's **Settings → Pages**:

| Field | Value |
|---|---|
| Source | Deploy from a branch |
| Branch | `main` |
| Folder | `/docs` |

Save, and it publishes at `https://<user>.github.io/<repo>/` within a minute or two. That
"branch + `/docs`" pairing is the standard way to host a site out of a repository that is
primarily something else - no `gh-pages` branch to maintain, and no second place to remember
to update. The empty **`docs/.nojekyll`** next to it tells Pages to serve the folder as plain
static files rather than running it through Jekyll, which would otherwise ignore any file or
folder beginning with an underscore.

The developer notes already in `docs/` (`ANTIVIRUS.md` and friends) are unaffected: `index.html`
is what the folder's root URL serves, and the Markdown files stay reachable at their own paths.

The site has no analytics, no tracking and no third-party JavaScript. The only external request
it makes is to Google Fonts for its two typefaces, and it falls back to system fonts if that is
blocked.

---

## ⚖️ Licensing & commercial use

Kestrel is **dual-licensed**.

**1. Open source use (GPL v3).** Free if you are an individual, an
open-source project, or willing to release your derivative work under
the GPL v3 as well. This is genuine open source: GPL v3 is OSI-approved,
so describing Kestrel as open source is accurate.

**2. Commercial use.** If you want Kestrel in a proprietary or
closed-source product, or in enterprise infrastructure, without the GPL
v3 obligations, you need a commercial licence.

To purchase one or discuss enterprise terms: **leafeon.void@gmail.com**

The distinction that catches people out: the GPL binds you when you
*convey* the software to someone else, not when you merely run it.
Using it inside your company, unmodified and undistributed, does not by
itself oblige you to publish anything. Shipping a product containing it
does. Anything you *produce* with Kestrel is yours under either licence.

See [LICENSE](LICENSE) for the terms and
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) for who needs which,
including **a note on PyQt6** whose own GPL/commercial dual licence
applies on top of this one. [EULA.md](EULA.md) covers what running it
means for your computer.

Not reviewed by a lawyer. Have one read it before relying on it
commercially.

---
