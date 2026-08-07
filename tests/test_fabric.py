"""Safety and integrity gate.

The corpus is synthetic, but "synthetic" is a claim that has to be enforced
rather than asserted. These tests fail the build if anything in the generated
output could be mistaken for real client data, or if the fabric's structural
guarantees are broken.

Run: pytest -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TENANTS = ROOT / "tenants"

SLUGS = [
    "q-airlines", "q-aerotech", "q-health", "q-assure-claims", "q-pharma",
    "q-devicelab", "q-bank", "q-assurance", "q-cruise", "q-retail", "q-quality",
]


def _require_build():
    if not (TENANTS / "registry.json").exists():
        pytest.skip("corpus not built — run `python -m pipeline.build_tenants`")


@pytest.fixture(scope="session")
def registry():
    _require_build()
    return json.loads((TENANTS / "registry.json").read_text())


@pytest.fixture(scope="session", params=SLUGS)
def tenant(request):
    _require_build()
    slug = request.param
    root = TENANTS / slug
    if not (root / "tenant.json").exists():
        pytest.skip(f"{slug} not built")
    return {
        "slug": slug,
        "manifest": json.loads((root / "tenant.json").read_text()),
        "graph": json.loads((root / "fabric" / "graph.json").read_text()),
        "index": json.loads((root / "fabric" / "index.json").read_text()),
        "documents": json.loads((root / "fabric" / "documents.json").read_text()),
        "health": json.loads((root / "fabric" / "health.json").read_text()),
        "semantic": json.loads((root / "fabric" / "semantic.json").read_text()),
        "docs_dir": root / "docs",
    }


def corpus_text(tenant) -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(tenant["docs_dir"].glob("*.md")))


# ---------------------------------------------------------------------------
# Client-data safety
# ---------------------------------------------------------------------------

# Real organisations this project must never name in generated output. The
# engagement context that motivated the build is exactly what must not leak.
FORBIDDEN_ORGS = [
    "nova biomedical", "novabiomedical", "southwest airlines", "southwest",
    "pwc", "pricewaterhouse", "qualizeal", "deloitte", "kpmg", "ernst & young",
    "accenture", "cognizant", "infosys", "wipro", "tcs",
    # Real carriers, banks, health systems and manufacturers whose names a
    # generator could plausibly drift into.
    "united airlines", "delta air", "american airlines", "lufthansa", "ryanair",
    "boeing", "airbus", "rolls-royce", "pratt & whitney",
    "kaiser permanente", "mayo clinic", "cleveland clinic", "hca healthcare",
    "unitedhealth", "anthem", "cigna", "aetna", "humana",
    "pfizer", "moderna", "novartis", "astrazeneca", "glaxosmithkline", "merck",
    "medtronic", "abbott", "siemens healthineers", "philips healthcare",
    "jpmorgan", "goldman sachs", "citibank", "wells fargo", "hsbc", "barclays",
    "carnival cruise", "royal caribbean", "norwegian cruise",
    "walmart", "target corporation", "tesco", "carrefour",
    "epic systems", "cerner", "meditech", "veeva", "salesforce",
]


def test_no_real_organisation_named(tenant):
    """No generated document may name a real organisation."""
    text = corpus_text(tenant).lower()
    found = [org for org in FORBIDDEN_ORGS if org in text]
    assert not found, f"{tenant['slug']} corpus names real organisations: {found}"


def test_no_real_orgs_in_manifest(tenant):
    blob = json.dumps(tenant["manifest"]).lower()
    found = [org for org in FORBIDDEN_ORGS if org in blob]
    assert not found, f"{tenant['slug']} manifest names real organisations: {found}"


def test_every_document_carries_synthetic_notice(tenant):
    """A reader who opens one file in isolation must still be told it is fake."""
    missing = [p.name for p in sorted(tenant["docs_dir"].glob("*.md"))
               if "Synthetic document" not in p.read_text(encoding="utf-8")]
    assert not missing, f"documents without a synthetic-data notice: {missing[:5]}"


# ---------------------------------------------------------------------------
# Identifier safety — reserved ranges only
# ---------------------------------------------------------------------------

def test_ip_addresses_are_reserved_for_documentation(tenant):
    """Any IPv4 literal must sit in an RFC 5737 documentation block."""
    text = corpus_text(tenant)
    ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    allowed = ("192.0.2.", "198.51.100.", "203.0.113.")
    bad = [ip for ip in ips if not ip.startswith(allowed)]
    assert not bad, f"non-reserved IP addresses in {tenant['slug']}: {set(bad)}"


def test_email_domains_are_rfc2606(tenant):
    text = corpus_text(tenant)
    emails = re.findall(r"[\w.\-]+@([\w.\-]+)", text)
    bad = [d for d in emails
           if not (d.endswith(("example.com", "example.net", "example.org",
                               ".test", ".invalid", ".example")))]
    assert not bad, f"non-reserved email domains in {tenant['slug']}: {set(bad)}"


def test_phone_numbers_use_fictitious_block(tenant):
    """Only 555-0100..555-0199 is guaranteed fictitious by NANPA."""
    text = corpus_text(tenant)
    phones = re.findall(r"\(555\)\s*(\d{3}-\d{4})", text)
    bad = [p for p in phones if not p.startswith("555-01")]
    assert not bad, f"phone numbers outside the fictitious block: {set(bad)}"


def test_npis_cannot_resolve(tenant):
    """Real NPIs begin with 1 or 2. Ours begin with 9, which NPPES never issued."""
    if tenant["slug"] not in ("q-health", "q-assure-claims"):
        pytest.skip("NPIs only appear in healthcare tenants")
    text = corpus_text(tenant)
    for npi in re.findall(r"\b([12]\d{9})\b", text):
        pytest.fail(f"identifier resembling a live NPI: {npi}")


def test_tail_numbers_are_unassigned(tenant):
    if tenant["slug"] not in ("q-airlines", "q-aerotech"):
        pytest.skip("tail numbers only appear in aviation tenants")
    text = corpus_text(tenant)
    tails = re.findall(r"\bN\d{1,5}[A-Z]{0,2}\b", text)
    bad = [t for t in tails if not re.fullmatch(r"N9\d{2}ZZ", t)]
    assert not bad, f"tail numbers outside the reserved pattern: {set(bad)}"


# ---------------------------------------------------------------------------
# Licensed content
# ---------------------------------------------------------------------------

def test_no_licensed_code_descriptors(tenant):
    """CPT and SNOMED CT descriptors are proprietary.

    We cite these systems by name and format, which is a public fact, but must
    never embed their code descriptions.
    """
    blob = json.dumps(tenant["manifest"]).lower()
    for cs in tenant["manifest"]["code_systems"]:
        name = cs["name"].lower()
        if "cpt" in name or "snomed" in name:
            assert not cs["codes"], (
                f"{tenant['slug']} ships descriptors for a licensed code "
                f"system: {cs['name']}"
            )
    assert "snomed ct concept" not in blob


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------

def test_graph_edges_resolve_to_nodes(tenant):
    ids = {n["id"] for n in tenant["graph"]["nodes"]}
    dangling = [(e["s"], e["rel"], e["t"]) for e in tenant["graph"]["edges"]
                if e["s"] not in ids or e["t"] not in ids]
    assert not dangling, f"edges referencing missing nodes: {dangling[:5]}"


def test_every_node_kind_has_a_legend_label(tenant):
    """An unlabelled kind renders as a raw slug in the legend."""
    kinds = tenant["graph"]["kinds"]
    used = {n["kind"] for n in tenant["graph"]["nodes"]}
    missing = used - set(kinds)
    assert not missing, f"{tenant['slug']} node kinds without a label: {missing}"


def test_kinds_do_not_leak_between_tenants(tenant):
    """Regression: ENTITY_KINDS was module-level and accumulated across builds,
    so the health system's legend listed aircraft and delay codes."""
    from pipeline.world import SPECS
    own = set(SPECS.get(tenant["slug"], {}).get("types", {}))
    structural = {"unit", "system", "authority", "site", "subject",
                  "doctype", "role", "code"}
    declared = set(tenant["graph"]["kinds"]) - structural
    assert declared <= own, (
        f"{tenant['slug']} declares foreign entity kinds: {declared - own}")


