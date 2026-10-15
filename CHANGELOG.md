# Features

Everything Kestrel can do, by area. The first section, Latest changes,
is what changed most recently, fixes included. Everything after it is a
list of capabilities, not a release history: no dates, no version
sections and no bug fixes. For older changes, read the commit log.

---

## Latest changes

### Your mouse

- **The AI no longer takes over your mouse.** Its clicks, from the
  chat, autonomous runs, macros and mapping, go to the control itself
  through Windows UI Automation: a button is pressed, a checkbox ticked,
  a tab, radio button or list item selected, a menu or a list opened, a
  text field focused. A classic Windows control with nothing like that
  gets the click as a window message. The pointer stays where you left
  it, and moving it while the AI works no longer sends the next click
  somewhere else. `ai/direct_input.py`.
- **Typing goes to the window the AI clicked**, the app it opened, or
  a window its own keys brought up (Win+R's Run box, a new window from
  Ctrl+N), not to whatever you are using. A dialog that window opens
  counts as that window. Into a standard Windows text box the text goes
  straight in, even with the window in the background. Elsewhere, and
  for key combinations such as Ctrl+T, the window is brought to the
  front first, once you pause typing or moving the mouse, and never
  from another virtual desktop; if it can't be, nothing is typed. When
  that window closes, keys go to the window it belonged to, or to the
  app its last key started, or wait for the AI's next click, never into
  whatever happens to be in front. A new window that may be yours (it
  came up while you were using the computer) isn't typed into either.
  With no window clicked yet, nothing goes into Kestrel's own chat box:
  the AI is told to click the field first.
- **No Delete outside a text field.** Typing ends with a Delete that
  clears an address bar's suggestion. It is now pressed only when the
  focus is in a text field: in a file list it would have sent the
  selected file to the Recycle Bin.
- **Typing stops when you take over.** Before every key, the window it
  started in has to still be the one in front. Click into another window
  halfway through, or let a dialog pop up, and typing stops there: Enter
  isn't pressed after half a command, the AI is told what happened, and
  a macro stops at that step.
- **When nothing else reaches a control** (a web page element with no
  role, a custom-drawn control, a game), Kestrel moves the mouse, clicks
  and puts it back where it was. Not while you hold a mouse button or
  Ctrl, Shift or Alt, which would drop what you are dragging or make it
  a Ctrl+click, and not until you pause typing or moving the mouse.
  **Settings > Mouse** turns this off: those clicks are then skipped, a
  macro stops there, and the AI is told why.
- **One press, never two.** A button that opens a dialog can keep its
  app from answering until the dialog closes; the run goes on, and the
  press isn't repeated another way. Neither is one the app reported an
  error for, since it may have happened anyway.
- **The corner fail-safe still works**: the pointer in a corner of the
  main screen stops automation before the next action, mapping runs
  included, which it never reached before. Now that you can use the
  mouse during a run, don't leave it resting in a corner.
- **Accented text is typed, not pasted.** Typing was meant to send each
  character by code point, but the input structure was 8 bytes short of
  what 64-bit Windows expects, Windows refused every call, and every
  accented character went through your clipboard instead.
- **Enter is let go.** The type-and-Enter and Enter tools pressed Enter
  and never released it.
- **A mapped control's fallback click on two screens** landed at half
  its position: the conversion compared the whole desktop with the main
  screen.
- **Kestrel's own border no longer blocks clicks.** Windows reports the
  purple working border, and Kestrel's other see-through panels, at
  every point they cover, so every click was taken as aimed at Kestrel
  and refused. Clicks now go through them to the window underneath. A
  click the AI aims from a screenshot that shows Kestrel's own window at
  that spot is refused instead: the app under it can't be seen there.
