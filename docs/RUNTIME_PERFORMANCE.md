# Would C be faster than this? Would Electron be slower?

An analysis of what changing runtime would actually buy Kestrel, across
the three axes worth caring about: memory footprint, startup time, and
UI event latency.

**Short answer.** Against Electron, Kestrel already wins every axis by a
wide margin, and switching to it would be a large regression. Against C,
it would win memory clearly and startup meaningfully, and would be
indistinguishable on UI event latency. None of that is the interesting
part.

The interesting part is that neither rewrite touches a single one of the
delays anyone has actually complained about. Both of the ~2 second waits
fixed this week were network and driver time with no application code in
them at all. A C rewrite reproduces both exactly.

---

## What was measured, and where

Measured on this machine, CPython 3.11.15, best of five runs:

| | cold start | peak RSS |
|---|---:|---:|
| bare interpreter | 10.6 ms | 10.3 MB |
| + `PIL.Image` | 46.4 ms | 14.6 MB |
| + `requests` | 133.7 ms | 28.6 MB |
| + `numpy` | 86.5 ms | 25.0 MB |
| all three | 206.2 ms | 44.7 MB |

Python call overhead, which is what a UI event handler pays:

| | |
|---|---:|
| trivial function call | 35 ns |
| attribute access | 19 ns |
| `sum(range(100))` | 0.59 µs |
| **60 Hz frame budget** | **16,667 µs** |
| trivial calls that fit in one frame | ~475,000 |

Qt and Electron could not be measured here (no PyQt6, no display, no
Node). Figures for those below are estimates from published
measurements and are marked as such. Everything in the two tables above
is real.

---

## Memory footprint

| | typical RSS | what dominates |
|---|---:|---|
| C + Win32/Direct2D | 5-15 MB | the bitmaps |
| **Kestrel today (PyQt6)** | **~90-150 MB** *(est.)* | Qt libraries, then the SDK imports |
| Electron | 200-400 MB *(est.)* | three Chromium processes before any app code |

The measured 44.7 MB above is the SDK layer alone, and Kestrel imports
considerably more than those three: `google-genai`, `openai`,
`anthropic`, `keyring`, `pyautogui`, `keyboard`, `mouse`, `uiautomation`,
plus Qt itself.

Electron's floor is structural rather than incidental: a main process, a
renderer and a GPU process, each carrying a share of Chromium, before
the application has done anything. There is no configuration that gets
it near 50 MB.

**Verdict:** C wins by roughly 10x. On a desktop with 16 GB, winning
10x on 120 MB is winning 110 MB, which is a rounding error against a
single Chrome tab. This axis does not justify anything.

---

## Startup time

| | cold start | *(est.)* |
|---|---:|---|
| C + Win32 | 30-100 ms | |
| **Kestrel today** | **~1.5-3 s of imports** | measured import costs extrapolated over its full dependency set |
| Electron | 300 ms to 1.5 s | Chromium init dominates; app code is noise against it |

This is the one axis where the runtime genuinely costs Kestrel
something. Python import is real, serial work: reading, parsing and
executing module code for a dependency tree that includes three AI SDKs.

Two things worth knowing before acting on it.

**First, the app currently sleeps longer than it loads.**
`app/splash_screen.py` holds the splash for a minimum of **2.6 seconds**:

```python
def ensure_minimum_display_time(self, min_seconds: float = 2.6) -> None:
```

That is deliberate - it stops the splash flashing past on a fast
machine - but it means startup cannot go below 2.6 s no matter what the
code does. A full C rewrite landing at 80 ms would still show a 2.6 s
splash. **Any startup work that does not start by changing this number
is measuring something the user never experiences.**

**Second, most of the import cost is avoidable without changing
language.** `ai/main.py` imports `google.genai`, `openai` and
`anthropic` at module level, and any given session uses exactly one of
them. Importing the other two is pure waste paid on every launch.
Deferring them into the provider factory is perhaps twenty lines.

**Verdict:** C wins by 20-30x on paper. Roughly half of Kestrel's real
cost is recoverable with lazy imports, and all of it is currently hidden
behind a deliberate 2.6 s wait.

---

## UI event latency

This is the axis where the intuition is most wrong.

