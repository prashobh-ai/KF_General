/* =============================================================================
   Engine — hybrid retrieval, MMR answer composition, derived confidence.

   Three layers, each solving a failure the layer below cannot:

   1. RETRIEVAL. BM25 handles exact terms and identifiers; LSA handles
      vocabulary mismatch. They are fused by Reciprocal Rank Fusion rather than
      score blending, because BM25 scores are unbounded and corpus-dependent
      while cosine is bounded [-1,1] — normalising them onto a common scale
      needs constants that go stale the moment the corpus changes. RRF ignores
      magnitudes and fuses on rank position, so it needs no tuning.

   2. COMPOSITION. Selecting the top-scoring sentence per document produces
      repetition, because the highest-scoring sentences across documents are
      often paraphrases of each other. Maximal Marginal Relevance forces each
      added sentence to contribute something the answer does not already have.

   3. CONFIDENCE. Combined as a weighted GEOMETRIC mean, because the signals
      are conjunctive: an answer with excellent retrieval but zero query
      coverage is not "average", it is wrong. A geometric mean punishes a single
      near-zero component; an arithmetic mean hides it.

   Nothing here calls a model. Every number is measurable from the retrieval run,
   and every sentence in an answer is lifted verbatim from an indexed passage.
   ============================================================================= */

