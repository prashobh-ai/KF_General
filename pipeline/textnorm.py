"""Sentence-level text classification.

Real corpora are extracted from PDFs, and PDF extraction produces debris:
table rows flattened into prose, running headers repeated on every page,
equations reduced to symbol soup, boilerplate legal text. That debris is
indexed alongside genuine prose and it is what produces answers that read as
confident nonsense — a retrieved "sentence" that is actually three table cells
and a page number.

Measuring the split is therefore a first-class quality signal, not a
housekeeping detail. This module classifies every sentence at build time so
Readability can be reported honestly and debris can be kept out of answers
while remaining visible in the index.

The classifier is deliberately rule-based rather than learned. The failure
modes it detects are structural — digit density, delimiter runs, absent verbs,
casing patterns — and a rule states plainly why a sentence was rejected, which
matters when the number it produces is shown to a client.
"""

from __future__ import annotations

import re

# A sentence needs a finite verb to be prose. Checking for a closed class of
# common ones is cheap and catches the dominant debris case: a row of noun
# phrases separated by whitespace that reads as a sentence to a splitter.
VERB_HINTS = frozenset("""
is are was were be been being has have had do does did will would shall should
may might must can could apply applies applied require requires required
record records recorded ensure ensures ensured provide provides provided
perform performs performed verify verifies verified review reviews reviewed
report reports reported approve approves approved complete completes completed
issue issues issued maintain maintains maintained retain retains retained
escalate escalates escalated confirm confirms confirmed assess assesses
assessed determine determines determined include includes included define
defines defined govern governs governed carry carries carried remain remains
sit sits sat make makes made take takes taken give gives given hold holds held
""".split())

SECTION_HEAD_RE = re.compile(r"^\s*(\d+(\.\d+)*|[A-Z]\.)\s+[A-Z]")
RUNNING_HEAD_RE = re.compile(
    r"^\s*(page\s+\d+|confidential|internal use only|uncontrolled when printed"
    r"|rev(ision)?\s+[\d.]+|doc(ument)?\s+id|effective\s+date)\b", re.I)

# Page furniture appears mid-string once a header has been flattened into the
# text stream, so it cannot be anchored to the start of the line.
PAGE_FURNITURE_RE = re.compile(
    r"\bpage\s+\d+\s+of\s+\d+\b|\buncontrolled when printed\b"
    r"|\brev(ision)?\s+[\d.]+\s+effective\b", re.I)
# Whitespace around the operator is required, because without it an ISO date
# ("2025-04-02") reads as subtraction and every dated header is misfiled as an
# equation.
EQUATION_RE = re.compile(r"[=<>±×÷∑∫√≤≥]\s*[\d(]|\b\d+\s+[+\-*/]\s+\d+")
LEGAL_RE = re.compile(
    r"\b(shall not be liable|without warranty|all rights reserved|"
    r"copyright|hereinafter|pursuant to the foregoing|notwithstanding)\b", re.I)

CLASSES = ("prose", "table", "header", "equation", "legal")


def classify_sentence(s: str) -> str:
    """Return one of CLASSES for a single sentence."""
    t = s.strip()
    if not t:
        return "header"

    words = t.split()
    n = len(words)

    if LEGAL_RE.search(t):
        return "legal"
    # Page furniture is checked before equations: a running header carries a
    # date, and a date looks like arithmetic to any loose operator pattern.
    if PAGE_FURNITURE_RE.search(t):
        return "header"
    if EQUATION_RE.search(t):
        return "equation"

    # Running headers and control-block fragments: short, and matching the
    # stock phrases that repeat on every page of a controlled document.
    if RUNNING_HEAD_RE.match(t):
        return "header"
    if n <= 4:
        return "header"
    if SECTION_HEAD_RE.match(t) and n <= 9 and not t.rstrip().endswith("."):
        return "header"

    # Table debris: pipes and tab runs survive extraction, and a flattened row
    # is dense with digits and short tokens while carrying no verb.
    if "|" in t or "\t" in t:
        return "table"
    digits = sum(ch.isdigit() for ch in t)
    if n and digits / max(len(t), 1) > 0.22:
        return "table"
    if re.search(r"(\s{3,}\S+){3,}", s):
        return "table"

    lower = {w.strip(".,;:()").lower() for w in words}
    if not (lower & VERB_HINTS):
        # No finite verb. Long noun-phrase runs are almost always a flattened
        # row or a caption; short ones are headings.
        return "table" if n > 8 else "header"

    # Title Case Throughout With No Sentence Punctuation is a heading that a
    # splitter has handed us as a sentence.
    caps = sum(1 for w in words if w[:1].isupper())
    if n >= 4 and caps / n > 0.75 and not t.rstrip().endswith((".", "?", "!")):
        return "header"

    return "prose"


SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in SENT_SPLIT_RE.split(text) if s.strip()]


def passage_quality(text: str) -> dict:
    """Classify a passage's sentences and score its prose fraction."""
    sents = split_sentences(text)
    if not sents:
        return {"sentences": 0, "prose": 0, "quality": 0.0, "counts": {}}
    counts: dict[str, int] = {}
    prose = 0
    for s in sents:
        c = classify_sentence(s)
        counts[c] = counts.get(c, 0) + 1
        if c == "prose":
            prose += 1
    return {
        "sentences": len(sents),
        "prose": prose,
        "quality": round(prose / len(sents), 4),
        "counts": counts,
    }
