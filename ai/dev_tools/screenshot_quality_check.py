# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/dev_tools/screenshot_quality_check.py
==========================================
Referenced from ai.main.SCREENSHOT_QUALITY_PRESETS' own comment - this is
the actual script, not just a comment pointing at one. Takes a real
screenshot (or an image file you pass in) and runs it through every
preset in ai.main.prepare_autonomous_screenshot, printing the resulting
pixel dimensions and byte size for each so the real difference is visible
locally, not asserted.

Usage:
    python -m ai.dev_tools.screenshot_quality_check              # captures your actual screen
    python -m ai.dev_tools.screenshot_quality_check path/to.png  # uses an existing image instead

Note on what this does and doesn't prove: pixel-count reduction is the
part that's documented to reduce Gemini's image TOKEN count (its vision
encoder tokenizes by resolution/tile count). Byte size is a real,
verifiable number this script can show you directly - useful for transfer
size/latency - but isn't the same measurement as token count. For the
actual token number on a real API call, run the app for real with
GEMINI_API_KEY set: ai.main.run_autonomous_mode already logs
"[autonomous] Screenshot tokens this step (<preset>): N" via a free
count_tokens() call on every step.
"""
import io
import sys

from PIL import Image, ImageGrab

from ai.main import SCREENSHOT_QUALITY_PRESETS, prepare_autonomous_screenshot


def _byte_size(image_or_part) -> int:
    if isinstance(image_or_part, Image.Image):
        buf = io.BytesIO()
        image_or_part.save(buf, format="PNG")
        return len(buf.getvalue())
    # types.Part.from_bytes(...) - the compressed presets return this
    return len(image_or_part.inline_data.data)


def main() -> None:
    if len(sys.argv) > 1:
        screen = Image.open(sys.argv[1])
        print(f"Using image file: {sys.argv[1]}")
    else:
        screen = ImageGrab.grab()
        print("Captured your actual screen.")

    orig_px = screen.width * screen.height
    orig_bytes = _byte_size(screen)
    print(f"\noriginal : {screen.width}x{screen.height}  bytes={orig_bytes:,}  pixels={orig_px:,}")

    for name in SCREENSHOT_QUALITY_PRESETS:
        if name == "original":
            continue
        result = prepare_autonomous_screenshot(screen, quality=name)
        b = _byte_size(result)
        preset = SCREENSHOT_QUALITY_PRESETS[name]
        max_side = preset["max_side"]
        scale = min(1.0, max_side / max(screen.width, screen.height)) if max_side else 1.0
        new_w, new_h = round(screen.width * scale), round(screen.height * scale)
        px = new_w * new_h
        print(
            f"{name:8s}: {new_w}x{new_h}  bytes={b:,} "
            f"({100 * b / orig_bytes:.1f}% of original bytes)  "
            f"pixels={px:,} ({orig_px / px:.2f}x fewer)"
        )


if __name__ == "__main__":
    main()
