# Features

Everything Kestrel can do, by area. This is a list of capabilities, not
a release history: there are no dates, no version sections and no bug
fixes here. If you want to know what the app does, read this. If you
want to know what changed between two builds, read the commit log.

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
  at 20,000 characters with a visible truncation marker.
- **Screenshots into the chat.** A global shortcut captures the screen
  and stages it as a chip so you can look at it before sending. It does
  not steal focus from whatever you were in.
- **Screenshots the assistant takes** land in the transcript as
  thumbnails, saved like any other message.
- **Learned preferences.** The assistant can record something it has
  learned about you, appended to a running list. Where two conflict, the
  most recent wins.
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

- **Search.** Returns titled results with snippets and links.
- **Reading a page.** Fetches a URL and extracts the article itself,
  headings and paragraphs, with navigation and boilerplate stripped.
  Images worth looking at are pulled out and downscaled first.
- **Crawling a site.** Looks for a sitemap, falls back to following
  links, and reads up to a page budget. It checks `robots.txt`, caps how
  much it downloads per page, and stays on the origin it started from.
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
- **Text by code point.** Typing goes through the Win32 input API by
  code point rather than by keyboard layout, so accented and non-Latin
  text arrives intact.

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
- **Compiled actions.** The map compiles into small parameterized click
  and input functions for that app, registered live into the tool set
  with no restart. At playback each one re-resolves its element by
  accessibility path and falls back to a bounding-box click.
- **Context aware.** Elements are kept with the panel, dialog or tab
  they belong to, so a Save in one dialog is not confused with a Save in
  another.
- **Destructive actions are skipped and gated.** Anything whose label
  reads as destructive is recorded but never clicked during mapping, and
  a compiled destructive action needs an explicit confirmation argument
  before it will run.
- **Saved maps are reused**, with a force-remap option for when the app
  has genuinely changed.
- **Progress overlay.** A small status window shows elapsed time and the
  screens found so far. It is excluded from screen capture, so it cannot
  end up inside an unrelated screenshot.
- **A Mapping page** with an app name field, a Start button and the same
  live progress, running on a background thread.

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
  confirmation answered, and a screenshot being taken.
- **Rebindable global shortcuts** for show and hide, always on top,
  screenshot and stop automation.
- **Always on top**, from the sidebar or its shortcut.
- **Tray icon**, with the console and quit on its menu.
- **Right-click menus** on sidebar chats, using native OS menu styling.
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
  allow-list, including inside a compiled mapping macro.
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