def test_passages_address_real_documents(tenant):
    doc_ids = {d["id"] for d in tenant["documents"]}
    orphan = {p["doc"] for p in tenant["index"]["passages"]} - doc_ids
    assert not orphan, f"passages pointing at missing documents: {orphan}"


def test_passage_ids_are_unique(tenant):
    ids = [p["id"] for p in tenant["index"]["passages"]]
    assert len(ids) == len(set(ids)), "duplicate passage addresses"


def test_metadata_sections_are_not_indexed(tenant):
    """Revision history and approval blocks are document apparatus.

    Indexing them lets a query be "answered" with a signature row, which is
    worse than no answer.
    """
    skip = {"references", "revision history", "approval",
            "definitions and acronyms", "entities in scope"}
    bad = [p["id"] for p in tenant["index"]["passages"]
           if p["section"].strip().lower() in skip]
    assert not bad, f"metadata sections were indexed: {bad[:5]}"


def test_bm25_postings_are_in_range(tenant):
    n = len(tenant["index"]["passages"])
    for term, post in list(tenant["index"]["bm25"]["postings"].items())[:400]:
        for idx, _w in post:
            assert 0 <= idx < n, f"posting for {term!r} out of range: {idx}"


def test_semantic_index_actually_compresses(tenant):
    """Retaining ~100% of variance means the projection is a lossless rotation
    that reproduces TF-IDF exactly and generalises nothing."""
    sem = tenant["semantic"]
    if not sem.get("enabled"):
        pytest.skip("semantic index disabled for this tenant")
    assert sem["variance"] < 0.97, (
        f"{tenant['slug']} LSA retains {sem['variance']:.0%} of variance — "
        "no compression, so no semantic generalisation")
    assert sem["components"] >= 16


