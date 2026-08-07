"""The eleven domain packs, in display order."""

from .aviation import AEROTECH, AIRLINES
from .base import (
    CLASSIFICATIONS,
    CONTROL_HEADER_FIELDS,
    DOC_STATES,
    RETENTION_RULES,
    TRAILING_SECTIONS,
    CodeSystem,
    DocType,
    Pack,
    Workflow,
)
from .clinical import CLAIMS, DEVICELAB, HEALTH, PHARMA
from .enterprise import ASSURANCE, BANK, CRUISE, QUALITY, RETAIL

PACKS: tuple[Pack, ...] = (
    AIRLINES,
    AEROTECH,
    HEALTH,
    CLAIMS,
    PHARMA,
    DEVICELAB,
    BANK,
    ASSURANCE,
    CRUISE,
    RETAIL,
    QUALITY,
)

BY_SLUG = {p.slug: p for p in PACKS}


def get(slug: str) -> Pack:
    try:
        return BY_SLUG[slug]
    except KeyError:
        raise SystemExit(
            f"unknown tenant {slug!r}. Known: {', '.join(sorted(BY_SLUG))}"
        ) from None


__all__ = [
    "PACKS", "BY_SLUG", "get", "Pack", "DocType", "CodeSystem", "Workflow",
    "CONTROL_HEADER_FIELDS", "CLASSIFICATIONS", "DOC_STATES",
    "TRAILING_SECTIONS", "RETENTION_RULES",
]
