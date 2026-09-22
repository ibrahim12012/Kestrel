END-USER LICENSE AGREEMENT (EULA) FOR KESTREL

This sits alongside LICENSE, which says under which terms you may use
Kestrel: the GPL v3, or a commercial licence. This file covers what using it means for you: what
it does to your computer, what its safeguards do and do not guarantee,
and what nobody is liable for. Where the two disagree, LICENSE wins.

Last updated: 2026

PLEASE READ THIS AGREEMENT CAREFULLY BEFORE INSTALLING OR USING KESTREL.
BY INSTALLING, COPYING, OR OTHERWISE USING KESTREL, YOU AGREE TO BE
BOUND BY THE TERMS OF THIS AGREEMENT.

1. WHAT KESTREL DOES

Kestrel is a desktop application that uses an AI model (currently
Google's Gemini API) to interpret natural-language instructions and, on
your behalf, take actions on your computer: opening and closing
applications, typing, clicking, running a fixed allow-list of command-
line programs, and, in "autonomous mode," chaining several such actions
together toward a goal you describe. Kestrel also includes safeguards
(see Section 4) intended to reduce - but which cannot eliminate - the
risk of unintended actions.

2. NO WARRANTY

KESTREL IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. THE AUTHORS DO
NOT WARRANT THAT KESTREL WILL CORRECTLY INTERPRET YOUR INSTRUCTIONS,
THAT ITS AI COMPONENT WILL BEHAVE PREDICTABLY, OR THAT ITS SAFETY
SAFEGUARDS WILL CATCH EVERY UNINTENDED OR DESTRUCTIVE ACTION.

3. LIMITATION OF LIABILITY

TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR EXEMPLARY DAMAGES (INCLUDING BUT
NOT LIMITED TO: LOSS OF DATA, LOSS OF PROFITS, SYSTEM DAMAGE, OR THE
COST OF SUBSTITUTE SOFTWARE) ARISING FROM AN ACTION KESTREL'S AI
COMPONENT TAKES ON YOUR HARDWARE, WHETHER IN AUTONOMOUS MODE OR
OTHERWISE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. YOU USE
KESTREL'S AUTOMATION FEATURES ENTIRELY AT YOUR OWN RISK.

4. SAFETY DESIGN - WHAT IT DOES AND DOES NOT GUARANTEE

Kestrel classifies system actions into risk tiers and:
  - Refuses any command not on an explicit allow-list.
  - Requires an explicit, code-enforced human approval (a dialog you
    must click "Yes" on) before running a HIGH-risk action - deleting
    files, closing applications, or similar - regardless of what the
    AI itself decided to do.
  - Caps how many actions of each risk tier can run unattended before
    stopping for a human to look again.
  - Pauses and hands control to you for identity-verification
    challenges (CAPTCHAs, 2FA/MFA codes) rather than attempting them.

These are mitigations, not guarantees. An AI model can still
misinterpret an instruction in ways that lead to a LOW- or MEDIUM-risk
action (e.g. installing a package, copying a file) you did not intend.
You are responsible for reviewing what Kestrel proposes to do, for the
consequences of approving a HIGH-risk action, and for maintaining your
own backups of anything important.

5. YOUR AI API KEY / ACCOUNT

If you use your own Gemini API key, your use of the underlying AI model
is additionally governed by Google's own terms for that API. Kestrel is
not responsible for costs, rate limits, or content-policy decisions
made by that underlying provider.

6. UPDATES AND TELEMETRY

Kestrel may check for newer versions (see docs/BUILD_SECURE.md - this
only checks and links to the release page, it does not install
anything automatically). Crash reporting is opt-in and off by default;
see docs/TELEMETRY_SETUP.md and Settings -> Privacy.

7. TERMINATION

This license is effective until terminated. It terminates automatically
if you fail to comply with its terms. Upon termination, you must cease
all use of Kestrel and destroy all copies in your possession.

8. GOVERNING LAW

This Agreement is governed by the laws of the jurisdiction in which the
publisher is established, without regard to conflict-of-law principles,
except where local consumer-protection law mandates otherwise.

---

This EULA governs END-USE of the distributed Kestrel application. It is
separate from, and does not modify, the license under which Kestrel's
source code is published (see LICENSE) - if you are distributing
Kestrel commercially under different terms than the current source
license, update LICENSE accordingly before shipping; this EULA alone
does not restrict copying/redistribution that the source license
already permits. See the note in LICENSE.