def test_world_relationships_are_traversable(tenant):
    """The graph must contain typed domain edges, not only metadata edges.

    Without these the fabric is a facet index and offers nothing over RAG.
    """
    asserted = [e for e in tenant["graph"]["edges"] if e.get("asserted")]
    assert len(asserted) >= 20, (
        f"{tenant['slug']} has only {len(asserted)} asserted domain edges")


def test_multi_hop_chain_exists(tenant):
    """At least one two-hop chain over asserted edges must exist, or the
    graph cannot answer anything retrieval could not."""
    from collections import defaultdict
    adj = defaultdict(list)
    for e in tenant["graph"]["edges"]:
        if e.get("asserted"):
            adj[e["s"]].append(e["t"])
            adj[e["t"]].append(e["s"])
    for start, firsts in adj.items():
        for mid in firsts:
            for end in adj[mid]:
                if end != start:
                    return
    pytest.fail(f"{tenant['slug']} has no traversable two-hop chain")


def test_health_scores_are_bounded(tenant):
    h = tenant["health"]
    assert 0 <= h["overall"] <= 100
    for d in h["dimensions"]:
        assert 0 <= d["score"] <= 100, f"{d['key']} out of range"
        assert d["action"], f"{d['key']} has no remediation action"


def test_registry_totals_match_tenants(registry):
    total = sum(t["counts"]["documents"] for t in registry["tenants"])
    assert total == registry["totals"]["documents"]
    assert len(registry["tenants"]) == len(SLUGS)


def test_seed_questions_retrieve_something(tenant):
    """A suggested question that returns nothing is a broken demo."""
    postings = tenant["index"]["bm25"]["postings"]
    for q in tenant["manifest"]["questions"]:
        terms = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", q)]
        assert any(t in postings for t in terms), (
            f"{tenant['slug']} seed question has no indexed terms: {q!r}")
