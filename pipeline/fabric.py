"""Build the fabric: entity graph, retrieval index, knowledge health.

The three artefacts here are what turn a folder of documents into something a
user can interrogate:

graph    Entities (units, systems, codes, authorities, subjects, sites) and the
         edges between them, derived from document structure rather than from
         an LLM. Deterministic extraction means the graph is explainable — the
         provenance of every edge is a specific document field.

index    A BM25 lexical index over paragraph-level passages, plus the passage
         addressing (document → section → paragraph) that citations need.
         Lexical, not embedding-based, because the demo must run entirely in
         the browser from static JSON with no inference service.

health   Metrics that answer "where is our knowledge weak?" — single-sourced
         topics, undated documents, orphans nothing references, and stale
         content past its review date.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .packs import Pack

STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have if in into is it its
of on or that the their there these this to was were which who will with not
must may shall any all each per than then when where while must be within
under over must can could would should about across after before between
during must-not no nor own same so too very s t
""".split())

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")

# Procedural English that appears in every controlled document ever written.
# These survive stop-word removal because they are content words, but they
# carry no domain signal — they are what made the concept cloud read as noise.
GENERIC_TERMS = frozenset("""
item items work working works completed complete completion performed perform
against operations operation requirement requirements revision only state
states permitted current newer cached effective once copy printed calls
independent verify check checks checked hand also person sequence starting
verification invalidates prerequisites confirm downstream records recorded
governs escalated cannot must shall should would could within under over
following follows applies applied applicable apply section sections document
documents documented documentation record recording ensure ensures ensuring
provide provided provides required require requires includes included include
using used use made make making taken take takes given give gives before
after during while where when what which whom whose there their they them
this that these those such same other another each every both either neither
more most less least many much few several various certain general specific
appropriate relevant necessary sufficient adequate reasonable
hours days weeks months annual annually periodic period periodically
assessed assess assessment beyond held holds holding approx approximately
reference references referenced referencing basis based stated states
window windows threshold thresholds interval intervals outcome outcomes
activity activities item-level named names name detail details
confirms confirm advances advance carries carry carried
""".split())


def tokenise(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)
            if t.lower() not in STOPWORDS and len(t) > 2]


# ---------------------------------------------------------------------------
# Passage extraction
# ---------------------------------------------------------------------------

