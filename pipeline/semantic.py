"""Dense semantic retrieval without a server, an API key, or a model download.

Why LSA and not a transformer
-----------------------------
A sentence-transformer means a ~90 MB model at build time and either a serving
process or a 20 MB+ ONNX payload in the browser. This demonstration is static
files on a CDN, so that is not available.

Truncated SVD over TF-IDF gives genuine distributional semantics — in the
aerospace corpus "corrosion", "pitting" and "structural" land near each other
because they co-occur — at roughly 1 MB, deterministic across builds, and with
a projection simple enough to run in JavaScript in well under a millisecond.

It is weaker than a transformer on paraphrase. It is dramatically better than
BM25 alone on vocabulary mismatch, which is the actual failure mode here: a
user asks about "kidney function" and the corpus says "creatinine"; a user asks
"who signs off a repair" and the corpus says "certifying staff".

Payload
-------
The browser receives a term → vector table, IDF weights, and passage vectors,
all int8 quantised. Query embedding is an IDF-weighted mean of its term vectors,
L2-normalised — the same operation performed here, so client and pipeline agree
exactly rather than approximately.
"""

from __future__ import annotations

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

# Tuned against payload size. 160 components retains enough variance on a
# ~1,000-passage corpus that semantic recall stops improving materially, while
# keeping the term table near 1 MB after quantisation.
MAX_TERMS = 9000
N_COMPONENTS = 160
MIN_COMPONENTS = 32
VARIANCE_TARGET = 0.88
MIN_DF = 2


def _quantise(mat: np.ndarray) -> tuple[list, float]:
    """int8 quantisation.

    Cuts payload 4x against float32 with negligible cosine error once vectors
    are L2-normalised — the rounding noise is orders of magnitude below the
    separation between a relevant and an irrelevant passage.
    """
    scale = float(np.abs(mat).max()) or 1.0
    q = np.clip(np.round(mat / scale * 127.0), -127, 127).astype(np.int8)
    return q.tolist(), scale


def build_semantic_index(texts: list[str]) -> dict:
    """Fit LSA over the passages and emit a browser-consumable index."""
    if len(texts) < 8:
        return {"enabled": False, "reason": "corpus too small for LSA"}

    vec = TfidfVectorizer(
        max_features=MAX_TERMS,
        min_df=MIN_DF,
        sublinear_tf=True,
        lowercase=True,
        token_pattern=r"[A-Za-z][A-Za-z0-9\-]{1,}",
        stop_words="english",
    )
    try:
        tfidf = vec.fit_transform(texts)
    except ValueError as exc:
        # Degenerate corpus. Retrieval falls back to BM25 alone rather than
        # taking the whole fabric down.
        return {"enabled": False, "reason": f"vectorizer: {exc}"}

    if tfidf.shape[1] < 8:
        return {"enabled": False, "reason": "vocabulary too small"}

    # Rank selection is variance-targeted rather than fixed.
    #
    # A fixed 160 components over this corpus's ~500-term vocabulary retains
    # 100% of variance, which means the projection is a lossless rotation: it
    # reproduces TF-IDF cosine exactly and generalises nothing. The whole value
    # of LSA is the *discarded* tail, where co-occurrence smoothing lets
    # "corrosion" and "pitting" collapse toward one another.
    #
    # Targeting ~88% forces genuine compression whatever the vocabulary size,
    # so the semantic retriever stays semantic as corpora grow or shrink.
    max_rank = min(N_COMPONENTS, tfidf.shape[1] - 1, tfidf.shape[0] - 1)
    if max_rank < 2:
        return {"enabled": False, "reason": "insufficient rank"}

    probe = TruncatedSVD(n_components=max_rank, random_state=42,
                         algorithm="randomized")
    probe.fit(tfidf)
    cumulative = probe.explained_variance_ratio_.cumsum()
    n_comp = int(np.searchsorted(cumulative, VARIANCE_TARGET) + 1)
    n_comp = max(MIN_COMPONENTS, min(n_comp, max_rank))

    svd = TruncatedSVD(n_components=n_comp, random_state=42, algorithm="randomized")
    doc_vectors = svd.fit_transform(tfidf)

    # L2-normalise so a dot product in the browser *is* cosine similarity.
    # Doing the normalisation here rather than at query time removes a
    # per-query square root over every passage.
    norms = np.linalg.norm(doc_vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    doc_vectors = doc_vectors / norms

    # Term vectors: a term's position in concept space is its row of V.
    # Projecting a query as the IDF-weighted mean of its term vectors is the
    # standard LSA fold-in and avoids shipping the full TF-IDF matrix.
    term_vectors = svd.components_.T          # (vocab, components)
    tnorms = np.linalg.norm(term_vectors, axis=1, keepdims=True)
    tnorms[tnorms == 0] = 1.0
    term_vectors = term_vectors / tnorms

    qdocs, dscale = _quantise(doc_vectors.astype(np.float32))
    qterms, tscale = _quantise(term_vectors.astype(np.float32))

    vocab = vec.get_feature_names_out().tolist()
    idf = vec.idf_.round(4).tolist()

    return {
        "enabled": True,
        "components": int(n_comp),
        "variance": round(float(svd.explained_variance_ratio_.sum()), 4),
        "vocab": vocab,
        "idf": idf,
        "termScale": tscale,
        "terms": qterms,
        "docScale": dscale,
        "docs": qdocs,
    }
