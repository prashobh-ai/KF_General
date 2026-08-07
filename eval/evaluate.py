"""Retrieval evaluation harness.

    python -m eval.evaluate [--tenant q-health] [--json]

We grade RETRIEVAL, not phrasing. If the right evidence never surfaces, no
amount of answer polish can save the response — and conversely, an answer that
reads well over the wrong passages is the most dangerous output the system can
produce. So every case declares what correct *evidence* looks like, and the
harness measures whether retrieval found it.

Three retrieval configurations are scored on identical cases so the value of
each layer is measurable rather than asserted:

    bm25       lexical only
    semantic   LSA only
    hybrid     reciprocal rank fusion of both

Metrics
-------
recall@5 / @10   did any relevant passage appear in the top k
MRR              1 / rank of the first relevant passage
nDCG@10          rank-discounted gain, so position matters

The gold set lives in eval/gold_set.yaml. Cases are written as field matchers
rather than passage ids, so they stay valid as the corpus is regenerated.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
TENANTS = ROOT / "tenants"



STOP = frozenset("""
a an and are as at be been but by for from had has have if in into is it its of
on or that the their there these this to was were which who will with not must
may shall any all each per than then when where while can could would should
about across after before between during no nor own same so too very what how
why does do did
""".split())

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")


def tokenise(s: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(s)
            if len(t) > 2 and t.lower() not in STOP]


# ---------------------------------------------------------------------------
# Retrievers — mirror the browser implementations exactly
# ---------------------------------------------------------------------------

class BM25:
    """Replays the shipped postings. Scoring must match engine.js or the
    harness measures a system nobody uses."""

    def __init__(self, index: dict, passages: list[dict], docs: dict):
        self.idf = index["bm25"]["idf"]
        self.postings = index["bm25"]["postings"]
        self.passages = passages
        self.docs = docs

    def search(self, query: str, top_k: int = 40):
        terms = tokenise(query)
        scores: dict[int, float] = {}
        matched: dict[int, set] = {}
        for t in terms:
            idf = self.idf.get(t)
            post = self.postings.get(t)
            if not idf or not post:
                continue
            for i, w in post:
                scores[i] = scores.get(i, 0.0) + idf * w
                matched.setdefault(i, set()).add(t)
        if not scores:
            return []
        tset = set(terms)
        out = []
        for i, base in scores.items():
            p = self.passages[i]
            d = self.docs.get(p["doc"], {})
            boost = 1.0
            if set(tokenise(d.get("subject", ""))) & tset:
                boost += 0.85
            if set(tokenise(d.get("unit", ""))) & tset:
                boost += 0.35
            if set(tokenise(p.get("section", ""))) & tset:
                boost += 0.45
            cov = len(matched[i]) / max(1, len(terms))
            out.append((i, base * boost * (0.55 + 0.75 * cov)))
        out.sort(key=lambda x: -x[1])
        return [{"idx": i, "score": s} for i, s in out[:top_k]]


class SemanticRetriever:
    def __init__(self, payload: dict):
        self.enabled = payload.get("enabled", False)
        if not self.enabled:
            return
        self.dim = payload["components"]
        self.vocab = {t: i for i, t in enumerate(payload["vocab"])}
        self.idf = payload["idf"]
        self.terms = np.array(payload["terms"], dtype=np.float32) * (payload["termScale"] / 127)
        self.docs = np.array(payload["docs"], dtype=np.float32) * (payload["docScale"] / 127)

    def search(self, query: str, top_k: int = 40):
        if not self.enabled:
            return []
        v = np.zeros(self.dim, dtype=np.float32)
        used = 0
        for t in tokenise(query):
            i = self.vocab.get(t)
            if i is None:
                continue
            v += self.terms[i] * self.idf[i]
            used += 1
        if not used:
            return []
        n = np.linalg.norm(v) or 1.0
        v /= n
        scores = self.docs @ v
        order = np.argsort(scores)[::-1][:top_k]
        return [{"idx": int(i), "score": float(scores[i])}
                for i in order if scores[i] > 0.04]


def rrf(runs: list[tuple[list[dict], float]], k: int = 60, top_k: int = 40):
    """Weighted reciprocal rank fusion. Mirrors engine.js exactly — a harness
    that measures different code from the one shipped is worse than none."""
    fused: dict[int, float] = {}
    for run, weight in runs:
        for rank, hit in enumerate(run, start=1):
            fused[hit["idx"]] = fused.get(hit["idx"], 0.0) + weight / (k + rank)
    ranked = sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]
    return [{"idx": i, "score": s} for i, s in ranked]


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def field_of(passage: dict, doc: dict, field: str):
    if field.startswith("doc."):
        return doc.get(field[4:])
    return passage.get(field)


def satisfies(passage: dict, doc: dict, matcher: dict) -> bool:
    for key, want in matcher.items():
        if key == "text_contains":
            text = passage["text"].lower()
            if not any(w.lower() in text for w in want):
                return False
        elif key == "section_contains":
            sec = (passage.get("section") or "").lower()
            if not any(w.lower() in sec for w in want):
                return False
        else:
            got = field_of(passage, doc, key)
            if got is None or str(want).lower() not in str(got).lower():
                return False
    return True


def relevant(passage: dict, doc: dict, case: dict) -> bool:
    for m in case.get("relevant_any", []):
        if satisfies(passage, doc, m):
            return True
    return False


def dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def score_case(results: list[dict], passages: list[dict], docs: dict,
               case: dict, k: int = 10) -> dict:
    top = results[:k]
    flags = []
    for r in top:
        p = passages[r["idx"]]
        flags.append(relevant(p, docs.get(p["doc"], {}), case))

    first = next((i for i, f in enumerate(flags) if f), None)
    ideal = dcg([1.0] * max(1, sum(flags)))
    return {
        "recall@5": float(any(flags[:5])),
        "recall@10": float(any(flags[:10])),
        "mrr": 1.0 / (first + 1) if first is not None else 0.0,
        "ndcg@10": (dcg([1.0 if f else 0.0 for f in flags]) / ideal) if ideal else 0.0,
    }


# ---------------------------------------------------------------------------

def evaluate_tenant(slug: str, cases: list[dict]) -> dict:
    fab = TENANTS / slug / "fabric"
    index = json.loads((fab / "index.json").read_text())
    passages = index["passages"]
    docs = {d["id"]: d for d in json.loads((fab / "documents.json").read_text())}
    sem_payload = json.loads((fab / "semantic.json").read_text())

    bm25 = BM25(index, passages, docs)
    sem = SemanticRetriever(sem_payload)

    configs = {"bm25": [], "semantic": [], "hybrid": []}
    per_case = []

    for case in cases:
        q = case["question"]
        lex = bm25.search(q)
        sm = sem.search(q)

        # Adaptive weighting: semantic contributes in proportion to how much
        # the query's vocabulary is MISSING from the lexical index.
        qt = tokenise(q)
        lex_cov = (sum(1 for t in qt if t in bm25.postings) / len(qt)) if qt else 0.0
        sem_w = 0.30 + 0.85 * (1 - lex_cov)

        # Always fuse. An earlier build gated fusion behind a "lexical is weak"
        # test because equal-weight fusion measured WORSE than BM25 alone
        # (-8.2% nDCG). The real cause turned out to be a lexically flat corpus:
        # section topics never reached the prose, so LSA had no co-occurrence
        # structure to learn. With that fixed, a full gate sweep shows fusion
        # wins at every threshold and always-fuse is the optimum (+3.7%).
        runs = [(r, w) for r, w in ((lex, 1.0), (sm, sem_w)) if r]
        hyb = rrf(runs) if runs else []

        row = {"id": case["id"], "category": case.get("category", "general")}
        for name, res in (("bm25", lex), ("semantic", sm), ("hybrid", hyb)):
            m = score_case(res, passages, docs, case)
            configs[name].append(m)
            row[name] = m
        per_case.append(row)

    summary = {}
    for name, rows in configs.items():
        if not rows:
            continue
        summary[name] = {
            metric: round(float(np.mean([r[metric] for r in rows])), 4)
            for metric in ("recall@5", "recall@10", "mrr", "ndcg@10")
        }
    return {"tenant": slug, "cases": len(cases),
            "summary": summary, "per_case": per_case}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", help="evaluate a single tenant")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args()

    gold = yaml.safe_load((ROOT / "eval" / "gold_set.yaml").read_text())
    by_tenant: dict[str, list] = {}
    for c in gold["cases"]:
        by_tenant.setdefault(c["tenant"], []).append(c)

    targets = [args.tenant] if args.tenant else sorted(by_tenant)
    reports = []
    for slug in targets:
        if slug not in by_tenant:
            continue
        if not (TENANTS / slug / "fabric" / "index.json").exists():
            raise SystemExit(f"{slug} not built — run `python -m pipeline.build_tenants`")
        reports.append(evaluate_tenant(slug, by_tenant[slug]))

    if args.json:
        print(json.dumps(reports, indent=2))
        return

    print("=" * 78)
    print("  Retrieval evaluation — lexical vs semantic vs hybrid")
    print("=" * 78)
    print(f"  {'tenant':<18}{'config':<11}{'R@5':>7}{'R@10':>7}{'MRR':>7}{'nDCG@10':>9}")
    print("-" * 78)

    agg: dict[str, list] = {"bm25": [], "semantic": [], "hybrid": []}
    for r in reports:
        for cfg in ("bm25", "semantic", "hybrid"):
            s = r["summary"].get(cfg)
            if not s:
                continue
            agg[cfg].append(s)
            print(f"  {r['tenant']:<18}{cfg:<11}"
                  f"{s['recall@5']:>7.2f}{s['recall@10']:>7.2f}"
                  f"{s['mrr']:>7.2f}{s['ndcg@10']:>9.3f}")
        print()

    print("-" * 78)
    print("  OVERALL")
    base = None
    for cfg in ("bm25", "semantic", "hybrid"):
        rows = agg[cfg]
        if not rows:
            continue
        means = {m: float(np.mean([x[m] for x in rows]))
                 for m in ("recall@5", "recall@10", "mrr", "ndcg@10")}
        if cfg == "bm25":
            base = means
        delta = ""
        if base and cfg == "hybrid":
            lift = (means["ndcg@10"] - base["ndcg@10"]) / max(base["ndcg@10"], 1e-9)
            delta = f"   nDCG lift vs lexical: {lift:+.1%}"
        print(f"  {'':<18}{cfg:<11}{means['recall@5']:>7.2f}{means['recall@10']:>7.2f}"
              f"{means['mrr']:>7.2f}{means['ndcg@10']:>9.3f}{delta}")
    print("=" * 78)


if __name__ == "__main__":
    main()
