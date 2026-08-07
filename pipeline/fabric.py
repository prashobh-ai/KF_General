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

import datetime as dt
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

def link_passages_to_entities(passages: list[dict], graph: dict) -> dict:
    """Record which entities are actually MENTIONED in each passage.

    This is the difference between a graph that reacts to a question and one
    that lights the same hubs every time.

    The previous approach activated entities from the *metadata* of retrieved
    documents — owning unit, system of record, governing authority. Those are
    shared by nearly every document in a tenant, so every question activated
    the same handful of hubs and the graph became decoration: colourful,
    animated, and carrying no information about what was asked.

    Mentions are specific. A question about de-icing lights de-icing, the
    stations that perform it and the delay codes it produces; a question about
    crew legality lights something else entirely. That is what makes the
    visualisation evidence rather than ornament.

    Matching is literal and case-insensitive against entity labels and, for
    concrete instances, their identifiers. Literal matching under-recalls —
    it misses paraphrase — but it never asserts a mention that is not in the
    text, and a highlight the user cannot verify by reading the passage is
    worse than a missing one.
    """
    # Build a matcher per entity. Short labels are skipped: a two-character
    # code matches inside unrelated words and would light constantly.
    matchers: list[tuple[str, str]] = []
    for n in graph["nodes"]:
        label = (n.get("label") or "").strip()
        ref = str(n.get("ref") or "").strip()
        needle = None
        if ref and len(ref) >= 4:
            needle = ref.lower()
        elif len(label) >= 5:
            # Instance labels carry a type prefix ("Aircraft N912ZZ"); the
            # distinctive part is what appears in prose.
            needle = label.split("\u00b7")[0].strip().lower()
        if needle and len(needle) >= 4:
            matchers.append((n["id"], needle))

    mention_counts: Counter = Counter()
    for p in passages:
        text = p["text"].lower()
        hits = [eid for eid, needle in matchers if needle in text]
        # A passage that "mentions" thirty entities is matching noise, not
        # content; keep the most specific by needle length.
        if len(hits) > 12:
            order = {eid: len(nd) for eid, nd in matchers}
            hits.sort(key=lambda e: -order.get(e, 0))
            hits = hits[:12]
        p["ents"] = hits
        mention_counts.update(hits)

    # Boilerplate suppression.
    #
    # A system of record named in the control header of every document is
    # mentioned in a third of all passages. It is a true mention and a useless
    # one: it activates on every question, and because it is a high-degree hub
    # it drags hundreds of edges into the highlight, burying the handful of
    # entities that actually answer the question.
    #
    # This is the same problem as a stop word, and the same fix: an entity
    # present in more than a third of passages carries no discriminating
    # signal, so it is excluded from activation while remaining in the graph.
    ceiling = max(6, int(len(passages) * 0.33))
    boilerplate = {e for e, c in mention_counts.items() if c > ceiling}
    if boilerplate:
        for p in passages:
            if p.get("ents"):
                p["ents"] = [e for e in p["ents"] if e not in boilerplate]
        mention_counts = Counter()
        for p in passages:
            mention_counts.update(p.get("ents", []))

    for n in graph["nodes"]:
        n["mentions"] = mention_counts.get(n["id"], 0)
        n["boilerplate"] = n["id"] in boilerplate

    return {
        "boilerplateSuppressed": len(boilerplate),
        "mentionCeiling": ceiling,
        "passagesLinked": sum(1 for p in passages if p.get("ents")),
        "meanEntitiesPerPassage": round(
            sum(len(p.get("ents", [])) for p in passages) / max(1, len(passages)), 2),
        "entitiesMentioned": len([n for n in graph["nodes"] if n["mentions"]]),
    }


