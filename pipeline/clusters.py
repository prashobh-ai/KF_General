"""Hierarchical clustering of the corpus, exported as a radial dendrogram.

Why a dendrogram and not another bar chart
------------------------------------------
Every other panel in the insights view answers "how much of X is there". None
of them answer "how does this corpus organise itself" — which is the question a
knowledge owner actually has when they inherit a document estate.

Agglomerative clustering answers it directly. Documents are embedded in the
same LSA concept space the semantic retriever uses, then merged bottom-up by
Ward linkage. The resulting tree is not imposed by the taxonomy: two documents
land near each other because they *talk about the same things*, which is why
the clusters frequently cut across owning units and reveal that the same
subject is being documented independently in three places.

Rendered as a circular dendrogram because the interesting structure is the
top-level split — the branches nearest the centre — and a radial layout puts
those at the centre of attention while giving every leaf equal room on the rim.
"""

from __future__ import annotations

import numpy as np
from scipy.cluster.hierarchy import linkage
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

# Ward linkage needs Euclidean distance; it produces compact, similarly-sized
# clusters, which is what makes a readable dendrogram. Average linkage on this
# data yields one giant cluster and a scatter of singletons.
LINKAGE_METHOD = "ward"
MAX_LEAVES = 80
TOP_CLUSTERS = 8


def _doc_vectors(docs: list[dict], passages: list[dict]) -> tuple[np.ndarray, list[str]]:
    """One vector per document, from the text of its passages."""
    by_doc: dict[str, list[str]] = {}
    for p in passages:
        by_doc.setdefault(p["doc"], []).append(p["text"])

    ids, texts = [], []
    for d in docs:
        chunks = by_doc.get(d["id"])
        if not chunks:
            continue
        ids.append(d["id"])
        # Repeat the subject so the document's own topic label carries weight
        # against a long body — otherwise long documents all drift together.
        texts.append(f"{d['subject']} {d['subject']} {d['unit']} " + " ".join(chunks))

    if len(ids) < 4:
        return np.zeros((0, 0)), []

    vec = TfidfVectorizer(max_features=4000, min_df=2, sublinear_tf=True,
                          stop_words="english",
                          token_pattern=r"[A-Za-z][A-Za-z0-9\-]{2,}")
    tfidf = vec.fit_transform(texts)
    n_comp = min(48, tfidf.shape[1] - 1, tfidf.shape[0] - 1)
    if n_comp < 2:
        return np.zeros((0, 0)), []
    svd = TruncatedSVD(n_components=n_comp, random_state=42)
    X = svd.fit_transform(tfidf)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return X / norms, ids


def build_dendrogram(docs: list[dict], passages: list[dict]) -> dict:
    X, ids = _doc_vectors(docs, passages)
    if not len(ids):
        return {"enabled": False}

    doc_by_id = {d["id"]: d for d in docs}
    Z = linkage(X, method=LINKAGE_METHOD)
    n = len(ids)

    # Assign each leaf to one of the top-level clusters by cutting the tree.
    # Colour comes from that assignment, so an entire branch shares a hue and
    # the eye reads groups rather than individual leaves.
    from scipy.cluster.hierarchy import fcluster
    k = min(TOP_CLUSTERS, max(2, n // 6))
    labels = fcluster(Z, t=k, criterion="maxclust")

    # Rebuild the merge tree as nested JSON. scipy returns a linkage matrix,
    # which is compact but not directly renderable.
    nodes: dict[int, dict] = {}
    for i, doc_id in enumerate(ids):
        d = doc_by_id[doc_id]
        nodes[i] = {
            "leaf": True,
            "id": doc_id,
            "name": d["subject"].title(),
            "unit": d["unit"],
            "type": d["abbrev"] or d["type"],
            "cluster": int(labels[i]),
            "height": 0.0,
            "size": 1,
        }

    for j, (a, b, dist, cnt) in enumerate(Z):
        a, b = int(a), int(b)
        left, right = nodes[a], nodes[b]
        cl = left.get("cluster") if left.get("cluster") == right.get("cluster") else 0
        nodes[n + j] = {
            "leaf": False,
            "height": float(dist),
            "size": int(cnt),
            "cluster": cl,
            "children": [left, right],
        }
        nodes.pop(a, None)
        nodes.pop(b, None)

    root = nodes[n + len(Z) - 1]

    # Collapse to a readable number of leaves. A rim of 400 labels is a grey
    # smear; merging the smallest branches keeps the shape and the legibility.
    def collapse(node: dict, budget: int) -> dict:
        if node["leaf"] or budget <= 1:
            if not node["leaf"]:
                return {"leaf": True, "name": f"{node['size']} documents",
                        "cluster": node.get("cluster", 0), "size": node["size"],
                        "collapsed": True, "height": node["height"]}
            return node
        kids = node["children"]
        total = sum(c["size"] for c in kids) or 1
        out = []
        for c in kids:
            share = max(1, int(round(budget * c["size"] / total)))
            out.append(collapse(c, share))
        return {**node, "children": out}

    tree = collapse(root, MAX_LEAVES)

    # Cluster summaries: what each group is actually about.
    summary: dict[int, dict] = {}
    for i, doc_id in enumerate(ids):
        c = int(labels[i])
        d = doc_by_id[doc_id]
        s = summary.setdefault(c, {"cluster": c, "n": 0, "units": {}, "subjects": {}})
        s["n"] += 1
        s["units"][d["unit"]] = s["units"].get(d["unit"], 0) + 1
        s["subjects"][d["subject"]] = s["subjects"].get(d["subject"], 0) + 1

    clusters = []
    for c, s in sorted(summary.items(), key=lambda kv: -kv[1]["n"]):
        top_units = sorted(s["units"].items(), key=lambda kv: -kv[1])[:3]
        top_subj = sorted(s["subjects"].items(), key=lambda kv: -kv[1])[:4]
        clusters.append({
            "cluster": c,
            "n": s["n"],
            "units": [u for u, _ in top_units],
            "subjects": [x.title() for x, _ in top_subj],
            # A cluster spanning several units means the same subject is being
            # documented independently in more than one place — the most
            # actionable thing this view surfaces.
            "spread": len(s["units"]),
        })

    return {
        "enabled": True,
        "method": LINKAGE_METHOD,
        "documents": n,
        "clusters": clusters,
        "tree": tree,
    }
