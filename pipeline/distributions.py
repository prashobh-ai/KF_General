"""Statistical shape for synthetic corpora.

Uniformly random data is the fastest way to make a synthetic corpus feel fake.
Real enterprise data is lumpy: a handful of codes account for most of the
volume, incidents cluster seasonally, and document age follows a decay curve
because old documents get superseded. This module supplies those shapes so the
generated corpus is believable in aggregate, not just per document.
"""

from __future__ import annotations

import datetime as dt
import math
import random


def zipf_weights(n: int, exponent: float = 1.1) -> list[float]:
    """Zipf-like weights for `n` items.

    Used for code selection. In a real claims corpus a few CARC codes carry
    most denials and a long tail appears once or twice; drawing uniformly from
    the code list would give every code equal footing and destroy that signal.
    """
    return [1.0 / ((i + 1) ** exponent) for i in range(n)]


def weighted_choice(rng: random.Random, items, weights=None):
    if weights is None:
        weights = zipf_weights(len(items))
    return rng.choices(list(items), weights=weights, k=1)[0]


def lognormal_int(rng: random.Random, median: float, sigma: float,
                  lo: int = 1, hi: int | None = None) -> int:
    """Long-tailed positive integer.

    Claim amounts, delay minutes, defect counts and test durations are all
    right-skewed — mostly small with a thin tail of large values. A normal
    distribution would produce symmetric noise that looks nothing like this.
    """
    v = rng.lognormvariate(math.log(median), sigma)
    v = max(lo, int(round(v)))
    return min(v, hi) if hi else v


def seasonal_weight(day: dt.date, peak_month: int, strength: float = 0.45) -> float:
    """A smooth annual cycle peaking in `peak_month`.

    Airlines peak in summer, retail in Q4, health systems in winter. A corpus
    whose documents are spread evenly across the year reads as generated.
    """
    phase = (day.month - peak_month) / 12.0 * 2 * math.pi
    return 1.0 + strength * math.cos(phase)


class DateSpread:
    """Draw document dates with recency bias and seasonality.

    Document management systems skew recent: older revisions get superseded and
    fall out of the effective set. An exponential decay over the window
    reproduces that without needing to model supersession explicitly.
    """

    def __init__(self, rng: random.Random, *, years: int = 3,
                 peak_month: int = 6, end: dt.date | None = None):
        self.rng = rng
        self.end = end or dt.date(2026, 5, 31)
        self.start = self.end - dt.timedelta(days=365 * years)
        self.peak_month = peak_month
        self.span = (self.end - self.start).days

    def draw(self) -> dt.date:
        for _ in range(12):
            # Exponential recency bias: most mass in the last third.
            u = self.rng.random() ** 1.25
            day = self.end - dt.timedelta(days=int(u * self.span))
            if self.rng.random() < seasonal_weight(day, self.peak_month) / 1.5:
                return day
        return day


def bucketed_counts(rng: random.Random, total: int, buckets: int,
                    concentration: float = 1.3) -> list[int]:
    """Split `total` across `buckets` with realistic concentration.

    Document counts per organisational unit are never even — a couple of units
    generate most of the paperwork.
    """
    w = [1.0 / ((i + 1) ** concentration) for i in range(buckets)]
    rng.shuffle(w)
    s = sum(w)
    counts = [max(1, int(round(total * x / s))) for x in w]
    # Reconcile rounding drift against the requested total.
    drift = total - sum(counts)
    i = 0
    while drift != 0 and buckets:
        idx = i % buckets
        if drift > 0:
            counts[idx] += 1
            drift -= 1
        elif counts[idx] > 1:
            counts[idx] -= 1
            drift += 1
        i += 1
    return counts