def extract_passages(doc: dict) -> list[dict]:
    """Split a document into addressable passages.

    Addressing is document → section → paragraph. Citations must resolve to a
    specific paragraph, not a whole document, or "traced to its source" is a
    marketing claim rather than a verifiable one.
    """
    # Sections that are document apparatus rather than knowledge. Indexing
    # them means a query can be "answered" with a revision-history row or a
    # cross-reference list, which is worse than no answer at all.
    SKIP_SECTIONS = {
        "references", "revision history", "approval",
        "definitions and acronyms",
    }

    passages: list[dict] = []
    section = "Preamble"
    section_no = 0
    para_no = 0
    buf: list[str] = []

    def flush():
        nonlocal buf, para_no
        text = " ".join(buf).strip()
        buf = []
        if len(text.split()) < 12:
            return
        # Skip table rows and control blocks — they are metadata, not prose.
        if text.startswith("|") or text.startswith(">"):
            return
        if section.strip().lower() in SKIP_SECTIONS:
            return
        # Bullet runs are lists of cross-references, not prose.
        if text.startswith("- "):
            return
        para_no += 1
        passages.append({
            "id": f"{doc['id']}#{section_no}.{para_no}",
            "doc": doc["id"],
            "section": section,
            "section_no": section_no,
            "para": para_no,
            "text": text,
        })

    for line in doc["body"].splitlines():
        s = line.strip()
        if s.startswith("## "):
            flush()
            head = s[3:].strip()
            m = re.match(r"^(\d+)\.\s*(.+)$", head)
            if m:
                section_no, section = int(m.group(1)), m.group(2)
            else:
                section = head
            para_no = 0
        elif s.startswith("# "):
            flush()
        elif not s:
            flush()
        else:
            buf.append(s)
    flush()
    return passages


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class BM25:
    """Okapi BM25 over passages.

    Exported as plain JSON so the browser can score queries with no server.
    We ship the postings rather than raw text scoring at query time because a
    corpus of ~700 documents produces enough passages that naive scanning
    would be visibly slow on a phone.
    """

    def __init__(self, passages: list[dict], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.passages = passages
        self.docs = [tokenise(p["text"]) for p in passages]
        self.lengths = [len(d) for d in self.docs]
        self.avgdl = sum(self.lengths) / max(1, len(self.lengths))
        self.df: Counter = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.N = len(self.docs)

    def postings(self, max_terms: int = 6000) -> dict:
        """Inverted index with precomputed idf and term frequencies."""
        keep = {t for t, c in self.df.most_common(max_terms) if c >= 2}
        inv: dict[str, list[list[float]]] = defaultdict(list)
        for i, d in enumerate(self.docs):
            tf = Counter(t for t in d if t in keep)
            dl = self.lengths[i] or 1
            for term, f in tf.items():
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                inv[term].append([i, round(f * (self.k1 + 1) / denom, 4)])
        idf = {t: round(math.log(1 + (self.N - self.df[t] + 0.5) /
                                 (self.df[t] + 0.5)), 4)
               for t in keep}
        return {"idf": idf, "postings": inv, "n": self.N}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

ENTITY_KINDS = {
    "unit": "Organisational unit",
    "system": "System of record",
    "authority": "Standard or regulation",
    "site": "Site or facility",
    "subject": "Subject area",
    "doctype": "Document type",
    "role": "Role",
    "code": "Controlled code",
}


def build_graph(pack: Pack, docs: list[dict], world=None, world_rels=None) -> dict:
    # Per-call copy. ENTITY_KINDS is module-level, and mutating it leaked every
    # tenant's instance kinds into the next one built in the same process —
    # the health system's legend listed aircraft and delay codes.
    kinds = dict(ENTITY_KINDS)
    """Derive the entity graph from document structure.

    Every node and edge traces to a document field, so the UI can answer "why
    is this connected?" with a document list rather than a shrug.
    """
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def node(key: str, label: str, kind: str):
        nid = f"{kind}:{key}"
        n = nodes.setdefault(nid, {
            "id": nid, "label": label, "kind": kind, "docs": [], "degree": 0,
        })
        return n

    def link(a: str, b: str, rel: str, doc_id: str):
        if a == b:
            return
        k = (a, rel, b)
        e = edges.setdefault(k, {"s": a, "t": b, "rel": rel, "docs": []})
        if doc_id not in e["docs"]:
            e["docs"].append(doc_id)

    for d in docs:
        u = node(d["unit"], d["unit"], "unit")
        sy = node(d["system"], d["system"], "system")
        au = node(d["authority"], d["authority"], "authority")
        si = node(d["site"], d["site"], "site")
        su = node(d["subject"], d["subject"].title(), "subject")
        dt_ = node(d["type_key"], d["type"], "doctype")
        ro = node(d["owner_role"], d["owner_role"], "role")

        for n in (u, sy, au, si, su, dt_, ro):
            if d["id"] not in n["docs"]:
                n["docs"].append(d["id"])

        link(dt_["id"], u["id"], "OWNED_BY", d["id"])
        link(dt_["id"], au["id"], "GOVERNED_BY", d["id"])
        link(dt_["id"], sy["id"], "RECORDED_IN", d["id"])
        link(su["id"], u["id"], "MANAGED_BY", d["id"])
        link(su["id"], si["id"], "PERFORMED_AT", d["id"])
        link(ro["id"], u["id"], "ACCOUNTABLE_IN", d["id"])
        link(su["id"], dt_["id"], "DOCUMENTED_AS", d["id"])

    # Code-system nodes bind the domain's controlled vocabulary into the graph.
    for cs in pack.code_systems:
        for code, meaning in cs.codes[:14]:
            cn = node(f"{cs.key}:{code}", f"{code} · {meaning[:44]}", "code")
            an = node(cs.authority, cs.authority, "authority")
            link(cn["id"], an["id"], "PUBLISHED_BY", "")

    # ---------------------------------------------------------------
    # The domain world: concrete entity instances and the typed relationships
    # between them. This is what separates a fabric from a facet index — these
    # edges assert facts (this component is installed on that aircraft) that no
    # single document states in full, and that retrieval alone cannot recover.
    # ---------------------------------------------------------------
    if world:
        for kind, items in world.items():
            kinds.setdefault(kind, kind.replace("_", " ").title())
            for inst in items:
                n = nodes.setdefault(inst.id, {
                    "id": inst.id, "label": inst.label, "kind": kind,
                    "docs": [], "degree": 0, "ref": inst.ref,
                    "attrs": inst.attrs, "instance": True,
                })

        # Attach documents to the instances they cite, then record co-citation:
        # two entities named in the same controlled document are related by that
        # document, and that is a defensible, traceable assertion.
        for d in docs:
            cited = d.get("instances") or []
            for iid in cited:
                if iid in nodes and d["id"] not in nodes[iid]["docs"]:
                    nodes[iid]["docs"].append(d["id"])
            for a in range(len(cited)):
                for b in range(a + 1, len(cited)):
                    if cited[a] in nodes and cited[b] in nodes:
                        link(cited[a], cited[b], "CO_DOCUMENTED", d["id"])
            # Bind instances to the document's owning unit and doctype so a
            # question about an entity can reach the procedures governing it.
            for iid in cited:
                if iid in nodes:
                    link(iid, f"unit:{d['unit']}", "GOVERNED_BY_UNIT", d["id"])
                    link(iid, f"doctype:{d['type_key']}", "DESCRIBED_IN", d["id"])

    if world_rels:
        for r in world_rels:
            if r.src in nodes and r.dst in nodes:
                # Provenance: the documents that cite both ends assert this edge.
                shared = [d["id"] for d in docs
                          if r.src in (d.get("instances") or [])
                          and r.dst in (d.get("instances") or [])]
                key = (r.src, r.rel, r.dst)
                e = edges.setdefault(key, {"s": r.src, "t": r.dst,
                                           "rel": r.rel, "docs": []})
                e["docs"] = sorted(set(e["docs"]) | set(shared))
                e["asserted"] = True

    # Prune single-document co-citation.
    #
    # Two entities appearing together in one document is weak evidence — it may
    # only mean both were in scope that day. Appearing together in two or more
    # independent documents is a repeated pattern worth asserting. Without this
    # filter co-citation produced 18,413 edges per build, swamping the typed
    # domain relationships that carry the actual meaning and tripling payload.
    for key in [k for k, e in edges.items()
                if e["rel"] == "CO_DOCUMENTED" and len(e["docs"]) < 2]:
        del edges[key]

    for e in edges.values():
        if e["s"] in nodes:
            nodes[e["s"]]["degree"] += 1
        if e["t"] in nodes:
            nodes[e["t"]]["degree"] += 1

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "kinds": kinds,
        "ontology": [{"s": s, "r": r, "t": t} for s, r, t in pack.ontology],
    }


