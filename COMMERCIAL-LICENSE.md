# Commercial licensing

Kestrel is free under the GPL v3. This document is about the other
option: using it without the GPL's obligations.

**Contact: leafeon.void@gmail.com**

> Replace that address, and the copyright holder named in `LICENSE`,
> before publishing. They are placeholders.

---

## Do you need one?

Ask one question: **are you giving the software to anyone outside your
own organisation?**

| What you are doing | What you need |
|---|---|
| Using Kestrel yourself, personally | GPL. Free. |
| Modifying it and keeping the changes to yourself | GPL. Free. |
| Modifying it and publishing your version under the GPL | GPL. Free. |
| Running it internally at a company, unmodified, not distributed | GPL. Free. |
| Shipping it, or part of it, inside a product you sell | **Commercial** |
| Building a closed-source product on top of it | **Commercial** |
| Distributing a modified version without publishing your source | **Commercial** |
| Offering it to customers as a hosted service | Talk to us first |

The last row is genuinely unsettled under GPL v3, which has no network
clause (that is the AGPL). Saying so here is more useful than pretending
the answer is obvious.

Output you produce with Kestrel - documents, macros, recordings - is
yours under either licence. Neither one reaches your work.

---

## What a commercial licence gives you

- The right to use, modify and distribute Kestrel inside proprietary
  software, with no obligation to publish your own source.
- Written terms you can hand to your own legal team, rather than an
  argument about what the GPL requires in your particular deployment.

Terms and pricing are not published here, because they depend on
headcount and on how the software is deployed. Get in touch.

---

## Third-party obligations

**This section is the one that matters, and it is easy to miss.**

Kestrel's own licence is not the only one involved. Its dependencies
carry their own, and one of them has the same dual-licensing structure
as Kestrel itself:

### PyQt6 - GPL v3 or a commercial licence from Riverbank Computing

PyQt6 is not permissively licensed. Riverbank Computing dual-licenses it
exactly the way Kestrel is dual-licensed here: GPL v3, or a paid
commercial licence.

The consequence: **selling a Kestrel commercial licence does not free
your customer from PyQt6's terms, and it does not free you from them
either.** A proprietary product built on Kestrel needs a commercial
PyQt6 licence from Riverbank as well as a commercial licence from us.
Two licences, two vendors.

This is not a problem with the arrangement, it is how the Qt ecosystem
works, and it is the single most common way a dual-licensed PyQt
project gets its commercial offering wrong. Confirm current terms
directly with Riverbank before quoting anyone a price.

Two ways out, if that is unwelcome:

- Switch the UI layer to PySide6, the Qt Company's own bindings, which
  are LGPL. The LGPL permits proprietary use when Qt is dynamically
  linked and replaceable. The port is real work but the APIs are close.
- Price a Riverbank commercial licence into what you charge.

### Everything else

The remaining direct dependencies are permissive (MIT, BSD, Apache 2.0,
PSF, HPND) and impose no obligation beyond keeping their notices. They
are compatible with both the GPL and a proprietary distribution:

```
AppOpener  Pillow  PyAudio  SpeechRecognition  anthropic  google-genai
keyboard  keyring  mouse  openai  openpyxl  psutil  pyautogui  pypdf
pyperclip  python-docx  python-dotenv  pywin32  reportlab
requests  sentry-sdk  uiautomation
```

Verify this list yourself before shipping. Licences change between
versions, and this was compiled by reading, not by running an audit
tool. `pip-licenses` will produce the authoritative list for the exact
versions you ship.

---

## Contributions

Selling a commercial licence requires owning the rights to the whole
work. `LICENSE` therefore asks contributors to grant the copyright
holder the right to relicense their contribution.

For anything beyond a small patch, use a real Contributor Licence
Agreement rather than a paragraph in a licence file. The Apache
Individual CLA is the usual starting point.
