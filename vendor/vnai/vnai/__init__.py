"""
Local stub for the upstream `vnai` package.

As of 2026-10, PyPI quarantined `vnai` (a usage-telemetry / license-
acceptance companion that vnstock3 imports unconditionally) with zero
installable files, which broke `pip install vnstock3` everywhere —
including our CI. vnstock3 only touches vnai for license bookkeeping and
an execution-timing decorator, neither of which this project needs, so
this stub satisfies the import without phoning home anywhere.

See vendor/vnai/README.md and requirements.txt for context.
"""


def setup(*args, **kwargs):
    return None


def accept_license_terms(*args, **kwargs):
    return True


def optimize_execution(*args, **kwargs):
    def decorator(func):
        return func

    return decorator
