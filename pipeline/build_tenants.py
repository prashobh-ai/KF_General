"""Generate every tenant's corpus and fabric artefacts.

    python -m pipeline.build_tenants [--only q-airlines] [--docs 60]

Outputs, per tenant, under tenants/<slug>/:

    docs/*.md            the synthetic corpus
    fabric/graph.json    entity graph
    fabric/index.json    BM25 postings and passage addressing
    fabric/health.json   knowledge health scoring
    fabric/insights.json aggregates for the insights view
    tenant.json          manifest consumed by the site builder

Everything under tenants/ is generated and git-ignored. The source of truth is
the domain packs in pipeline/packs/.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from . import packs as packlib
from .docgen import DocumentBuilder
from .fabric import (BM25, build_graph, build_health, build_insights,
                     extract_passages, link_passages_to_entities)
from .clusters import build_dendrogram
from .semantic import build_semantic_index

ROOT = Path(__file__).resolve().parent.parent
TENANTS = ROOT / "tenants"

# Seasonal peak per domain — the month each industry's document volume crests.
# Airlines peak with summer flying, retail with Q4 trade, health with winter
# admissions. Flat month distributions are one of the clearest tells that a
# corpus was generated rather than accumulated.
PEAK_MONTH = {
    "q-airlines": 7, "q-aerotech": 2, "q-health": 1, "q-assure-claims": 1,
    "q-pharma": 9, "q-devicelab": 10, "q-bank": 3, "q-assurance": 2,
    "q-cruise": 6, "q-retail": 11, "q-quality": 9,
}

DOC_TARGET = {
    "q-airlines": 64, "q-aerotech": 66, "q-health": 60, "q-assure-claims": 58,
    "q-pharma": 62, "q-devicelab": 58, "q-bank": 60, "q-assurance": 56,
    "q-cruise": 60, "q-retail": 58, "q-quality": 58,
}


def seed_for(slug: str) -> int:
    """Stable per-tenant seed so rebuilds are byte-reproducible."""
    return abs(hash(slug)) % (2**31) if False else sum(
        (i + 1) * ord(c) for i, c in enumerate(slug)
    )


def write_json(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text)


def build_tenant(pack, docs_target: int) -> dict:
    root = TENANTS / pack.slug
    docs_dir = root / "docs"
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    builder = DocumentBuilder(pack, seed_for(pack.slug),
                              peak_month=PEAK_MONTH.get(pack.slug, 6))
    docs = builder.build_corpus(docs_target)

    for d in docs:
        (docs_dir / d["filename"]).write_text(d["body"], encoding="utf-8")

    passages = [p for d in docs for p in extract_passages(d)]
    graph = build_graph(pack, docs, builder.world, builder.world_rels)
    mentions = link_passages_to_entities(passages, graph)
    health = build_health(docs, graph, passages)
    insights = build_insights(pack, docs, graph, passages)
    bm25 = BM25(passages)

    fabric = root / "fabric"
    meta = [{k: v for k, v in d.items() if k != "body"} for d in docs]

    write_json(fabric / "graph.json", graph)
    write_json(fabric / "health.json", health)
    write_json(fabric / "insights.json", insights)
    # Dense semantic index. BM25 alone fails on vocabulary mismatch, which is
    # the dominant failure mode when a user asks in their own words rather than
    # the corpus's.
    sem = build_semantic_index([p["text"] for p in passages])
    write_json(fabric / "semantic.json", sem)

    # Hierarchical clustering: how the corpus organises itself, independent of
    # the taxonomy it was filed under.
    write_json(fabric / "dendrogram.json", build_dendrogram(docs, passages))

    write_json(fabric / "index.json", {
        "passages": [{k: v for k, v in p.items()} for p in passages],
        "bm25": bm25.postings(),
    })
    write_json(fabric / "documents.json", meta)

    manifest = {
        "slug": pack.slug,
        "tenant": pack.tenant,
        "industry": pack.industry,
        "tagline": pack.tagline,
        "accent": pack.accent,
        "units": list(pack.units),
        "roles": list(pack.roles),
        "systems": list(pack.systems),
        "lexicon": list(pack.lexicon),
        "questions": list(pack.questions),
        "doc_types": [
            {"key": d.key, "name": d.name, "abbrev": d.abbrev,
             "authority": d.authority, "system": d.system,
             "sections": list(d.sections)}
            for d in pack.doc_types
        ],
        "code_systems": [
            {"key": c.key, "name": c.name, "authority": c.authority,
             "fmt": c.fmt, "codes": [{"c": a, "m": b} for a, b in c.codes]}
            for c in pack.code_systems
        ],
        "workflows": [
            {"key": w.key, "name": w.name, "states": list(w.states),
             "terminal": list(w.terminal)}
            for w in pack.workflows
        ],
        "ontology": [{"s": s, "r": r, "t": t} for s, r, t in pack.ontology],
        "counts": {
            "documents": len(docs),
            "passages": len(passages),
            "words": sum(d["words"] for d in docs),
            "entities": len(graph["nodes"]),
            "relationships": len(graph["edges"]),
        },
        "mentions": mentions,
        "health": health["overall"],
        "semantic": {"enabled": sem.get("enabled", False),
                     "components": sem.get("components", 0),
                     "variance": sem.get("variance", 0)},
    }
    write_json(root / "tenant.json", manifest)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="build a single tenant slug")
    ap.add_argument("--docs", type=int, help="override document count")
    args = ap.parse_args()

    targets = [packlib.get(args.only)] if args.only else list(packlib.PACKS)

    print("=" * 74)
    print("  Knowledge Fabric — synthetic corpus build")
    print("=" * 74)

    manifests = []
    for pack in targets:
        n = args.docs or DOC_TARGET.get(pack.slug, 58)
        m = build_tenant(pack, n)
        c = m["counts"]
        sm = m["semantic"]
        lsa = f"LSA {sm['components']}d/{sm['variance']:.0%}" if sm["enabled"] else "LSA off"
        print(f"  {pack.slug:<16} {m['tenant']:<27} "
              f"{c['documents']:>3}d {c['passages']:>4}p "
              f"{c['entities']:>3}e {c['relationships']:>4}r · "
              f"health {m['health']:>5} · {lsa}")
        manifests.append(m)

    registry = {
        "generated": True,
        "tenants": [
            {k: m[k] for k in ("slug", "tenant", "industry", "tagline",
                               "accent", "counts", "health")}
            for m in manifests
        ],
        "totals": {
            "documents": sum(m["counts"]["documents"] for m in manifests),
            "words": sum(m["counts"]["words"] for m in manifests),
            "entities": sum(m["counts"]["entities"] for m in manifests),
            "relationships": sum(m["counts"]["relationships"] for m in manifests),
            "passages": sum(m["counts"]["passages"] for m in manifests),
        },
    }
    write_json(TENANTS / "registry.json", registry)

    t = registry["totals"]
    print("-" * 74)
    print(f"  {len(manifests)} tenants · {t['documents']} documents · "
          f"{t['words']:,} words · {t['entities']} entities · "
          f"{t['relationships']} relationships")
    print("  every document synthetic · no client data · reserved-range identifiers")
    print("=" * 74)


if __name__ == "__main__":
    main()
