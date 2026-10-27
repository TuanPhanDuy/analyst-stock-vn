"""
Local stub for vnstock_ezchart.mplot.

Every real vnstock_ezchart release on PyPI depends on the legacy `vnstock`
package, which was removed from PyPI entirely in 2026-10 — so the real
package can't be installed anymore either. vnstock3 only imports MPlot and
Utils at module load time to build its (unused, by this project) Chart
wrapper; this project never plots, so no-op classes are enough to satisfy
the import.
"""


class MPlot:
    pass


class Utils:
    pass