# ---------------------------------------------------------------------------
# Knowledge health
# ---------------------------------------------------------------------------

def build_health(docs: list[dict], graph: dict, today: str = "2026-05-31") -> dict:
    """Score the corpus on dimensions a knowledge owner can act on.

    Each metric names a specific remediation. A score with no action attached
    is decoration.
    """
    total = len(docs)
    by_subject: dict[str, list[str]] = defaultdict(list)
    for d in docs:
        by_subject[d["subject"]].append(d["id"])

    cited = set()
    for d in docs:
        cited.update(d["refs"])

    orphans = [d["id"] for d in docs if d["id"] not in cited]
    single = [s for s, ids in by_subject.items() if len(ids) == 1]
    stale = [d["id"] for d in docs if d["review"] < today]
    unowned = [d["id"] for d in docs if not d.get("owner")]

    # Coverage: how evenly the corpus spans its own organisational units.
    unit_counts = Counter(d["unit"] for d in docs)
    n_units = len(unit_counts)
    if n_units:
        ideal = total / n_units
        spread = 1 - min(1.0, sum(abs(c - ideal) for c in unit_counts.values())
                         / (2 * total))
    else:
        spread = 0.0

    def pct(n: int) -> float:
        return round(100 * (1 - n / total), 1) if total else 0.0

    dimensions = [
        {
            "key": "sourcing",
            "name": "Corroboration",
            "score": pct(len(single)),
            "detail": f"{len(single)} subjects appear in exactly one document.",
            "action": "Cross-document corroboration reduces single points of failure. "
                      "Add a second authoritative source for these subjects.",
            "items": single[:12],
        },
        {
            "key": "connectivity",
            "name": "Connectivity",
            "score": pct(len(orphans)),
            "detail": f"{len(orphans)} documents are referenced by nothing else.",
            "action": "Orphans are invisible in navigation. Link them from an "
                      "index or parent procedure.",
            "items": orphans[:12],
        },
        {
            "key": "currency",
            "name": "Currency",
            "score": pct(len(stale)),
            "detail": f"{len(stale)} documents are past their review date.",
            "action": "Route to the document owner for review or formal extension.",
            "items": stale[:12],
        },
        {
            "key": "ownership",
            "name": "Ownership",
            "score": pct(len(unowned)),
            "detail": f"{len(unowned)} documents have no named owner.",
            "action": "Unowned documents cannot be maintained. Assign an accountable role.",
            "items": unowned[:12],
        },
        {
            "key": "coverage",
            "name": "Unit coverage",
            "score": round(spread * 100, 1),
            "detail": f"Documentation spans {n_units} organisational units.",
            "action": "Units with thin coverage carry undocumented practice. "
                      "Prioritise the lightest for authoring.",
            "items": [u for u, _ in unit_counts.most_common()[-6:]],
        },
    ]

    overall = round(sum(d["score"] for d in dimensions) / len(dimensions), 1)
    return {
        "overall": overall,
        "dimensions": dimensions,
        "counts": {
            "documents": total,
            "entities": len(graph["nodes"]),
            "relationships": len(graph["edges"]),
            "orphans": len(orphans),
            "stale": len(stale),
        },
    }