- **Keys and shortcuts.** `tool_press_keys` presses one key or a
  combination (Escape, Tab, Ctrl+A, Win+R, Alt+Tab) in the window the AI
  works in. Combinations that close a window or delete for good (Alt+F4,
  Ctrl+W, Shift+Delete) ask you first, however they are written. Steps
  you spell out ("press Win+R,
  type notepad, press Enter") are taken as given instead of being
  replaced by a shortcut.
- **An autonomous run no longer ends after its first step** when the
  chat had just opened an app: a flag the chat left set read as "done".

### Installer

- **A native installer.** Setup is now a small Windows program written
  in C (`installer/setup`, built by `installer/build_installer.py`).
  It installs per user with no administrator rights, shows the licence,
  the options and the progress in the app's own look, and handles high
  DPI and the keyboard. The Inno Setup script is kept as a fallback.
- **Safe updates.** An update is unpacked next to the installed version
  and swapped in, so a failed or canceled update leaves the old one
  exactly as it was. Running the same version again offers a reinstall.
  A running Kestrel is asked to close first, and an install made by the
  old Inno Setup installer is replaced cleanly.
- **Uninstall removes everything**, the uninstaller included, and asks
  before deleting chats and settings.
- **Unattended installs**: `/S`, `/D=<folder>`, `--desktop-shortcut=0|1`,
  `--startup=0|1`, `--launch` and `--force-close`, and `/S --delete-data`
  for the uninstaller.
- **One setup window at a time.** Starting setup again brings the open
  one to the front.

### Startup

- **Kestrel.exe closing as its window appeared.** The installed app
  died inside the call that first shows the window, with an access
  violation and no message. The three things that could do that are
  gone. Nothing hooks the window's messages any more: a second launch
  and the installer reach the running app through named Windows objects
  a background thread waits on. Kestrel.exe loads the newest Microsoft
  C++ runtime on the machine before Qt, so Qt and any DLL injected into
  the window never run on an older copy than they were built for. And
  its manifest is back to the standard one, with DPI awareness left to
  Qt and no process-wide UTF-8 code page. The installer no longer waits
  9 seconds for an app that isn't listening before offering to end it.
- **Faster first window.** The splash screen no longer stays up for a
  fixed 2.6 seconds. AppOpener, pyautogui, the Gemini SDK and the audio
  stack load after the window is up, in the background, instead of
  before it. The Console logs how long startup took.
- **No console windows, at launch or after.** Kestrel.exe has no console
  of its own, so every console program it starts (cmd.exe, PowerShell,
  `ver`) gets a window, and these flashed on screen at launch and again
  once the app was up: AppOpener runs cmd.exe on import and a string of
  commands when its app list query fails, and Python runs `ver` the
  first time anything asks which Windows this is. Now nothing Kestrel
  starts opens a console window, whichever library starts it, unless it
  asks for one: opening Command Prompt or PowerShell from the chat still
  shows it. A tool you add can ask for one too (README, Adding Your Own
  Tools).
- **One Kestrel at a time.** Launching it again brings up the running
  one instead of starting a second copy, which doubled every hotkey and
  sound. The running one confirms it has shown its window. If it is
  still closing (or has just crashed), the new launch waits for it to be
  gone and starts normally; if it never answers, the new launch says so
  instead of closing without a word.
- **Failures leave a trace.** `%LOCALAPPDATA%\Kestrel\logs` holds
  `crash.log` (uncaught errors, Qt aborts, native crashes) and
  `kestrel.log` (everything the current run logged; the run before is
  kept as `kestrel.previous.log`). An error at startup is shown with the
  file's path, and an error inside one button or menu is logged instead
  of closing the whole app, which is what PyQt does by default. When a
  run ends without closing normally, the next launch writes down how far
  it got, its last log lines, and what Windows recorded about the crash,
  including the DLL it happened in.

### Chat

- **Long conversations are compacted**, the way Claude does it. Near the
  model's context budget, the older part is summarised by the model and
  the conversation carries on from the summary plus the recent
  messages. `/compact` does it on demand, and a request the provider
  rejects as too long is compacted and retried once. The summary is
  saved with the chat, marked in the transcript and readable from View
  summary.
- **Each chat is its own conversation for the model.** Switching chats
  used to keep talking to the same history, and a chat reopened after a
  restart had no context. A macro saved or deleted mid-chat no longer
  wipes it either.
- **Screenshots you can see.** Larger previews with a caption saying who
  took them. A click opens the screenshot inside the Kestrel window, over
  the page and sized to show all of it, instead of in a window of its
  own. Zoom, fit, copy, open in your image viewer, show in folder, the
  arrow keys between images, and Esc or the X to go back.
- **An autonomous run's screens are kept.** Each step's screenshot is
  saved in a folder for the run and shown as a strip in the chat when it
  ends. Runs beyond the newest 40, or older than 30 days, are deleted.
- **Images you send stay with the message**, shown above it, also after
  a reload.
- **Sources.** The pages an answer drew on are listed in a card above
  it.

### Web

- **The assistant reads the web itself**, like ChatGPT or Claude:
  searching, reading a page and crawling a site all happen directly,
  without the browser, autonomous mode or a macro. The prompt now sends
  lookups there and keeps autonomous mode for doing things on screen. A
  run that did nothing on screen no longer saves an empty macro.
- **Pages built with JavaScript** are read in an off-screen browser
  (Edge or Chrome, headless, with a throwaway profile) instead of coming
  back empty.
- **It says who it is.** Requests identify as `Kestrel-User`, the way
  ChatGPT-User and Claude-User do, instead of as Chrome.
- **Crawls follow the site's rules**: its `robots.txt` rules for
  Kestrel-User and its Crawl-delay. A `robots.txt` that answers with a
  server error means "not now".
- **No CAPTCHA bypass.** A CAPTCHA, a Cloudflare or other bot check, or
  HTTP 429 stops the read and the rest of a crawl, and the answer says
  so. Nothing tries to solve, dodge or wait out a check; the way past
  one is you, in your own browser. DuckDuckGo's human check is reported
  the same way instead of as "no results".
- **Installed prompts update.** The prompt an install copies on its
  first run now follows the installed version and keeps the preferences
  learned on that machine. The file it replaces is kept as
  `instructions.previous.txt`.

### Screen and mapping

- **Names and coordinates for what is on screen.** Every screenshot the
  assistant takes, and every autonomous step, comes with the controls
  of the window in front, read from Windows UI Automation: name, type,
  screen position, and what changed since the last screenshot,
  including a different control at the same coordinates.
  `tool_check_control_at` checks one point before a click.
- **The mapping overlay is a tree.** Screens nest under the control
  that opens them. Hovering a control shows its name, centre, box and
  screen, and outlines it on screen. The overlay stays open when mapping
  ends, and the assistant gets the same tree as text.
- **The same map on the Mapping page**, live: the screens, their
  controls and buttons, the counts and the clock, with the same outline
  on hover. Opened halfway through a run, or after one started from the
  chat, the page shows what it missed.
- **Stop a run, hide the overlay.** The Mapping page has a Stop button
  for any mapping run, the chat's included, and the run keeps what it
  mapped. A switch there hides the overlay now and for the next runs.
  Esc doesn't stop a mapping run, not even inside an autonomous run: the
  mapper presses Esc itself to close menus, and the run would stop
  itself at the first one. When a run ends, the panel stops letting
  clicks through on the Windows side too, not only in Qt, so its X
  always works.
- **The right window is mapped.** The window is picked by its program
  (notepad.exe for "notepad"), then its title. Its window class no
  longer counts, which let a short name like "code" map whichever window
  came first. Kestrel's own window, the taskbar, the desktop and windows
  on another virtual desktop are never mapped, and when nothing matches,
  the message lists the open windows.
- **More is never pressed while mapping**: send, post, reply, pay, buy,
  submit, call, accept, decline, install, like, follow, log out and
  "Don't save", in English, French, Spanish, Portuguese, German, Italian,
  Arabic, Hindi, Chinese, Japanese and Korean. Matched as whole words,
  so "Postal code" isn't taken for "post".
- **Nothing you typed is overwritten.** Mapping types its test value
  only into empty one-line fields it can read. Drop-down lists, number
  boxes and password boxes, where typing would pick an item or change a
  setting, are left alone.
- **The window is put back.** Mapping maximizes the app's window to
  start; when the run ends, however it ends, the window is restored, or
  minimized again if it was.
- **Mapped controls are pressed by name.** `tool_use_mapped_control`
  takes the app and a control's name from the map and finds the control
  in the window as it is now. Mapping used to add one AI tool per
  control, hundreds for one app, each falling back to clicking where the
  control was when the map was made, whatever was there now. A control
  that isn't on screen now isn't pressed: the AI is told which screen
  has it and what opens that screen. The name has to be the whole name
  ("Save" doesn't press "AutoSave"), a minimized app is shown first, and
  destructive controls ask you first.
- **Maps that stopped short aren't reused.** A saved map replaces
  mapping again only when its run finished, and a run that is stopped or
  hits a limit no longer replaces a finished map. Maps saved by earlier
  versions are mapped once more.
- **The Mapping page.** Start always maps afresh and doesn't count
  against the AI's action budget. The outcome is one line, in red when
  the run couldn't start or failed, instead of the text written for the
  AI. Saved maps can be shown again or deleted. A control behind
  Kestrel's window isn't outlined on top of it. Kestrel comes back to the
  front when a run started there ends. The overlay gives the stop
  shortcut while a run goes.
- **One automation at a time.** An AI run, a macro started from the
  Macros page and a mapping run no longer run together: the second is
  refused and told which is running. Starting a mapping run no longer
  clears a stop meant for another run.

### Sounds

- **New sounds**, generated by `tools/make_sounds.py`, played without
  freezing the window or cutting off Read aloud. A WAV dropped into
  `assets/sounds` with the same name replaces one.
- **Each event plays once.** A held shortcut no longer repeats (the
  screenshot shortcut played twice), the stop shortcut's sound is not
  played again when the run winds down, and a stopped macro no longer
  also plays "finished".
- **A Sounds switch** in Settings.

### Window

- **The window drawn twice after Alt+Tab**, a smaller copy of the whole
  app over the top-left of the real one. Qt was drawing at 100% in a
  window Windows had at 125%. The uiautomation package sets the process
  to per-monitor v1 DPI awareness when it loads, and the Mapping page's
  imports loaded it before Qt could set per-monitor v2, the mode Qt is
  built for: the startup log said "DPI awareness per-monitor". Kestrel
  now sets v2 first thing, and uiautomation no longer loads at startup,
  which also takes it off the startup time. If Qt and Windows still
  disagree about the scale, the window is redrawn at the right one when
  you come back to it, and the log says it happened.

### Look

- **New icons.** 51 icons drawn for Kestrel as SVG and tinted to the
  theme. The qtawesome dependency is gone.
- **The purple logo everywhere**: app icon, taskbar, window, chat,
  installer and website favicon, and in the tray, dimmed while working.
- **Menus in the app's colours.** A chat's right-click menu follows the
  theme, like the tray menu, instead of the OS style.
- **Macros page filter bar.** The search box and the filter dropdown are
  the same height, with the same space above and below them.

### Fixes

- **No dark box behind text** in the delete-profile, delete-chat and
  rename-chat dialogs and on cards: a stylesheet with no selector
  painted every label in the window's background colour.
- **Esc stops a run again.** Binding it failed on a missing import and
  the error was only logged. A second missing import sat in the window's
  corner-rounding fallback.
- **Regenerate** re-asks the right message when a screenshot or other
  card sits between the question and the reply.
- **Cards stay above a streamed reply.** With streaming on, a card that
  came with a reply (a screenshot, terminal output, a file) was shown
  below it, in a different order than after a reload, and took the
  reply's Regenerate button until the chat was reloaded.
- **Default preferences.** "likes coffee" was removed from the
  preferences shipped in `ai/instructions.txt`.
- **No crash at the next mapping run after a language change.**
  Changing the language rebuilds every page, and the old Mapping page
  stayed subscribed to mapping runs after it was deleted.

---

## Chat

- **Multiple conversations.** Every chat is saved, titled and listed in
  the sidebar, stored locally in SQLite. Search runs across every
  message you have ever sent.
- **Streaming replies.** Answers arrive token by token from each
  provider's own streaming API rather than appearing all at once.
- **Per-message actions.** Copy, Read aloud, Edit and Regenerate sit
  under every message. Editing resends from that point; regenerate
  applies to the latest reply.
- **A composer that grows.** The input grows with what you type up to
  about nine lines, then scrolls. An expand toggle turns it into a
  drafting area about 45% of the window height, and it collapses itself
  after a send.
- **Long messages collapse.** A long pasted message shows a 14-line
  preview; a long reply is height-capped once it has finished streaming.
  Either expands with a toggle.
- **Code blocks arrive whole.** While the model is still inside a fenced
  block the chat shows a compact placeholder naming the language and
  line count, and the syntax-highlighted block appears in one piece when
  the closing fence lands. Copy and Read aloud see the real text
  throughout.
- **Extended thinking.** A per-message toggle in the composer. The
  reasoning streams into a collapsible section above the reply, which
  collapses to "Thought for 9s" when it finishes.
- **Attachments.** Drag a file onto the composer or use the paperclip.
  Text, code, config, CSV, PDF, Word, Excel and images all attach as
  removable chips. Several files can ride on one message; each is capped
  at 20,000 characters with a visible truncation marker. Images stay
  with the message they were sent with, shown above it.
- **Screenshots into the chat.** A global shortcut captures the screen
  and stages it as a chip so you can look at it before sending. It does
  not steal focus from whatever you were in.
- **Screenshots in the transcript**, the assistant's and yours, at a
  readable size with a caption saying who took them. A click opens the
  whole screenshot inside the window, with zoom, copy and show in
  folder, and the arrow keys step between images.
- **Learned preferences.** The assistant can record something it has
  learned about you, appended to a running list. Where two conflict, the
  most recent wins.
- **One conversation per chat.** Each chat keeps its own context for the
  model, including after a restart.
- **Compaction.** A conversation nearing the model's context budget has
  its older part summarised by the model, and carries on from the
  summary plus the recent messages. `/compact` does it on demand. The
  summary is saved with the chat and readable from the transcript.
- **Sources.** Web pages an answer drew on are listed in a card above
  it.
- **Work mood.** Saying you are in work mode makes replies terser and
  skips small talk for the rest of that conversation. No setting, no
  persisted state.
- **Export and backup.** A chat exports to Markdown or JSON. A full
  backup writes chats, macros, profiles and settings into one archive,
  and restores from it.

## Files and documents

- **Reading.** PDFs, Word documents, spreadsheets, plain text and code
  are parsed to text and put into the conversation. Images go in as
  images for models that can see.
- **Writing.** The assistant produces real files: plain text, Markdown,
  CSV, Word, PDF and Excel. Word documents get real headings and
  paragraphs, PDFs are laid out and paginated, spreadsheets get a header
  row and real cells.
- **Merging to PDF.** Several files combine into one PDF in the order
  given. Images become full pages, existing PDF pages are copied in
  unchanged, and text files are rendered onto new pages titled with
  their own filename. Anything that cannot be converted is named and
  skipped rather than failing the whole merge.
- **Show in folder.** Every generated file drops a card in the chat with
  its name, its folder and a button that opens Explorer with the file
  selected. The cards survive a chat reload.
- **Configurable save location.** Settings chooses where generated files
  land, with Browse and Open buttons. An unwritable path falls back to
  the default and says so at the moment of saving.
- **No name collisions.** A second `report.pdf` becomes `report (2).pdf`
  rather than overwriting the first.

## Web

- **The assistant does it itself.** Searching, reading and crawling
  happen directly, the way chat assistants read the web: no browser on
  screen, no autonomous mode, no macro.
- **Search.** Returns titled results with snippets and links.
- **Reading a page.** Fetches a URL and extracts the article itself,
  headings and paragraphs, with navigation and boilerplate stripped.
  Images worth looking at are pulled out and downscaled first. A page
  that builds itself with JavaScript is read in an off-screen browser
  (Edge or Chrome, headless, with a throwaway profile).
- **Crawling a site.** Looks for a sitemap, falls back to following
  links, and reads up to a page budget. It follows `robots.txt` and its
  Crawl-delay, caps how much it downloads per page, and stays on the
  origin it started from.
- **Honest about itself.** Requests identify as `Kestrel-User`. A
  CAPTCHA, a bot check or a "too many requests" answer stops the read,
  and the reply says so; nothing tries to get past one.
- **Opening a site by name**, from chat or as a macro step.
- **A fresh tab, not yours.** Asked to open a browser that is already
  running, it opens a new tab instead of taking over the one in front of
  you. Chrome, Edge, Firefox, Brave, Opera and Vivaldi are recognised.

## Voice

- **Talk instead of typing.** The mic records one utterance, transcribes
  it, and puts it through the same path a typed message takes. The reply
  is read back aloud.
- **Review before sending.** What the mic heard lands in the composer to
  be read, corrected or thrown away. A Settings option turns the review
  step off for hands-free use.
- **Cancel mid-recording.** Clicking the mic while it is listening
  genuinely interrupts, rather than waiting out the silence timeout.
- **Wake word.** An optional background listener wakes the assistant on
  hearing its name. Off by default, and it tracks whatever the active
  profile is currently called.
- **Say stop.** While a reply is being read aloud, the listener switches
  to listening for "stop", reusing the mic it already has open.
- **Answer the confirmation card out loud.** When an autonomous-mode
  suggestion came from a voice request, the listener switches to
  accept and refuse words until the card is answered, so the whole
  exchange happens without the mouse.
- **Speech-shaped replies.** For voice messages only, the prompt asks
  for phrasing meant to be heard: no markdown, numbers spelled out, two
  to four sentences unless more is clearly needed. The instruction never
  appears in the chat or in the saved conversation.

## Autonomous mode

- **The run loop.** Take a screenshot, call exactly one tool, take
  another screenshot and look at what changed, repeating until the goal
  is met, three steps fail in a row, or the step budget runs out.
- **It asks first.** There is no tool the model can call to start a run.
  It proposes the task in plain language and the app shows an inline
  confirmation card carrying the exact goal. Nothing happens until you
  accept.
- **Configurable step ceiling**, 3 to 100 with a default of 20. A
  ceiling rather than a target.
- **Emergency stop.** `Esc` or a rebindable global shortcut halts a run
  instantly from anywhere, leaves a visible note in the chat and plays a
  sound. Throwing the mouse into a screen corner is a separate failsafe
  that does not depend on either.
- **Human handoff.** Meeting a CAPTCHA, an "I'm not a robot" checkbox or
  a two-factor prompt stops the run rather than clicking through it. A
  sound plays, a card appears, and Continue resumes from a fresh
  screenshot.
- **Names and coordinates.** Each step's screenshot comes with the
  controls of the window in front, read from Windows UI Automation, and
  what changed since the step before, including a different control at
  the same coordinates.
- **Every step's screen is kept**, in a folder per run, and shown in the
  chat as a strip when the run ends.
- **Text by code point.** Typing goes through the Win32 input API by
  code point rather than by keyboard layout, so accented and non-Latin
  text arrives intact.
- **Your mouse stays yours.** Clicks go to the control through UI
  Automation or a window message, and typing to the window the AI
  clicked. Only an app that takes nothing but a real click gets one, with
  the mouse put back at once, and Settings can forbid even that.

## Macros

- **Macros page.** Every macro is a card that runs directly, prompting
  for parameters and converting them to the type the macro expects.
  Cards badge who authored them, and each has delete behind a
  confirmation.
- **Portable JSON on disk.** Each macro is a self-contained record
  rather than generated Python sitting in the source tree. A real
  callable is synthesized from it at load time, which is what makes
  deleting, replacing and sharing possible.
- **Export, import and share.** A share button writes one macro out as a
  `.ktl` file; the toolbar offers import and export-all. An import never
  silently overwrites: a name clash is reported and you choose.
- **Delete or replace by asking.** The assistant can drop a broken macro
  by name, fuzzy matched, clearing both the stored record and the live
  function. Re-recording under an existing name replaces it.
- **Generalization.** A literal recording tied to one value becomes a
  parameterized macro taking an argument, through a dedicated model
  session with its own prompt. It runs automatically after autonomous
  mode saves a macro and manually from a button on the Recorder page. On
  low confidence it keeps the original untouched.
- **Both versions are kept**, so you can pick the exact recording or the
  flexible one.
- **Clicks that survive a moved button.** A generalized macro tries a
  described element first and falls back to the recorded coordinates, so
  a small layout shift does not break it.
- **Configurable playback delay**, 0.1 to 10 seconds.
- **Only real recordings are saved.** A run that did nothing on screen,
  because it only read the web for example, saves no macro.

## Block editor

- **Scratch-style blocks.** A macro is a stack of rounded, colour-tinted
  rows: blue acts, amber controls the flow, teal looks at the screen.
  Nesting is shown by indentation.
- **Wait, repeat, forever, branch and stop.** Repeat takes a count,
  forever has its own iteration cap, and a branch tests the colour at a
  point.
- **Inline editable values.** Every value is a clickable slot in the
  block itself, so a step reads as a sentence you edit in place.
- **Pick values off the screen.** A colour slot opens the picker with
  its eyedropper; a position slot offers a timed pick from the screen.
- **Colour change watcher.** Waits for the colour at a point to change,
  but aborts if it changes to a colour you named as the failure state,
  so a macro can guard itself against an error instead of clicking on
  through it.
- **Drag to reorder**, with a grip handle marking the affordance. A
  C-block takes its contents with it, so a drag can never separate an
  opener from its end.
- **Structure that cannot go unbalanced.** Adding a C-block inserts its
  matching end; deleting one removes its partner and keeps the contents.
  Structure is validated on save.
- **Macros inside macros.** Any saved macro drops into another as a
  single block. A macro is never offered inside itself, a cycle is
  refused at run time rather than hanging, and nesting is capped at
  eight levels.
- **One program, two runtimes.** The same blocks drive the replay
  interpreter for literal macros and real nested Python for generalized
  ones, with repeat compiled to a loop and a colour branch to if/else.
- **Editor window** with the macro's name, a Generalized or Literal
  badge, the description as a field, and the blocks in a carded panel.

## Recorder

- **A page of its own**, reachable from the sidebar and from the Macros
  page, driven by a non-blocking global hotkey controller.
- **Blocks as you record.** Captured actions appear as blocks with the
  control and sensing palette underneath, so the logic is wrapped around
  the clicks before the macro is saved. Captured and hand-added blocks
  save, replay and generalize as one list.
- **Left click, right click, typing mode and Enter**, each on its own
  trigger. Button choice is carried through recording, replay and
  generalization, so a macro can open a context menu.
- **Rebindable triggers**, to another key or to a mouse button. The
  page's own subtitle and status line describe whatever is currently
  bound.

## UI mapping

- **Structural discovery.** Reads an application's accessibility tree
  through Windows UI Automation rather than by vision, walking it as a
  bounded depth-first search with loop detection and backtracking. The
  result is a state graph saved per app.
- **Controls by name.** Once an app is mapped, the assistant presses a
  control or types into a field by its name from the map
  (`tool_use_mapped_control`). The control is found in the window as it
  is now; one that isn't on screen isn't clicked.
- **Context aware.** Elements are kept with the panel, dialog or tab
  they belong to, so a Save in one dialog is not confused with a Save in
  another.
- **Destructive actions are skipped and gated.** Anything whose label
  reads as destructive (delete, send, pay, post, log out and the like, in
  eleven languages) is recorded but never clicked during mapping, and
  pressing one afterwards asks you first. Only empty one-line fields get
  the test value.
- **Finished maps are reused**, with a force-remap option for when the
  app has changed. A map from a run that stopped short is mapped again.
- **The window is left as it was**: maximized for the run, then
  restored.
- **Map overlay.** A small window builds the map as a tree while
  mapping runs, screens nested under the control that opens them.
  Hovering a control shows its name, centre, box and screen and outlines
  it on screen. It stays open when mapping ends, and the assistant gets
  the same tree as text.
- **A Mapping page** with an app name field, Start and Stop buttons, and
  the same map as the overlay, built live on a background thread. A
  switch there turns the overlay off, and saved maps can be shown again
  or deleted.

## Models and profiles

- **Four providers.** Gemini, OpenAI and Anthropic behind one
  tool-calling interface, with each provider's schema generated from the
  same Python signatures. Local is the fourth: anything exposing an
  OpenAI-compatible endpoint, such as Ollama, LM Studio, llama.cpp or
  vLLM, with the same tool calling, image input and streaming.
- **Model switcher** in the chat topbar.
- **Assistant profiles.** A profile bundles a provider, model,
  instructions and assistant name. Switching happens in the background:
  the pickers update immediately, a message sent mid-switch is handed
  off when the new session is live, and a failed switch rolls back.
- **Name your assistant**, from Settings and from a first-run screen
  shown before anything else. The name is injected into the system
  prompt through a placeholder rendered at load time, and it is also the
  wake word.
- **Local model settings** for the server URL, model name and an
  optional bearer token for a LAN server that wants one.

## Interface

- **Light and dark themes**, applied instantly across every dialog,
  menu, dropdown and tray icon.
- **Seven languages.** English, Arabic, Chinese, French, Hindi,
  Portuguese and Spanish, switched live, with right-to-left layout for
  Arabic. Anything untranslated falls back to English rather than going
  blank.
- **Live console**, opened from the tray, mirroring everything the app
  logs, colour-coded by line type. It replays the recent buffer when
  opened, so it is never blank just because you opened it late.
- **Undo** for destructive actions, offered as a bar rather than a
  dialog that makes you promise first.
- **Activity indicators.** A thin rounded border breathes around the
  screen while the assistant is acting, a floating pill names the
  current phase, and the taskbar icon blinks. The typing indicator is
  the app's logo assembling itself.
- **Notification sounds** for things that happen while the window is not
  in front: an automation finishing, with different tones for success
  and for stopped, a macro finishing, the wake word being heard, a voice
  confirmation answered, the microphone opening and closing, a
  screenshot being taken, and a step handed to you. Each event plays
  once, and a switch in Settings turns them off.
- **Icons drawn for Kestrel**, as SVG, tinted to the theme.
- **Rebindable global shortcuts** for show and hide, always on top,
  screenshot and stop automation.
- **Always on top**, from the sidebar or its shortcut.
- **Tray icon**, with the console and quit on its menu.
- **Right-click menus** on sidebar chats, in the app's own colours.
- **One instance.** Launching Kestrel again brings up the running
  window.
- **A rounded frameless window** that still snaps, tiles and resizes
  like a normal Windows window, because the OS is still doing the
  moving.

## Safety

- **Approval enforced in code.** Running a command or force-closing an
  application shows a real modal before it happens. It defaults to No
  and fails closed: with no approval handler connected the action is
  denied, never silently allowed.
- **Command allow-list.** Commands are tokenized and checked against an
  explicit list with a risk tier each, refused outright if they carry
  chaining or redirection characters or a destructive base command, then
  run as an argument list with no shell interpolation anywhere.
- **Per-session action limits**, 100 low, 30 medium and 5 high by
  default, with the caps and a manual reset in Settings.
- **Destructive labels need confirmation**, separate from the
  allow-list, including a mapped control the assistant presses.
- **One automation at a time.** An AI run, a macro and a mapping run
  never click at once; the one that comes second is refused.
- **API keys in the OS credential store**, Windows Credential Manager,
  Keychain or Secret Service, with a one-time migration of any existing
  plaintext value.
- **Identity.** Asked what model is behind it, the assistant answers as
  itself and does not discuss a specific vendor.

## Terminal

- **Run Windows commands**, with a 60-second timeout and output capped
  at 4,000 characters, subject to the allow-list and the caps above.
- **Output cards in chat.** A command appears as a card whose header
  carries the command and a succeeded or failed badge. A silent command
  gets no body; when there is output, the body is sized to how much
  there is.

## Maintenance

- **Installer.** A native per-user setup program with updates that
  cannot leave a half-installed app, a complete uninstall, and a command
  line for unattended installs.
- **Update checking.** The app compares its version against the latest
  published release and raises a tray notification when a newer one
  exists. Check only: it does not install anything itself.
- **Opt-in crash reporting**, off by default, crash-only, with no
  prompts or activity included. The Privacy section in Settings appears
  only once a reporting endpoint is actually configured.
- **Launching and focusing applications.** Colloquial names resolve to
  real executables, and the built-in Windows utilities launch by
  executable name or system URI so they open regardless of the display
  language. An app already running is brought to the front rather than
  relaunched.