def build_health(docs: list[dict], graph: dict, passages: list[dict],
                 today: str = "2026-05-31") -> dict:
    """Score the corpus on five measures a knowledge owner can act on.

    Every metric states its formula and its raw inputs, because a score without
    a derivation is a number someone has to take on trust — and the first
    question a technical reviewer asks is "how did you get that?".

    Bands are deliberately demanding. A corpus that scores 90 on everything has
    told you nothing about where to spend effort next.
    """
    from .textnorm import passage_quality

    total = len(docs)
    if not total:
        return {"overall": 0, "metrics": [], "risks": [], "counts": {}}

    today_d = dt.date.fromisoformat(today)

    # ---- shared inputs ---------------------------------------------------
    by_doc_passages: Counter = Counter(p["doc"] for p in passages)
    entity_docs: dict[str, set] = defaultdict(set)
    for n in graph["nodes"]:
        for d in n.get("docs", []):
            entity_docs[n["id"]].add(d)
    entities = {k: v for k, v in entity_docs.items() if v}

    quality = [passage_quality(p["text"]) for p in passages]
    sentences = sum(q["sentences"] for q in quality)
    prose = sum(q["prose"] for q in quality)
    debris_counts: Counter = Counter()
    for q in quality:
        for k, v in q["counts"].items():
            if k != "prose":
                debris_counts[k] += v

    dated = [d for d in docs if d.get("effective")]
    authoritative = [d for d in docs if d.get("authoritative_date")]
    undated = [d for d in docs if not d.get("effective")]

    def band(v: float) -> str:
        return "strong" if v >= 75 else "fair" if v >= 50 else "weak"

    metrics = []

    # ---- 1. Depth --------------------------------------------------------
    # A mean saturates: one richly-documented procedure hides ten stubs, and
    # every tenant then scores 100. What a knowledge owner needs to know is how
    # much of the estate is too thin to answer from, so we score the share of
    # documents that clear the threshold rather than the average across them.
    TARGET_PASSAGES = 8
    mean_pass = sum(by_doc_passages.values()) / total
    deep = [d for d in docs if by_doc_passages.get(d["id"], 0) >= TARGET_PASSAGES]
    v = len(deep) / total * 100
    metrics.append({
        "key": "depth", "label": "Depth", "value": round(v, 1), "band": band(v),
        "what": "How much usable material each document contributes.",
        "risk": "Thin documents give the system only one place to look, so "
                "answers get shallower.",
        "formula": "documentsWith(>=8 passages) / documents x 100",
        "inputs": {
            "documents": total,
            "passages": len(passages),
            "meanPassagesPerDocument": round(mean_pass, 1),
            "documentsAtOrAboveTarget": len(deep),
            "target": TARGET_PASSAGES,
            "thinDocuments": total - len(deep),
        },
        "note": "How much retrievable material exists per document.",
    })

    # ---- 2. Connectedness -------------------------------------------------
    # Organisational scaffolding — units, systems, authorities, roles — is
    # attached to nearly every document by construction, so counting it makes
    # connectedness read 100 on any corpus whatsoever. The question is whether
    # SUBJECT MATTER recurs across documents, so the measure runs over topical
    # and instance entities only.
    STRUCTURAL = {"unit", "system", "authority", "role", "site", "doctype"}
    kind_of = {n["id"]: n["kind"] for n in graph["nodes"]}
    topical = {k: v_ for k, v_ in entities.items()
               if kind_of.get(k) not in STRUCTURAL}
    cross = [k for k, v_ in topical.items() if len(v_) >= 2]
    raw = len(cross) / max(1, len(topical))
    # Reported as the raw share rather than normalised against a "healthy"
    # threshold. Nova divides by 0.35, which is calibrated to a corpus of PDFs
    # where cross-document recurrence is genuinely rare; on this corpus that
    # normalisation pins every tenant at 100 and the metric stops saying
    # anything. The unnormalised share is also directly readable: 68 means 68%
    # of concepts appear in more than one document.
    v = raw * 100
    metrics.append({
        "key": "connectedness", "label": "Connectedness", "value": round(v, 1),
        "band": band(v),
        "what": "How often the same topic appears across different documents.",
        "risk": "Disconnected documents mean questions spanning two of them "
                "cannot be answered at all.",
        "formula": "topicalEntitiesIn(>=2 documents) / topicalEntities x 100",
        "inputs": {
            "topicalEntities": len(topical),
            "crossDocumentEntities": len(cross),
            "structuralEntitiesExcluded": len(entities) - len(topical),
            "rawRatio": f"{raw * 100:.1f}%",
            "referenceThreshold": "35% is healthy for a PDF-derived estate",
        },
        "note": "Share of concepts that appear in more than one document — the "
                "actual measure of whether this is a fabric or a pile of files.",
    })

    # ---- 3. Traceability --------------------------------------------------
    v = (len(dated) + len(authoritative)) / (2 * total) * 100
    by_method = Counter(d.get("date_source", "unknown") for d in docs)
    metrics.append({
        "key": "traceability", "label": "Traceability", "value": round(v, 1),
        "band": band(v),
        "what": "Whether we can tell when each document is from, and how we know.",
        "risk": "Without dates you cannot tell a current revision from a "
                "withdrawn one.",
        "formula": "(documentsWithAnyDate + documentsWithAuthoritativeDate) / "
                   "(2 x documents) x 100",
        "inputs": {
            "documents": total,
            "withDate": len(dated),
            "authoritative": len(authoritative),
            "byMethod": dict(by_method),
        },
        "note": "An authoritative source — an approved revision stamp — counts "
                "double against a system timestamp, because only one of them "
                "survives a challenge.",
    })

    # ---- 4. Readability ---------------------------------------------------
    v = (prose / sentences * 100) if sentences else 0.0
    metrics.append({
        "key": "readability", "label": "Readability", "value": round(v, 1),
        "band": band(v),
        "what": "How much of the text is clean prose rather than table debris.",
        "risk": "Debris produces answers that look confident and read as "
                "nonsense.",
        "formula": "mean(perPassageProseQuality) x 100, from build-time "
                   "sentence classification",
        "inputs": {
            "passagesMeasured": len(passages),
            "sentences": sentences,
            "proseSentences": prose,
            "prosePercent": f"{v:.1f}%",
        },
        "note": "How much of the indexed text is usable prose rather than table "
                "fragments, running headers and equations.",
    })

    # ---- 5. Currency ------------------------------------------------------
    FRESH, STALE = 730, 2190
    ages = sorted((today_d - dt.date.fromisoformat(d["effective"])).days
                  for d in dated)
    if ages:
        mid = len(ages) // 2
        median_age = ages[mid] if len(ages) % 2 else (ages[mid - 1] + ages[mid]) / 2
        v = max(0.0, min(1.0, 1 - (median_age - FRESH) / (STALE - FRESH))) * 100
    else:
        median_age, v = 0, 0.0
    metrics.append({
        "key": "currency", "label": "Currency", "value": round(v, 1),
        "band": band(v),
        "what": "How recent the maintained documents are.",
        "risk": "Out-of-date documentation is a compliance exposure, not just a "
                "quality one.",
        "formula": "1 - (medianAgeDays - 730) / (2190 - 730), clamped to [0,1]",
        "inputs": {
            "datedDocuments": len(dated),
            "undatedDocuments": len(undated),
            "medianAgeDays": int(median_age),
            "medianAgeYears": round(median_age / 365.25, 1),
            "freshWindowDays": FRESH,
            "staleAtDays": STALE,
        },
        "note": "Median age of documents we can date. Undated documents are "
                "excluded rather than assumed old — assuming would inflate the "
                "problem and hide the real one, which is that they are undated.",
    })

    # ---- risk register ----------------------------------------------------
    debris = sum(debris_counts.values())
    singletons = [k for k, v_ in topical.items() if len(v_) <= 1]
    linked_docs = set()
    for v_ in entities.values():
        if len(v_) >= 2:
            linked_docs |= v_
    isolated = [d["id"] for d in docs if d["id"] not in linked_docs]

    risks = [
        {
            "label": "extraction debris",
            "value": f"{debris / max(sentences, 1) * 100:.0f}%",
            "detail": f"{debris} of {sentences} sentences classified as table, "
                      f"header, equation or legal text",
            "how": "build-time sentence classifier (pipeline/textnorm.py)",
            "why": "These are indexed but excluded from answers. A high share "
                   "means the source documents are layout-heavy and retrieval "
                   "has less to work with.",
            "breakdown": dict(debris_counts),
        },
        {
            "label": "singleton concepts",
            "value": len(singletons),
            "detail": f"{len(singletons)} of {len(entities)} entities appear in "
                      f"exactly one document",
            "how": "count(entity.documents <= 1)",
            "why": "A concept described in one place disappears if that "
                   "document is revised or withdrawn.",
            "items": [graph_label(graph, k) for k in singletons[:10]],
        },
        {
            "label": "isolated documents",
            "value": len(isolated),
            "detail": f"{len(isolated)} of {total} documents share no entity "
                      f"with any other document",
            "how": "documents absent from every multi-document entity",
            "why": "Nothing links these into the fabric. They can only ever "
                   "answer questions that name them directly.",
            "items": isolated[:10],
        },
        {
            "label": "undated documents",
            "value": len(undated),
            "detail": f"{len(undated)} of {total} documents have no recoverable date",
            "how": "date resolution failed across revision stamp, system "
                   "timestamp and body text",
            "why": "Supersession cannot be checked. An answer may be quoting a "
                   "withdrawn revision with no way to tell.",
            "items": [d["id"] for d in undated[:10]],
        },
    ]

    overall = round(sum(m["value"] for m in metrics) / len(metrics), 1)
    strongest = max(metrics, key=lambda m: m["value"])
    weakest = min(metrics, key=lambda m: m["value"])

    return {
        "overall": overall,
        "metrics": metrics,
        "risks": risks,
        "strongest": f"Strongest: {strongest['label'].lower()} — "
                     f"{strongest['what'][0].lower()}{strongest['what'][1:]}",
        "weakest": f"Needs attention: {weakest['label'].lower()}. {weakest['risk']}",
        "counts": {
            "documents": total,
            "passages": len(passages),
            "entities": len(entities),
            "relationships": len(graph["edges"]),
            "isolated": len(isolated),
            "undated": len(undated),
        },
    }


def graph_label(graph: dict, node_id: str) -> str:
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n["label"]
    return node_id


def build_insights(pack: Pack, docs: list[dict], graph: dict,
                   passages: list[dict]) -> dict:
    """Aggregates for the insights view."""
    by_type = Counter(d["type"] for d in docs)
    by_unit = Counter(d["unit"] for d in docs)
    by_class = Counter(d["classification"] for d in docs)
    by_month: Counter = Counter()
    for d in docs:
        # Undated documents are a real category now, not a defect. They are
        # excluded from the timeline rather than assumed to be from any date.
        if d.get("effective"):
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