def build_insights(pack: Pack, docs: list[dict], graph: dict,
                   passages: list[dict]) -> dict:
    """Aggregates for the insights view."""
    by_type = Counter(d["type"] for d in docs)
    by_unit = Counter(d["unit"] for d in docs)
    by_class = Counter(d["classification"] for d in docs)
    by_month: Counter = Counter()
    for d in docs:
        by_month[d["effective"][:7]] += 1

    # ---------------------------------------------------------------
    # Concept extraction.
    #
    # Ranking by raw document frequency produced a cloud of "item", "work",
    # "against", "only", "also" — the connective tissue of procedural English,
    # which every corpus shares and which therefore says nothing about THIS
    # one. A concept cloud whose largest word is "item" is worse than no cloud.
    #
    # Two changes fix it. First, a domain vocabulary is assembled from the
    # pack itself — lexicon, unit names, systems, authorities, subjects,
    # document types and code meanings — and terms in it are strongly
    # preferred, because those are the words a practitioner would recognise as
    # belonging to their field. Second, everything else must clear a
    # distinctiveness bar: present in enough passages to be a real theme, but
    # not so ubiquitous that it is boilerplate.
    # ---------------------------------------------------------------
    domain_vocab: set[str] = set()
    for phrase in (
        list(pack.lexicon) + list(pack.units) + list(pack.systems)
        + list(pack.subjects) + list(pack.sites) + list(pack.roles)
    ):
        domain_vocab.update(tokenise(phrase))
    for d in pack.doc_types:
        domain_vocab.update(tokenise(d.name))
        domain_vocab.update(tokenise(d.authority))
        for sec in d.sections:
            domain_vocab.update(tokenise(sec))
    for cs in pack.code_systems:
        domain_vocab.update(tokenise(cs.name))
        domain_vocab.update(tokenise(cs.authority))
        for code, meaning in cs.codes:
            domain_vocab.update(tokenise(meaning))
            if any(ch.isdigit() for ch in code) and len(code) > 2:
                domain_vocab.add(code.lower())
    for wf in pack.workflows:
        for st in wf.states:
            domain_vocab.update(tokenise(st))

    df: Counter = Counter()
    for p in passages:
        df.update(set(tokenise(p["text"])))
    n_pass = max(1, len(passages))

    scored: list[tuple[float, str, int]] = []
    for term, n in df.items():
        if len(term) < 4 or term in GENERIC_TERMS:
            continue
        ratio = n / n_pass
        # Boilerplate: in more than 55% of passages it is scaffolding, not a
        # theme. Noise: fewer than 3 passages is not a pattern.
        if ratio > 0.55 or n < 3:
            continue
        in_domain = term in domain_vocab
        if not in_domain and len(term) < 6:
            continue
        # Mid-frequency terms are the most characteristic, so weight peaks
        # away from both the ubiquitous and the rare.
        salience = n * (1.0 - ratio) * (2.6 if in_domain else 1.0)
        scored.append((salience, term, n))

    scored.sort(reverse=True)
    concept = [{"term": t, "n": n} for _sal, t, n in scored[:44]]

    kind_counts = Counter(n["kind"] for n in graph["nodes"])
    hubs = sorted(graph["nodes"], key=lambda n: -n["degree"])[:12]

    return {
        "by_type": [{"k": k, "n": n} for k, n in by_type.most_common()],
        "by_unit": [{"k": k, "n": n} for k, n in by_unit.most_common()],
        "by_class": [{"k": k, "n": n} for k, n in by_class.most_common()],
        "timeline": [{"k": k, "n": n} for k, n in sorted(by_month.items())],
        "concepts": concept,
        "entity_kinds": [{"k": k, "n": n} for k, n in kind_counts.most_common()],
        "hubs": [{"id": h["id"], "label": h["label"], "kind": h["kind"],
                  "degree": h["degree"]} for h in hubs],
        "words": sum(d["words"] for d in docs),
        "passages": len(passages),
    }
