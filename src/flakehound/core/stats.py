"""Small, dependency-free statistics helpers."""

from __future__ import annotations

import math


def wilson_interval(failures: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a failure rate.

    Better than the naive p +/- 1.96*sqrt(p(1-p)/n) for small samples and for
    rates near 0 or 1, which is exactly our situation (few runs, rare failures).
    """
    if trials <= 0:
        return (0.0, 1.0)
    if not 0 <= failures <= trials:
        raise ValueError("failures must be between 0 and trials")
    p = failures / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