(function (global) {
  'use strict';

  const STOP = new Set(('a an and are as at be been but by for from had has have if in into is it its of on or ' +
    'that the their there these this to was were which who will with not must may shall any all each per than ' +
    'then when where while can could would should about across after before between during no nor own same so ' +
    'too very what how why does do did').split(' '));

  // A query made entirely of function words carries no retrievable intent.
  // BM25 will happily return hundreds of passages for "the" — technically
  // correct, operationally useless, and it erodes trust the moment a
  // meaningless query produces a confident-looking answer.
  const FUNCTION_ONLY = new Set(('the a an and or but if then of to in on at by for with is are was were be ' +
    'been being it its this that these those as from so than too very can will just what how why')
    .split(' '));

  const RRF_K = 60;          // Cormack et al. (2009)
  const MMR_LAMBDA = 0.72;   // relevance-leaning, enough diversity pressure to kill paraphrase
  const MAX_SENTENCES = 5;
  const EPS = 0.02;          // floor so one zero signal cannot annihilate the product

  function tokenise(s) {
    return (String(s).toLowerCase().match(/[a-z][a-z0-9\-]{1,}/g) || [])
      .filter(t => t.length > 2 && !STOP.has(t));
  }

  function isDegenerate(q) {
    const t = (String(q).toLowerCase().match(/[a-z0-9]{2,}/g) || []);
    if (!t.length) return true;
    return t.every(x => FUNCTION_ONLY.has(x));
  }

  // ---------------------------------------------------------------------------
  // Semantic retriever — LSA fold-in
  // ---------------------------------------------------------------------------

  class Semantic {
    constructor(payload) {
      this.enabled = !!(payload && payload.enabled);
      if (!this.enabled) return;
      this.dim = payload.components;
      this.termScale = payload.termScale / 127;
      this.docScale = payload.docScale / 127;
      this.terms = payload.terms;
      this.docs = payload.docs;
      this.idf = payload.idf;
      this.vocab = new Map(payload.vocab.map((t, i) => [t, i]));
    }

    /** IDF-weighted mean of term vectors, L2-normalised. */
    embed(query) {
      if (!this.enabled) return null;
      const v = new Float32Array(this.dim);
      let used = 0;
      for (const t of tokenise(query)) {
        const idx = this.vocab.get(t);
        if (idx === undefined) continue;
        const w = this.idf[idx] || 1;
        const row = this.terms[idx];
        for (let d = 0; d < this.dim; d++) v[d] += row[d] * this.termScale * w;
        used++;
      }
      if (!used) return null;
      let n = 0;
      for (let d = 0; d < this.dim; d++) n += v[d] * v[d];
      n = Math.sqrt(n) || 1;
      for (let d = 0; d < this.dim; d++) v[d] /= n;
      return v;
    }

    /** Share of query terms present in the LSA vocabulary. */
    coverage(query) {
      if (!this.enabled) return 0;
      const t = tokenise(query);
      if (!t.length) return 0;
      return t.filter(x => this.vocab.has(x)).length / t.length;
    }

    search(query, topK) {
      const q = this.embed(query);
      if (!q) return [];
      const out = [];
      for (let i = 0; i < this.docs.length; i++) {
        const row = this.docs[i];
        let s = 0;
        for (let d = 0; d < this.dim; d++) s += row[d] * q[d];
        s *= this.docScale;
        if (s > 0.04) out.push({ chunkIdx: i, score: s });
      }
      out.sort((a, b) => b.score - a.score);
      return out.slice(0, topK);
    }
  }

  // ---------------------------------------------------------------------------
  // Fusion
  // ---------------------------------------------------------------------------

  function reciprocalRankFusion(runs, topK) {
    const fused = new Map();
    for (const run of runs) {
      if (!run || !run.results.length) continue;
      run.results.forEach((hit, i) => {
        const key = hit.chunkIdx;
        if (key == null) return;
        let e = fused.get(key);
        if (!e) {
          e = { chunkIdx: key, score: 0, retrievers: [], ranks: {}, contributions: {} };
          fused.set(key, e);
        }
        const c = (run.weight || 1) / (RRF_K + i + 1);
        e.score += c;
        e.ranks[run.retriever] = i + 1;
        e.contributions[run.retriever] = c;
        if (!e.retrievers.includes(run.retriever)) e.retrievers.push(run.retriever);
      });
    }
    return [...fused.values()].sort((a, b) => b.score - a.score).slice(0, topK);
  }


  // ---------------------------------------------------------------------------
  // Graph traversal — the layer plain RAG cannot replicate
  // ---------------------------------------------------------------------------
  //
  // Retrieval answers "which passages mention X". Traversal answers "what is X
  // connected to, and what governs those things" — a question whose answer is
  // distributed across documents and stated in full by none of them.
  //
  // The classic failure this fixes: ask "which aircraft are affected by open
  // Airworthiness Directives on the wing structure". Vector or lexical search
  // returns the AD documents, because those are what mention wing structure and
  // Airworthiness Directives. It cannot return the aircraft, because no AD
  // document lists them — the AD names a component, and a different document
  // records that the component is installed on a tail number. One hop of
  // traversal recovers what no amount of retrieval tuning will.

  class GraphIndex {
    constructor(graph) {
      this.nodes = new Map(graph.nodes.map(n => [n.id, n]));
      this.kinds = graph.kinds || {};
      this.adj = new Map();
      this.asserted = new Map();   // typed, domain-meaningful edges only

      for (const e of graph.edges) {
        this._push(this.adj, e.s, { rel: e.rel, to: e.t, docs: e.docs, asserted: !!e.asserted });
        this._push(this.adj, e.t, { rel: e.rel, to: e.s, docs: e.docs, asserted: !!e.asserted, inverse: true });
        if (e.asserted) {
          this._push(this.asserted, e.s, { rel: e.rel, to: e.t, docs: e.docs });
          this._push(this.asserted, e.t, { rel: e.rel, to: e.s, docs: e.docs, inverse: true });
        }
      }

      // Instances are the concrete things — tail numbers, batches, claims.
      // Matching a query against their identifiers is how a question like
      // "what is open against N912ZZ" finds its entry point into the graph.
      this.instances = graph.nodes.filter(n => n.instance);
      this.byRef = new Map();
      for (const n of this.instances) {
        if (n.ref) this.byRef.set(String(n.ref).toLowerCase(), n);
      }
      this.labelIndex = graph.nodes.map(n => ({
        id: n.id, kind: n.kind, tokens: new Set(tokenise(n.label)),
      }));
    }

    _push(map, k, v) {
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(v);
    }

    /** Entry points: graph nodes the query names directly. */
    seeds(query) {
      const raw = String(query).toLowerCase();
      const qt = new Set(tokenise(query));
      const hits = new Map();

      // Exact identifier mention is the strongest possible signal.
      for (const [ref, node] of this.byRef) {
        if (ref.length >= 4 && raw.includes(ref)) hits.set(node.id, 3.0);
      }
      // Otherwise match on label tokens, requiring real overlap so a single
      // common word cannot drag in half the graph.
      for (const n of this.labelIndex) {
        if (!n.tokens.size) continue;
        let inter = 0;
        for (const t of n.tokens) if (qt.has(t)) inter++;
        if (!inter) continue;
        const score = inter / Math.sqrt(n.tokens.size);
        if (score >= 0.5) hits.set(n.id, Math.max(hits.get(n.id) || 0, score));
      }
      return [...hits.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
        .map(([id, score]) => ({ id, score, node: this.nodes.get(id) }));
    }

    /**
     * Breadth-first expansion over asserted edges, recording the path taken.
     * Depth is capped at 2: a third hop in a densely connected domain graph
     * reaches most of the corpus and stops discriminating.
     */
    expand(seedIds, maxDepth = 2, maxNodes = 60) {
      const seen = new Map();
      const queue = [];
      for (const id of seedIds) {
        if (!this.nodes.has(id)) continue;
        seen.set(id, { depth: 0, path: [], docs: [] });
        queue.push(id);
      }
      while (queue.length && seen.size < maxNodes) {
        const cur = queue.shift();
        const info = seen.get(cur);
        if (info.depth >= maxDepth) continue;
        for (const edge of (this.asserted.get(cur) || [])) {
          if (seen.has(edge.to)) continue;
          seen.set(edge.to, {
            depth: info.depth + 1,
            path: info.path.concat([{ from: cur, rel: edge.rel,
                                      to: edge.to, inverse: !!edge.inverse }]),
            docs: edge.docs || [],
          });
          queue.push(edge.to);
        }
      }
      return seen;
    }

    /** Human-readable chain, e.g. "AD 2024-04-01 —APPLIES_TO→ Component P77…". */
    describePath(path) {
      if (!path.length) return '';
      const parts = [];
      for (const step of path) {
        const from = this.nodes.get(step.from);
        const to = this.nodes.get(step.to);
        parts.push(`${from ? from.label : step.from} \u2014${step.rel}\u2192 ${to ? to.label : step.to}`);
      }
      return parts.join(' \u00b7 ');
    }
  }

  // ---------------------------------------------------------------------------
  // Engine
  // ---------------------------------------------------------------------------

  class Engine {
    constructor(bundle) {
      this.passages = bundle.index.passages;
      this.idf = bundle.index.bm25.idf;
      this.postings = bundle.index.bm25.postings;
      this.docs = new Map(bundle.documents.map(d => [d.id, d]));
      this.graph = bundle.graph;
      this.gi = new GraphIndex(bundle.graph);
      this.semantic = new Semantic(bundle.semantic);
      this.docText = bundle.docText || null;

      // doc id -> passage indices, so a document reached by traversal can
      // contribute its passages without a linear scan per query.
      this.byDoc = new Map();
      this.passages.forEach((p, i) => {
        if (!this.byDoc.has(p.doc)) this.byDoc.set(p.doc, []);
        this.byDoc.get(p.doc).push(i);
      });
    }

    // -- lexical ------------------------------------------------------------

    bm25(query, topK) {
      const terms = tokenise(query);
      if (!terms.length) return [];
      const scores = new Map();
      const matched = new Map();

      for (const t of terms) {
        const idf = this.idf[t], post = this.postings[t];
        if (!idf || !post) continue;
        for (const [i, w] of post) {
          scores.set(i, (scores.get(i) || 0) + idf * w);
          if (!matched.has(i)) matched.set(i, new Set());
          matched.get(i).add(t);
        }
      }

      // Field boost. A term in the document's own subject label is a far
      // stronger relevance signal than the same term buried in body prose.
      const termSet = new Set(terms);
      const out = [];
      for (const [i, base] of scores) {
        const p = this.passages[i];
        const d = this.docs.get(p.doc);
        let boost = 1;
        if (d) {
          if (tokenise(d.subject).some(t => termSet.has(t))) boost += 0.85;
          if (tokenise(d.unit).some(t => termSet.has(t))) boost += 0.35;
        }
        if (tokenise(p.section).some(t => termSet.has(t))) boost += 0.45;
        const cov = matched.get(i).size / terms.length;
        out.push({ chunkIdx: i, score: base * boost * (0.55 + 0.75 * cov) });
      }
      out.sort((a, b) => b.score - a.score);
      return out.slice(0, topK);
    }

    /** Share of query terms that exist in the lexical postings. */
    lexicalCoverage(query) {
      const t = tokenise(query);
      if (!t.length) return 0;
      return t.filter(x => this.postings[x]).length / t.length;
    }

    /**
     * Adaptive fusion weighting.
     *
     * Equal-weight RRF measured WORSE than BM25 alone on this corpus (-8.2%
     * nDCG). That is the expected result when one retriever is much stronger:
     * fusing a weak run at equal weight drags good results down the list.
     *
     * The fix is not to abandon fusion but to weight it by the evidence for
     * needing it. Lexical coverage — the share of query terms that actually
     * exist in the BM25 postings — is a direct measure of vocabulary mismatch:
     *
     *   coverage high  the user is speaking the corpus's language, BM25 is
     *                  reliable, semantic should only break ties
     *   coverage low   the user's words are absent from the corpus, BM25 has
     *                  nothing to match on, semantic must carry the query
     *
     * So semantic weight scales inversely with lexical coverage. This keeps the
     * vocabulary-mismatch benefit while removing the dilution on exact-term
     * queries, which is where the regression came from.
     */
    _semanticWeight(query) {
      const cov = this.lexicalCoverage(query);
      return 0.30 + 0.85 * (1 - cov);
    }

    // -- hybrid -------------------------------------------------------------

    search(query, topK) {
      topK = topK || 20;
      const t0 = performance.now();

      if (isDegenerate(query)) {
        return { results: [], mode: 'rejected', latencyMs: 0,
                 diagnostics: { rejected: 'query contains no content terms',
                                lexicalHits: 0, semanticHits: 0, fusedHits: 0,
                                agreement: 0, vocabCoverage: 0 } };
      }

      const lexical = this.bm25(query, topK * 2);
      const sem = this.semantic.enabled ? this.semantic.search(query, topK * 2) : [];

      let mode = 'hybrid';
      if (!sem.length && lexical.length) mode = 'lexical';
      else if (!lexical.length && sem.length) mode = 'semantic';
      else if (!lexical.length && !sem.length) mode = 'empty';

      const semWeight = this._semanticWeight(query);
      const runs = [];
      if (lexical.length) runs.push({ retriever: 'bm25', results: lexical, weight: 1.0 });
      if (sem.length) runs.push({ retriever: 'semantic', results: sem, weight: semWeight });

      const fused = reciprocalRankFusion(runs, topK).map(r =>
        Object.assign(r, { p: this.passages[r.chunkIdx] }));

      const agreed = fused.filter(r => r.retrievers.length > 1).length;

      return {
        results: fused,
        mode,
        latencyMs: +(performance.now() - t0).toFixed(2),
        diagnostics: {
          lexicalHits: lexical.length,
          semanticHits: sem.length,
          fusedHits: fused.length,
          agreement: fused.length ? +(agreed / fused.length).toFixed(2) : 0,
          vocabCoverage: +this.semantic.coverage(query).toFixed(2),
          lexicalCoverage: +this.lexicalCoverage(query).toFixed(2),
          semanticWeight: +semWeight.toFixed(2),
          // RRF scores cannot measure how decisively the top result won — rank 1
          // and rank 5 differ by under 3% BY CONSTRUCTION. Margin must come from
          // the underlying retriever scores, which have real dynamic range.
          lexicalScores: lexical.slice(0, 6).map(h => h.score),
          semanticScores: sem.slice(0, 6).map(h => h.score),
        },
      };
    }

    // -- composition --------------------------------------------------------

    _sentences(p) {
      return p.text.split(/(?<=[.!?])\s+/)
        .map(s => s.trim())
        .filter(s => {
          const w = s.split(/\s+/).length;
          return w >= 9 && w <= 60;
        });
    }

    _relevance(sentence, qterms) {
      const st = new Set(tokenise(sentence));
      let cover = 0, weight = 0;
      for (const t of qterms) {
        if (st.has(t)) { cover++; weight += (this.idf[t] || 0.8); }
      }
      if (!cover) return 0;
      // Mild length normalisation only — enough to break ties, not enough to
      // let a fragment outrank a substantive sentence.
      return weight * (1 + cover * 0.35) / Math.pow(st.size || 1, 0.22);
    }

    _similarity(a, b) {
      const A = new Set(tokenise(a)), B = new Set(tokenise(b));
      if (!A.size || !B.size) return 0;
      let inter = 0;
      for (const t of A) if (B.has(t)) inter++;
      return inter / Math.sqrt(A.size * B.size);
    }

    /**
     * Maximal Marginal Relevance selection.
     *
     *     argmax [ lambda*relevance(s) - (1-lambda)*max similarity(s, chosen) ]
     *
     * Taking the top sentence from each top document seems reasonable and
     * produces visibly broken answers, because the highest-scoring sentences
     * across documents are frequently paraphrases of one another. MMR makes
     * each addition compete on what it adds rather than on raw score.
     */
    _mmr(candidates, limit) {
      const chosen = [];
      const pool = candidates.slice();
      while (chosen.length < limit && pool.length) {
        let best = -1, bestScore = -Infinity;
        for (let i = 0; i < pool.length; i++) {
          const c = pool[i];
          let maxSim = 0;
          for (const ch of chosen) {
            const s = this._similarity(c.text, ch.text);
            if (s > maxSim) maxSim = s;
          }
          const score = MMR_LAMBDA * c.rel - (1 - MMR_LAMBDA) * maxSim * c.rel;
          if (score > bestScore) { bestScore = score; best = i; }
        }
        if (best < 0) break;
        const pick = pool.splice(best, 1)[0];
        let dup = false;
        for (const ch of chosen) {
          if (this._similarity(pick.text, ch.text) > 0.62) { dup = true; break; }
        }
        if (!dup) chosen.push(pick);
      }
      return chosen;
    }

    /**
     * Graph expansion of a retrieval run.
     *
     * Two independent entry points into the graph:
     *   1. entities the query NAMES directly (an identifier, an entity label);
     *   2. entities the retrieved documents CITE.
     *
     * From those seeds we traverse asserted domain edges and collect the
     * documents attached to everything reached. Those documents are the ones a
     * retrieval-only system misses: they never mention the query's terms, but
     * they describe things the query's subject is connected to.
     */
    graphExpand(query, run) {
      const seeds = this.gi.seeds(query);

      // Instances cited by the top retrieved documents.
      const citedSeeds = new Set();
      for (const r of run.results.slice(0, 8)) {
        const d = this.docs.get(r.p.doc);
        if (d && d.instances) d.instances.forEach(i => citedSeeds.add(i));
      }

      const seedIds = [...new Set([...seeds.map(s => s.id), ...citedSeeds])];
      if (!seedIds.length) {
        return { seeds: [], reached: [], newDocs: [], paths: [] };
      }

      const reached = this.gi.expand(seedIds, 2, 60);
      const retrievedDocs = new Set(run.results.map(r => r.p.doc));

      const newDocs = new Map();
      const paths = [];
      for (const [nodeId, info] of reached) {
        if (!info.depth) continue;           // seeds themselves are not a finding
        const node = this.gi.nodes.get(nodeId);
        if (!node) continue;
        for (const docId of (node.docs || [])) {
          if (retrievedDocs.has(docId)) continue;   // retrieval already had it
          if (!newDocs.has(docId)) {
            newDocs.set(docId, { docId, via: nodeId, depth: info.depth,
                                 path: info.path });
          }
        }
        if (info.path.length && paths.length < 14) {
          paths.push({ node, depth: info.depth, path: info.path,
                       text: this.gi.describePath(info.path) });
        }
      }

      // ---------------------------------------------------------------
      // Derived findings.
      //
      // This is the part retrieval cannot produce, and it took a measurement to
      // see why. Feeding graph-reached documents into the quote pool changed
      // nothing: those documents do not contain the question's words, so no
      // sentence in them ever scores as relevant. The graph's contribution was
      // never going to be *more quotations*.
      //
      // It is a derived answer set. "Which aircraft are affected by open ADs on
      // the wing structure" is answered by a LIST OF TAIL NUMBERS that appears
      // in no document, assembled by joining AD -> component -> aircraft. So we
      // surface the reached entities themselves, grouped by type, each carrying
      // the traversal path that justifies its inclusion.
      // ---------------------------------------------------------------
      const seedSet = new Set(seedIds);
      const groups = new Map();
      for (const [nodeId, info] of reached) {
        if (!info.depth || seedSet.has(nodeId)) continue;
        const node = this.gi.nodes.get(nodeId);
        if (!node || !node.instance) continue;    // concrete things only
        if (!groups.has(node.kind)) groups.set(node.kind, []);
        groups.get(node.kind).push({
          id: nodeId, label: node.label, ref: node.ref, attrs: node.attrs || {},
          depth: info.depth,
          path: this.gi.describePath(info.path),
          docs: node.docs || [],
        });
      }
      const findings = [...groups.entries()]
        .map(([kind, items]) => ({
          kind,
          label: this.gi.kinds[kind] || kind,
          items: items.sort((a, b) => a.depth - b.depth).slice(0, 12),
          total: items.length,
        }))
        .sort((a, b) => b.total - a.total)
        .slice(0, 5);

      return {
        seeds: seeds.map(s => s.node).filter(Boolean),
        seedIds,
        reached: [...reached.keys()],
        newDocs: [...newDocs.values()],
        paths,
        findings,
        findingCount: findings.reduce((n, g) => n + g.total, 0),
      };
    }

    answer(query, opts) {
      const cfg = Object.assign({ maxSentences: MAX_SENTENCES, graph: true }, opts || {});
      const run = this.search(query, 24);

      if (run.mode === 'rejected') {
        return { ok: false, reason: 'degenerate', query, run, confidence: 0 };
      }
      if (!run.results.length) {
        return { ok: false, reason: 'no-match', query, run, confidence: 0 };
      }

      const qterms = new Set(tokenise(query));
      const candidates = [];
      const pushFrom = (passage, source, boost) => {
        const doc = this.docs.get(passage.doc);
        if (!doc) return;
        for (const s of this._sentences(passage)) {
          const rel = this._relevance(s, qterms);
          // Must genuinely address the question. A sentence sharing one
          // incidental word is not evidence, and quoting it would misrepresent
          // the corpus as having answered something it did not.
          if (rel < 1.2) continue;
          candidates.push({ text: s, rel: rel * boost, passage, doc, source });
        }
      };

      for (const r of run.results) pushFrom(r.p, 'retrieval', 1.0);

      // Graph-reached documents. Discounted, because they were found by
      // connection rather than by matching the question — they must still earn
      // their place on sentence relevance, they simply get to compete at all.
      const graph = cfg.graph ? this.graphExpand(query, run)
                              : { seeds: [], reached: [], newDocs: [], paths: [] };
      let graphContributed = 0;
      for (const nd of graph.newDocs.slice(0, 10)) {
        for (const idx of (this.byDoc.get(nd.docId) || [])) {
          pushFrom(this.passages[idx], 'graph', 0.82);
          graphContributed++;
        }
      }

      if (!candidates.length) {
        return { ok: false, reason: 'no-coverage', query, run, confidence: 0 };
      }

      candidates.sort((a, b) => b.rel - a.rel);
      const picked = this._mmr(candidates.slice(0, 40), cfg.maxSentences);
      if (!picked.length) {
        return { ok: false, reason: 'no-coverage', query, run, confidence: 0 };
      }

      const signals = this._confidence(query, picked, run);
      const fromGraph = picked.filter(p => p.source === 'graph').length;

      return {
        ok: true,
        query,
        run,
        graph,
        graphSentences: fromGraph,
        sentences: picked,
        citations: picked.map((p, i) => ({ n: i + 1, doc: p.doc, passage: p.passage })),
        entities: this.entitiesFor(run.results.slice(0, 12)),
        sources: new Set(picked.map(p => p.doc.id)).size,
        confidence: signals.overall,
        signals,
      };
    }

    /**
     * Run the same question twice — retrieval only, then retrieval plus graph —
     * and report what the graph added.
     *
     * This exists because "knowledge graphs are better than RAG" is an assertion
     * until you can point at the specific documents one finds and the other
     * cannot. The comparison is run live, on the user's own question, so the
     * claim is demonstrated rather than described.
     */
    compare(query) {
      const ragOnly = this.answer(query, { graph: false });
      const fabric = this.answer(query, { graph: true });

      const ragDocs = new Set(ragOnly.ok ? ragOnly.sentences.map(s => s.doc.id) : []);
      const fabricDocs = new Set(fabric.ok ? fabric.sentences.map(s => s.doc.id) : []);
      const added = [...fabricDocs].filter(d => !ragDocs.has(d))
        .map(id => this.docs.get(id)).filter(Boolean);

      const g = fabric.graph || { findings: [], findingCount: 0, newDocs: [] };

      // Units and authorities reachable only through traversal. This is the
      // sharpest framing of the difference: retrieval stays inside the
      // vocabulary of the question, traversal crosses organisational
      // boundaries the question never named.
      const ragUnits = new Set([...ragDocs].map(d => (this.docs.get(d) || {}).unit));
      const newUnits = [...new Set(added.map(d => d.unit))].filter(u => !ragUnits.has(u));

      return {
        query,
        rag: ragOnly,
        fabric,
        addedDocs: added,
        newUnits,
        paths: fabric.graph ? fabric.graph.paths : [],
        seeds: fabric.graph ? fabric.graph.seeds : [],
        reached: fabric.graph ? fabric.graph.reached.length : 0,
        findings: g.findings,
        findingCount: g.findingCount,
        unreachableByRag: g.newDocs.length,
        verdict: g.findingCount
          ? `Retrieval returned ${ragDocs.size} document${ragDocs.size === 1 ? '' : 's'} that ` +
            `mention the question. Traversal additionally resolved ${g.findingCount} ` +
            `connected entit${g.findingCount === 1 ? 'y' : 'ies'} that no single document lists — ` +
            `the answer set itself, assembled across ${g.newDocs.length} further document` +
            `${g.newDocs.length === 1 ? '' : 's'}.`
          : 'Retrieval alone was sufficient here — the question named no entity the graph could traverse from.',
      };
    }

    /** Reconstruct a document's text from indexed passages. */
    documentText(docId) {
      const idxs = this.byDoc.get(docId) || [];
      const bySection = new Map();
      for (const i of idxs) {
        const p = this.passages[i];
        const key = `${p.section_no}|${p.section}`;
        if (!bySection.has(key)) bySection.set(key, []);
        bySection.get(key).push(p);
      }
      return [...bySection.entries()]
        .sort((a, b) => (+a[0].split('|')[0]) - (+b[0].split('|')[0]))
        .map(([key, ps]) => ({
          no: +key.split('|')[0],
          section: key.split('|').slice(1).join('|'),
          paras: ps.sort((a, b) => a.para - b.para),
        }));
    }

    // -- confidence ---------------------------------------------------------

    /**
     * Five measured signals, weighted geometric mean.
     *
     * A confidence number that never moves is not a score, it is decoration —
     * and a technical audience reads it as one the moment two very different
     * answers both report the same value.
     */
    _confidence(query, picked, run) {
      const d = run.diagnostics;
      const clamp = x => Math.max(EPS, Math.min(1, x));

      // 1. Retrieval margin — did one passage clearly win, or was it a coin toss?
      const ls = d.lexicalScores || [];
      let margin = 0.35;
      if (ls.length >= 2) {
        const top = ls[0];
        const rest = ls.slice(1, 5);
        const mean = rest.reduce((a, b) => a + b, 0) / rest.length;
        margin = top > 0 ? (top - mean) / top : 0;
      }

      // 2. Retriever agreement — two methods with different failure modes
      //    converging on the same passage is real corroboration.
      const agreement = d.agreement || 0;

      // 3. Question coverage — the strongest single predictor of a wrong answer
      //    is one that never mentions what was asked.
      const qt = tokenise(query);
      const answered = new Set();
      picked.forEach(p => tokenise(p.text).forEach(t => answered.add(t)));
      let wTotal = 0, wHit = 0;
      for (const t of qt) {
        const w = this.idf[t] || 0.8;
        wTotal += w;
        if (answered.has(t)) wHit += w;
      }
      const coverage = wTotal ? wHit / wTotal : 0;

      // 4. Source consensus — one document can be out of date or about a
      //    different variant. Capped at 3; beyond that it stops meaning more.
      const nDocs = new Set(picked.map(p => p.doc.id)).size;
      const consensus = 1 - 1 / Math.min(Math.max(nDocs, 1), 3);

      // 5. Authority spread — agreement across governing standards is stronger
      //    than agreement within one.
      const nAuth = new Set(picked.map(p => p.doc.authority)).size;
      const authority = Math.min(1, nAuth / 2);

      const specs = [
        ['retrievalMargin', margin, 0.24, 'One clear best source',
         'A decisive winner means the corpus contains a specific answer. A flat score distribution means many weak near-matches.'],
        ['retrieverAgreement', agreement, 0.18, 'Two methods agreed',
         'Lexical and semantic search fail in different ways. Both surfacing the same passage is genuine corroboration.'],
        ['queryCoverage', coverage, 0.26, 'Covers what you asked',
         'IDF-weighted share of the question\u2019s terms the answer actually addresses, so rare words count for most.'],
        ['sourceConsensus', consensus, 0.18, 'More than one document',
         'Independent corroboration across documents rather than resting on a single file.'],
        ['authoritySpread', authority, 0.14, 'More than one authority',
         'Evidence agreeing across separate governing standards is stronger than agreement within one.'],
      ];

      let product = 1;
      const parts = specs.map(([key, raw, weight, label, why]) => {
        const v = clamp(raw);
        product *= Math.pow(v, weight);
        return { key, label, why, weight, value: +v.toFixed(3), pct: Math.round(v * 100) };
      });

      return { overall: Math.round(product * 100), parts, mode: run.mode };
    }

    // -- graph --------------------------------------------------------------

    /**
     * Entities to activate in the graph.
     *
     * Deliberately narrow. Lighting every entity touched by every retrieved
     * document turns most of the graph red, and a graph where everything is
     * highlighted communicates exactly as much as one where nothing is. The
     * top few documents carry the answer; those are the ones worth showing.
     */
    entitiesFor(hits) {
      // Entities MENTIONED in the retrieved passages, not the metadata of the
      // documents they came from.
      //
      // Metadata activation lit the owning unit, system of record and governing
      // authority of every retrieved document. Those are shared across a
      // tenant, so every question activated the same hubs and the graph
      // stopped carrying information about what was asked. Mentions vary
      // sharply: a de-icing question lights de-icing, its stations and its
      // delay codes; a crew-legality question lights something else.
      const weight = new Map();
      hits.slice(0, 12).forEach((h, rank) => {
        const ents = (h.p && h.p.ents) || [];
        // Rank-weighted, so the passage that actually answered the question
        // contributes more than the twelfth-best match.
        const w = 1 / (1 + rank * 0.35);
        for (const e of ents) weight.set(e, (weight.get(e) || 0) + w);
      });

      if (!weight.size) return [];
      return [...weight.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 14)
        .map(([id]) => id);
    }

    lineage(r) {
      if (!r.ok) return [];
      const d = r.run.diagnostics;
      const units = new Set(r.sentences.map(s => s.doc.unit));
      const modeLabel = { hybrid: 'lexical + semantic', lexical: 'lexical only',
                          semantic: 'semantic only' }[r.run.mode] || r.run.mode;
      return [
        { k: 'Parse', h: 'Query analysed',
          d: `${tokenise(r.query).length} content terms after stop-word removal. ` +
             `${Math.round(d.vocabCoverage * 100)}% found in the semantic vocabulary.` },
        { k: 'Retrieve', h: `Dual retrieval \u2014 ${modeLabel}`,
          d: `BM25 returned ${d.lexicalHits} candidates over ` +
             `${this.passages.length.toLocaleString()} passages; LSA returned ${d.semanticHits}.` },
        { k: 'Fuse', h: 'Adaptive reciprocal rank fusion',
          d: `Ranks fused at k=${RRF_K}. Lexical coverage was ` +
             `${Math.round(d.lexicalCoverage * 100)}%, so the semantic run was weighted ` +
             `${d.semanticWeight}. ${Math.round(d.agreement * 100)}% of results were ` +
             `surfaced independently by both retrievers.` },
        { k: 'Compose', h: 'Maximal marginal relevance',
          d: `Sentences selected at \u03BB=${MMR_LAMBDA}, so each addition had to ` +
             `contribute something the answer did not already contain.` },
        { k: 'Traverse', h: 'Graph expansion',
          d: r.graph && r.graph.newDocs.length
             ? `${r.graph.seeds.length} entities matched the question directly; traversal ` +
               `reached ${r.graph.reached.length} connected entities and surfaced ` +
               `${r.graph.newDocs.length} documents retrieval did not rank. ` +
               `${r.graphSentences} of the quoted sentences came from them.`
             : `${r.entities.length} entities lit across ${units.size} organisational ` +
               `unit${units.size === 1 ? '' : 's'}. Traversal added no new documents.` },
        { k: 'Cite', h: 'Verbatim extraction',
          d: `${r.sentences.length} sentences lifted unmodified from ${r.sources} ` +
             `document${r.sources === 1 ? '' : 's'}. Retrieval completed in ${r.run.latencyMs} ms.` },
      ];
    }
  }

  global.Engine = Engine;
  global.kfTokenise = tokenise;
  global.KF_RRF_K = RRF_K;
})(window);
