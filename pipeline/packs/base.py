"""Domain pack schema.

A pack is the *scaffolding* for one industry: the real org units, real document
taxonomy, real code systems, real workflow states, real role titles and real
identifier grammars that industry actually uses. The content poured into that
scaffolding is entirely invented.

The split matters. Realism in enterprise documents comes almost entirely from
structure — a reader who has seen a hundred Airworthiness Directives recognises
one by its shape long before reading a word of it. So the shape is real and
sourced; the company names, people, dates and numbers are fabricated from
reserved ranges (see pipeline/identifiers.py).

Nothing in a pack names a real company. Code systems, regulation numbers and
standard identifiers are cited because they are public facts, not client data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocType:
    """One document type in a domain's taxonomy."""

    key: str                       # stable slug, used in filenames and the graph
    name: str                      # what practitioners call it
    abbrev: str = ""               # what they actually say out loud
    sections: tuple[str, ...] = () # canonical section order
    id_grammar: str = "DOC"        # prefix for the document control number
    weight: float = 1.0            # relative frequency in the corpus
    authority: str = ""            # the standard or regulation that defines it
    system: str = ""               # system of record it originates from


@dataclass(frozen=True)
class CodeSystem:
    """A controlled vocabulary the domain's documents cite."""

    key: str
    name: str
    authority: str
    fmt: str                       # human-readable format description
    codes: tuple[tuple[str, str], ...] = ()   # (code, meaning)
    note: str = ""


@dataclass(frozen=True)
class Workflow:
    """A lifecycle state machine documents move through."""

    key: str
    name: str
    states: tuple[str, ...]
    terminal: tuple[str, ...] = ()


@dataclass
class Pack:
    """Everything needed to generate one tenant's corpus and graph."""

    slug: str
    tenant: str                    # invented company name
    industry: str
    tagline: str
    accent: str                    # hex, drives the tenant's graph palette

    units: tuple[str, ...] = ()            # organisational units
    roles: tuple[str, ...] = ()            # job titles that sign documents
    systems: tuple[str, ...] = ()          # invented systems of record
    doc_types: tuple[DocType, ...] = ()
    code_systems: tuple[CodeSystem, ...] = ()
    workflows: tuple[Workflow, ...] = ()

    # Domain vocabulary — the abbreviations an insider uses without expanding.
    lexicon: tuple[str, ...] = ()
    # Subject matter nouns used to build document titles and entity names.
    subjects: tuple[str, ...] = ()
    # Named facilities / sites / lines. Invented, but shaped like real ones.
    sites: tuple[str, ...] = ()
    # Entity → relationship → entity triples defining the domain ontology.
    ontology: tuple[tuple[str, str, str], ...] = ()
    # Seed questions the demo answers well, chosen to exercise the graph.
    questions: tuple[str, ...] = ()

    def doc_type(self, key: str) -> DocType:
        for d in self.doc_types:
            if d.key == key:
                return d
        raise KeyError(f"{self.slug}: no doc type {key!r}")

    def code_system(self, key: str) -> CodeSystem:
        for c in self.code_systems:
            if c.key == key:
                return c
        raise KeyError(f"{self.slug}: no code system {key!r}")


# ---------------------------------------------------------------------------
# Controlled-document scaffolding shared by every domain
# ---------------------------------------------------------------------------

# Every governed document in every one of these industries carries the same
# control apparatus. Encoding it once is what makes eleven very different
# corpora feel like they came from real document management systems.

CONTROL_HEADER_FIELDS = (
    "Document ID",
    "Title",
    "Revision",
    "Status",
    "Effective Date",
    "Next Review",
    "Document Owner",
    "Classification",
)

CLASSIFICATIONS = (
    ("Internal", 0.52),
    ("Confidential", 0.31),
    ("Restricted", 0.11),
    ("Public", 0.06),
)

DOC_STATES = Workflow(
    key="doc_control",
    name="Document control",
    states=("Draft", "In Review", "Approved", "Effective", "Superseded", "Obsolete"),
    terminal=("Superseded", "Obsolete"),
)

# Closing sections appended to controlled documents, in canonical order.
TRAILING_SECTIONS = (
    "Definitions and Acronyms",
    "References",
    "Revision History",
    "Approval",
)

RETENTION_RULES = (
    "Retain 7 years from supersession.",
    "Retain for the life of the asset plus 2 years.",
    "Retain 10 years per records schedule RS-04.",
    "Retain 3 years after contract expiry.",
    "Permanent record — do not destroy.",
)
