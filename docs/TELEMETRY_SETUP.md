# Crash reporting setup (Sentry)

1. Create a free project at https://sentry.io (platform: Python).
2. Copy its DSN into `.env`:
   ```
   SENTRY_DSN=https://xxxx@xxxx.ingest.sentry.io/xxxx
   ```
3. That's it - `KESTREL_TELEMETRY_ENABLED` stays `0` (off) until a user
   explicitly opts in from Settings -> Privacy. Until both a DSN is
   configured AND a user opts in, nothing is ever sent.

What's captured: unhandled exceptions only (via `sys.excepthook`, see
`ai/telemetry.py`) - no prompts, no file contents, no activity logs,
`send_default_pii=False`. If you need more (breadcrumbs, user context)
later, that's a deliberate expansion of what's collected and should be
disclosed to users, not something to add quietly.
