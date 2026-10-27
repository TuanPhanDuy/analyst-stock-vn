# vendor/vnai

A minimal local stand-in for the `vnai` package.

`vnstock3` imports `vnai` unconditionally (license-acceptance bookkeeping
and an `optimize_execution` timing decorator). Since 2026-10, PyPI has
quarantined `vnai` with zero installable files, so `pip install vnstock3`
fails everywhere, including CI, until that's lifted.

This stub implements only the three symbols vnstock3 actually calls
(`setup`, `accept_license_terms`, `optimize_execution`) as no-ops — no
telemetry, no network calls. `requirements.txt` installs it from this path
so `vnstock3`'s `vnai>=2.0` dependency resolves locally instead of hitting
PyPI.

Delete this directory and the `./vendor/vnai` line in `requirements.txt`
once PyPI un-quarantines the real `vnai`.
