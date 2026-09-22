# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/errors.py
=======================
Single exception type for the discovery engine, kept in its own module
(rather than in accessibility.py or mapper.py) so any part of the
package can raise/catch it without a circular import.
"""


class DiscoveryError(Exception):
    """
    Raised when UI discovery can't proceed at all - no accessibility
    backend available for this OS/environment, the target app/window
    couldn't be found, or the accessibility tree came back empty.
    Never raised for "this one element couldn't be read" - those are
    logged and skipped by the mapper so a single bad node doesn't abort
    an otherwise-useful mapping run.
    """