| | click to painted result | why |
|---|---:|---|
| C + Win32 | ~0.1-1 ms | event → function → Direct2D |
| **Kestrel today (PyQt6)** | **~0.2-2 ms** *(est.)* | event → Qt C++ → one Python call → Qt C++ paint |
| Electron | 5-30 ms *(est.)* | JS → DOM mutation → style → layout → paint → composite, minimum one frame |

PyQt6 is not "Python doing UI work". Event loop, hit testing, layout,
painting and compositing are all Qt's C++. Python appears once per
event, to run the handler. That handler costs 35 ns to enter, and around
475,000 of them fit inside a single 60 Hz frame.

So a Qt app written in Python and the same app written in C++ have the
same UI latency, to within noise, unless the handler itself does heavy
work. Where Kestrel's handlers *do* heavy work, they do it for reasons
that are not the language: the em-dash-era layout bugs fixed earlier
were `sizeHint` and alignment-flag problems in Qt's own C++ layout
engine, and would have been identical bugs in C++.

Electron is genuinely slower here, and structurally so. Every visual
change goes through a style and layout pass over a DOM. That is a large
amount of machinery to move a list row.

**Verdict:** C wins by a margin too small to perceive. Electron loses by
a margin that is perceptible.

---

## Where Kestrel's latency actually is

This is the part that matters, and it is why the table above is mostly
beside the point.

The two delays raised this week, both roughly two seconds:

**Voice mode cold start.** Time went to: loading the PortAudio DLL,
enumerating every audio host API and device, and a hard-coded
`adjust_for_ambient_noise(duration=0.5)` that reads the microphone for
exactly half a second and cannot return early. The enumeration is C code
inside PyAudio and the Windows audio stack. The half second is a
deliberate wall-clock wait. **Python's share of that two seconds is
approximately zero**, and a C rewrite would load the same DLL, enumerate
the same devices and wait the same half second.

The fix was not a faster language. It was doing the enumeration during
the splash screen instead of when the user asks for the microphone, and
shortening a calibration that no longer needed to be long.

**Read aloud.** Time went to: an OS keyring lookup (a DPAPI round trip),
constructing an HTTP client, and one blocking network request to Gemini
to synthesize ~240 characters of speech - about fifteen seconds of
audio, none of which could play until all of it arrived. **Again
approximately zero Python compute.** A C client would have made the same
request and waited the same time.

The fix was making the first request small - one sentence rather than a
greedily packed paragraph - and starting the second request alongside
the first rather than after it. The first synthesis request dropped from
212 characters to 67, a 68% reduction, with no text lost.

Neither of these is unusual for this app. Kestrel spends its time
waiting on AI APIs, on screenshot capture, on UI Automation walking
another process's accessibility tree, and on the speech services. All of
it is I/O or someone else's C. **A language change moves none of it.**

---

## What to do instead

Ranked by real effect on what a user perceives:

1. **Lower or remove the 2.6 s splash minimum.** One number. It is
   currently the single largest component of perceived startup, and it
   is self-inflicted. If the splash is wanted for its own sake, that is
   a legitimate product choice - but then startup time is a product
   decision, not an engineering one, and should stop being discussed as
   the latter.
2. **Lazy-import the two unused provider SDKs.** Roughly twenty lines
   for a meaningful share of the remaining import cost.
3. **Keep moving work off the critical path.** `prewarm_audio` and
   `prewarm_voice` are the pattern: a cost the user does not wait for is
   worth more than a cost made 30% smaller.
4. **Stream the TTS response** if the API gains a streaming mode, or
   cache synthesized audio per message so a re-read is instant. Both
   attack real, measured wait time.
5. **Nothing about the runtime.** 31,000 lines of application code plus
   14,700 lines of tests and four Qt Designer files, rewritten in C, to
   win 110 MB of RAM and a startup improvement currently masked by a
   deliberate sleep - while every latency anyone has complained about
   stays exactly where it is.

Electron is not a consideration in the other direction: it would cost
memory, cost startup, cost UI latency, and would additionally have to
reimplement the global keyboard hooks, input injection and UI Automation
access through native modules anyway, which is the part Python is
already good at reaching.
