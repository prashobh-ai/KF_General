"""The domain world — the layer that makes this a fabric rather than a folder.

Why this module exists
----------------------
A graph derived only from document metadata (owning unit, system of record,
governing authority) is not a knowledge graph. It is a facet index with edges
drawn on. Every question it can answer, a plain vector-RAG system answers just
as well by retrieving the same documents, because the "relationships" carry no
information that is not already in each document's header.

A knowledge fabric earns its name when entities are *shared between documents*
and the relationships between them are traversable. That is what enables the
class of question RAG structurally cannot answer:

    "Which aircraft are affected by open Airworthiness Directives on the wing
     structure, and which of those are scheduled into a base check this quarter?"

No single passage contains that answer. It requires joining an AD to the
components it affects, those components to the aircraft they are installed on,
and those aircraft to their maintenance schedule — three hops across four
documents. Retrieval alone returns four documents and leaves the join to the
reader. Traversal returns the answer.

So this module builds, per tenant, a small world of concrete entity instances —
actual tail numbers, batch numbers, claim numbers, vessels, facilities — with
typed relationships between them. Documents are then generated *about* those
instances, referencing them by identifier. Because many documents reference the
same instances, the graph acquires genuine cross-document structure, and every
edge remains traceable to the documents that assert it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .identifiers import IdFactory


@dataclass
class Instance:
    """One concrete thing in the domain: a tail number, a batch, a claim."""

    id: str                     # graph id, e.g. "aircraft:N912ZZ"
    kind: str                   # instance type
    label: str                  # display label
    ref: str                    # the identifier as it appears in prose
    attrs: dict = field(default_factory=dict)
    docs: list = field(default_factory=list)


@dataclass
class Relation:
    src: str
    rel: str
    dst: str
    docs: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-domain world specifications
#
# Each spec declares the instance types to mint and the relationship rules that
# wire them together. The rules are deliberately domain-accurate: an
# Airworthiness Directive applies to a component type, components are installed
# on aircraft, and work orders are raised against aircraft — that chain is how
# the real records actually link, which is why traversing it answers real
# questions.
# ---------------------------------------------------------------------------

SPECS: dict[str, dict] = {
    "q-airlines": {
        "types": {
            "aircraft": {"n": 34, "label": "Aircraft {ref}", "id": "tail"},
            "station": {"n": 24, "label": "Station {ref}", "id": "station"},
            "flight": {"n": 43, "label": "Flight {ref}", "id": "flight"},
            "melitem": {"n": 29, "label": "MEL item {ref}", "id": "mel"},
            "delaycode": {"n": 24, "label": "Delay code {ref}", "id": "delay"},
        },
        "links": [
            ("flight", "OPERATED_BY", "aircraft", 1),
            ("flight", "DEPARTS_FROM", "station", 1),
            ("flight", "ARRIVES_AT", "station", 1),
            ("flight", "DELAYED_BY", "delaycode", 1),
            ("aircraft", "CARRIES_OPEN_ITEM", "melitem", 2),
            ("melitem", "DEFERRED_AT", "station", 1),
        ],
    },
    "q-aerotech": {
        "types": {
            "aircraft": {"n": 29, "label": "Aircraft {ref}", "id": "tail"},
            "component": {"n": 43, "label": "Component {ref}", "id": "part"},
            "ad": {"n": 29, "label": "AD {ref}", "id": "ad"},
            "workorder": {"n": 38, "label": "Work order {ref}", "id": "wo"},
            "shop": {"n": 14, "label": "{ref}", "id": "shop"},
        },
        "links": [
            ("component", "INSTALLED_ON", "aircraft", 1),
            ("ad", "APPLIES_TO", "component", 2),
            ("workorder", "RAISED_AGAINST", "aircraft", 1),
            ("workorder", "EMBODIES", "ad", 1),
            ("component", "OVERHAULED_AT", "shop", 1),
        ],
    },
    "q-health": {
        "types": {
            "pathway": {"n": 24, "label": "Pathway {ref}", "id": "path"},
            "orderset": {"n": 34, "label": "Order set {ref}", "id": "os"},
            "condition": {"n": 29, "label": "{ref}", "id": "icd"},
            "unitward": {"n": 19, "label": "{ref}", "id": "ward"},
            "interface": {"n": 19, "label": "Interface {ref}", "id": "ifc"},
        },
        "links": [
            ("pathway", "TRIGGERS", "orderset", 2),
            ("pathway", "TREATS", "condition", 1),
            ("orderset", "USED_ON", "unitward", 2),
            ("interface", "TRANSMITS", "orderset", 1),
            ("condition", "DOCUMENTED_ON", "unitward", 1),
        ],
    },
    "q-assure-claims": {
        "types": {
            "policy": {"n": 29, "label": "Policy {ref}", "id": "mcp"},
            "carc": {"n": 26, "label": "CARC {ref}", "id": "carc"},
            "provider": {"n": 29, "label": "Provider {ref}", "id": "npi"},
            "claimbatch": {"n": 34, "label": "Claim batch {ref}", "id": "icn"},
            "edifile": {"n": 19, "label": "{ref}", "id": "x12"},
        },
        "links": [
            ("claimbatch", "SUBMITTED_BY", "provider", 1),
            ("claimbatch", "DENIED_WITH", "carc", 2),
            ("carc", "DRIVEN_BY", "policy", 1),
            ("claimbatch", "TRANSMITTED_IN", "edifile", 1),
            ("provider", "SUBJECT_TO", "policy", 2),
        ],
    },
    "q-pharma": {
        "types": {
            "product": {"n": 19, "label": "Product {ref}", "id": "prod"},
            "batch": {"n": 43, "label": "Batch {ref}", "id": "batch"},
            "deviation": {"n": 34, "label": "Deviation {ref}", "id": "dev"},
            "capa": {"n": 29, "label": "CAPA {ref}", "id": "capa"},
            "line": {"n": 14, "label": "{ref}", "id": "line"},
        },
        "links": [
            ("batch", "OF_PRODUCT", "product", 1),
            ("batch", "MANUFACTURED_ON", "line", 1),
            ("deviation", "AFFECTS", "batch", 2),
            ("deviation", "RESOLVED_BY", "capa", 1),
            ("capa", "APPLIES_TO", "line", 1),
        ],
    },
    "q-devicelab": {
        "types": {
            "device": {"n": 19, "label": "Device {ref}", "id": "dev"},
            "hazard": {"n": 34, "label": "Hazard {ref}", "id": "haz"},
            "control": {"n": 34, "label": "Risk control {ref}", "id": "rc"},
            "complaint": {"n": 38, "label": "Complaint {ref}", "id": "cmp"},
            "softitem": {"n": 24, "label": "Software item {ref}", "id": "sw"},
        },
        "links": [
            ("hazard", "ARISES_IN", "device", 1),
            ("control", "MITIGATES", "hazard", 2),
            ("control", "IMPLEMENTED_IN", "softitem", 1),
            ("complaint", "REPORTED_AGAINST", "device", 1),
            ("complaint", "LINKED_TO_HAZARD", "hazard", 1),
        ],
    },
    "q-bank": {
        "types": {
            "borrower": {"n": 29, "label": "Borrower {ref}", "id": "cif"},
            "facility": {"n": 38, "label": "Facility {ref}", "id": "loan"},
            "covenant": {"n": 29, "label": "Covenant {ref}", "id": "cov"},
            "alert": {"n": 34, "label": "AML alert {ref}", "id": "alert"},
            "model": {"n": 19, "label": "Model {ref}", "id": "model"},
        },
        "links": [
            ("facility", "EXTENDED_TO", "borrower", 1),
            ("facility", "CONSTRAINED_BY", "covenant", 2),
            ("alert", "RAISED_ON", "borrower", 1),
            ("model", "RATES", "facility", 2),
            ("alert", "SCORED_BY", "model", 1),
        ],
    },
    "q-assurance": {
        "types": {
            "engagement": {"n": 24, "label": "Engagement {ref}", "id": "eng"},
            "account": {"n": 29, "label": "{ref}", "id": "acct"},
            "risk": {"n": 34, "label": "RMM {ref}", "id": "rmm"},
            "control": {"n": 34, "label": "Control {ref}", "id": "ctrl"},
            "workpaper": {"n": 38, "label": "Workpaper {ref}", "id": "wp"},
        },
        "links": [
            ("risk", "IDENTIFIED_IN", "engagement", 1),
            ("risk", "AFFECTS_ACCOUNT", "account", 1),
            ("control", "ADDRESSES", "risk", 2),
            ("workpaper", "TESTS", "control", 1),
            ("workpaper", "FILED_UNDER", "engagement", 1),
        ],
    },
    "q-cruise": {
        "types": {
            "vessel": {"n": 22, "label": "{ref}", "id": "vessel"},
            "port": {"n": 29, "label": "Port {ref}", "id": "port"},
            "deficiency": {"n": 29, "label": "Deficiency {ref}", "id": "psc"},
            "equipment": {"n": 34, "label": "Equipment {ref}", "id": "equip"},
            "voyage": {"n": 34, "label": "Voyage {ref}", "id": "voy"},
        },
        "links": [
            ("voyage", "SAILED_BY", "vessel", 1),
            ("voyage", "CALLS_AT", "port", 2),
            ("deficiency", "RAISED_ON", "vessel", 1),
            ("deficiency", "RAISED_AT", "port", 1),
            ("deficiency", "CONCERNS", "equipment", 1),
            ("equipment", "FITTED_TO", "vessel", 1),
        ],
    },
    "q-retail": {
        "types": {
            "vendor": {"n": 29, "label": "Vendor {ref}", "id": "gln"},
            "item": {"n": 38, "label": "Item {ref}", "id": "gtin"},
            "po": {"n": 38, "label": "PO {ref}", "id": "po"},
            "dc": {"n": 14, "label": "{ref}", "id": "dc"},
            "chargeback": {"n": 29, "label": "Chargeback {ref}", "id": "cb"},
        },
        "links": [
            ("po", "PLACED_WITH", "vendor", 1),
            ("po", "CONTAINS", "item", 2),
            ("po", "RECEIVED_AT", "dc", 1),
            ("chargeback", "ISSUED_TO", "vendor", 1),
            ("chargeback", "ARISES_FROM", "po", 1),
        ],
    },
    "q-quality": {
        "types": {
            "release": {"n": 24, "label": "Release {ref}", "id": "rel"},
            "requirement": {"n": 38, "label": "Requirement {ref}", "id": "req"},
            "testcase": {"n": 43, "label": "Test case {ref}", "id": "tc"},
            "defect": {"n": 38, "label": "Defect {ref}", "id": "def"},
            "environment": {"n": 14, "label": "{ref}", "id": "env"},
        },
        "links": [
            ("testcase", "COVERS", "requirement", 1),
            ("testcase", "EXECUTED_IN", "environment", 1),
            ("defect", "RAISED_BY", "testcase", 1),
            ("defect", "BLOCKS", "release", 1),
            ("requirement", "SCHEDULED_FOR", "release", 1),
        ],
    },
}


# ---------------------------------------------------------------------------
# Reference minting
# ---------------------------------------------------------------------------

def _mint(kind: str, ids: IdFactory, rng: random.Random, pack, i: int) -> tuple[str, dict]:
    """Produce a domain-correct identifier and attributes for one instance."""
    y = rng.choice((2024, 2025, 2026))
    if kind == "tail":
        return ids.tail_number(), {}
    if kind == "station":
        return f"ZZ{ids.letters(1)}", {"role": rng.choice(("hub", "spoke", "line station"))}
    if kind == "flight":
        return f"NM{rng.randint(100, 998)}", {}
    if kind == "mel":
        ata = rng.choice([c for c, _ in pack.code_system("ata").codes])
        return f"{ata}-{rng.randint(10,49)}", {"ata": ata,
                                               "category": rng.choice("ABCD")}
    if kind == "delay":
        c, m = rng.choice(pack.code_system("delay").codes)
        return c, {"meaning": m}
    if kind == "part":
        return f"P{ids.digits(6)}-{ids.letters(2)}", {"serial": f"SN{ids.digits(5)}"}
    if kind == "ad":
        return f"{y}-{rng.randint(1,26):02d}-{rng.randint(1,20):02d}", {}
    if kind == "wo":
        return f"WO-{y}-{ids.digits(4)}", {}
    if kind == "shop":
        return rng.choice(pack.sites), {}
    if kind == "path":
        return f"CP-{ids.digits(3)}", {}
    if kind == "os":
        return f"OS-{ids.digits(3)}", {}
    if kind == "icd":
        c, m = rng.choice(pack.code_system("icd10").codes)
        return c, {"meaning": m}
    if kind == "ward":
        return rng.choice(pack.sites), {}
    if kind == "ifc":
        return f"IF-{ids.digits(3)}", {}
    if kind == "mcp":
        return f"MCP-{ids.digits(4)}", {}
    if kind == "carc":
        c, m = rng.choice(pack.code_system("carc").codes)
        return c, {"meaning": m}
    if kind == "npi":
        return ids.npi(), {}
    if kind == "icn":
        return f"ICN{ids.digits(11)}", {}
    if kind == "x12":
        c, m = rng.choice(pack.code_system("x12").codes)
        return f"{c} file", {"meaning": m}
    if kind == "prod":
        return f"VT-{ids.digits(3)}", {}
    if kind == "batch":
        return ids.batch(), {}
    if kind == "dev":
        return f"DEV-{y}-{ids.digits(4)}", {}
    if kind == "capa":
        return f"CAPA-{y}-{ids.digits(4)}", {}
    if kind == "line":
        return rng.choice(pack.sites), {}
    if kind == "haz":
        return f"HAZ-{ids.digits(3)}", {}
    if kind == "rc":
        return f"RC-{ids.digits(3)}", {}
    if kind == "cmp":
        return f"CMP-{y}-{ids.digits(4)}", {}
    if kind == "sw":
        return f"SW-{ids.digits(3)}", {"class": rng.choice("ABC")}
    if kind == "cif":
        return f"CIF{ids.digits(7)}", {}
    if kind == "loan":
        return f"FAC-{ids.digits(6)}", {}
    if kind == "cov":
        return f"COV-{ids.digits(3)}", {"test": rng.choice(("DSCR", "LTV", "leverage"))}
    if kind == "alert":
        return f"ALT-{y}-{ids.digits(4)}", {}
    if kind == "model":
        return f"MOD-{ids.digits(3)}", {}
    if kind == "eng":
        return f"ENG-{y}-{ids.digits(3)}", {}
    if kind == "acct":
        return rng.choice(("Revenue", "Trade receivables", "Inventory",
                           "Goodwill", "Accrued liabilities", "Leases",
                           "Share-based payment", "Deferred tax")), {}
    if kind == "rmm":
        return f"RMM-{ids.digits(3)}", {}
    if kind == "ctrl":
        return f"CTL-{ids.digits(3)}", {}
    if kind == "wp":
        return f"{rng.choice('ABCDEFG')}-{rng.randint(1,9)}", {}
    if kind == "vessel":
        return rng.choice([s for s in pack.sites if s.startswith("MV")] or ["MV Meridian Aurora"]), {"imo": ids.imo()}
    if kind == "port":
        return f"ZZ{ids.letters(3)}", {}
    if kind == "psc":
        c, m = rng.choice(pack.code_system("pscdef").codes)
        return c, {"meaning": m}
    if kind == "equip":
        return f"EQ-{ids.digits(4)}", {}
    if kind == "voy":
        return f"VOY-{y}-{ids.digits(3)}", {}
    if kind == "gln":
        return ids.gln(), {}
    if kind == "gtin":
        return ids.gtin13(), {}
    if kind == "po":
        return f"PO-{y}-{ids.digits(5)}", {}
    if kind == "dc":
        return rng.choice(pack.sites), {}
    if kind == "cb":
        return f"CB-{y}-{ids.digits(4)}", {}
    if kind == "rel":
        return f"R{rng.randint(1,9)}.{rng.randint(0,9)}", {}
    if kind == "req":
        return f"REQ-{ids.digits(3)}", {}
    if kind == "tc":
        return f"TC-{ids.digits(4)}", {}
    if kind == "def":
        return f"DEF-{ids.digits(4)}", {}
    if kind == "env":
        return rng.choice(pack.sites), {}
    return f"{kind.upper()}-{ids.digits(4)}", {}


def build_world(pack, seed: int) -> tuple[dict[str, list[Instance]], list[Relation]]:
    """Mint the tenant's entity instances and wire them together."""
    spec = SPECS.get(pack.slug)
    if not spec:
        return {}, []

    rng = random.Random(seed ^ 0x5EED)
    ids = IdFactory(seed ^ 0x5EED)

    by_kind: dict[str, list[Instance]] = {}
    seen_refs: set[str] = set()

    for kind, cfg in spec["types"].items():
        items: list[Instance] = []
        for i in range(cfg["n"]):
            for _ in range(8):
                ref, attrs = _mint(cfg["id"], ids, rng, pack, i)
                if ref not in seen_refs:
                    break
            seen_refs.add(ref)
            items.append(Instance(
                id=f"{kind}:{ref}",
                kind=kind,
                label=cfg["label"].format(ref=ref),
                ref=ref,
                attrs=attrs,
            ))
        by_kind[kind] = items

    relations: list[Relation] = []
    seen_edges: set[tuple] = set()
    for src_kind, rel, dst_kind, fanout in spec["links"]:
        srcs = by_kind.get(src_kind, [])
        dsts = by_kind.get(dst_kind, [])
        if not srcs or not dsts:
            continue
        for s in srcs:
            for d in rng.sample(dsts, k=min(fanout, len(dsts))):
                if s.id == d.id:
                    continue
                key = (s.id, rel, d.id)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                relations.append(Relation(src=s.id, rel=rel, dst=d.id))

    return by_kind, relations
