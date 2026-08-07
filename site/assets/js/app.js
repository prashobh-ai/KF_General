/* =============================================================================
   App — the tenant demonstration page.
   ============================================================================= */

(function () {
  'use strict';

  const $  = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const SLUG = document.body.dataset.tenant;
  const BASE = document.body.dataset.base || '../../';

  let bundle, engine, galaxy, current = null;

  // ---------------------------------------------------------------------------
  // Scroll reveal
  // ---------------------------------------------------------------------------

  const io = new IntersectionObserver(es => {
    es.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });

  function observeRises(root) { $$('.rise', root || document).forEach(el => io.observe(el)); }

  // Count-up. Numbers that arrive already at their value read as static text;
  // counting makes them feel measured.
  function countUp(el, to, dur) {
    const start = performance.now();
    const from = 0;
    const fmt = n => n.toLocaleString();
    function step(t) {
      const k = Math.min(1, (t - start) / (dur || 1400));
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(Math.round(from + (to - from) * e));
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------

  async function boot() {
    const root = `${BASE}data/${SLUG}`;
    const [manifest, graph, index, documents, health, insights, semantic, dendrogram] = await Promise.all([
      fetch(`${root}/tenant.json`).then(r => r.json()),
      fetch(`${root}/graph.json`).then(r => r.json()),
      fetch(`${root}/index.json`).then(r => r.json()),
      fetch(`${root}/documents.json`).then(r => r.json()),
      fetch(`${root}/health.json`).then(r => r.json()),
      fetch(`${root}/insights.json`).then(r => r.json()),
      // The semantic index is the largest single payload. Failing soft here
      // means vocabulary-mismatch queries degrade to BM25 rather than the
      // whole page dying.
      fetch(`${root}/semantic.json`).then(r => r.json()).catch(() => ({ enabled: false })),
      fetch(`${root}/dendrogram.json`).then(r => r.json()).catch(() => ({ enabled: false }))
    ]);

    bundle = { manifest, graph, index, documents, health, insights, semantic, dendrogram };
    engine = new Engine(bundle);

    document.documentElement.style.setProperty('--accent', manifest.accent);
    document.documentElement.style.setProperty('--accent-rgb', hexToRgb(manifest.accent));

    initGalaxy();
    renderStats();
    renderSuggestions();
    renderHealth();
    renderInsights();
    renderDendrogram();
    renderCorpus();
    observeRises();

    $('#loading') && $('#loading').classList.add('hide');
  }

  function hexToRgb(h) {
    const n = parseInt(h.replace('#', ''), 16);
    return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
  }

  // ---------------------------------------------------------------------------
  // Galaxy
  // ---------------------------------------------------------------------------

  function initGalaxy() {
    const canvas = $('#galaxy');
    if (!canvas || !window.THREE) return;

    const tip = $('#tip');
    galaxy = new Galaxy(canvas, {
      maxNodes: window.innerWidth < 760 ? 150 : 300,
      onHover(node, ev) {
        if (!node) { tip.classList.remove('on'); return; }
        tip.innerHTML =
          `<span class="k">${esc(bundle.graph.kinds[node.kind] || node.kind)}</span>` +
          `<span class="t">${esc(node.label)}</span>` +
          `<span class="muted tiny">${node.degree} connections · ${node.docs.length} documents</span>`;
        const r = canvas.getBoundingClientRect();
        tip.style.left = Math.min(r.width - 270, ev.clientX - r.left + 14) + 'px';
        tip.style.top = Math.max(8, ev.clientY - r.top - 10) + 'px';
        tip.classList.add('on');
      },
      onSelect(node) { openEntity(node); }
    });

    galaxy.setGraph(bundle.graph, bundle.manifest.accent);
    window.addEventListener('resize', () => galaxy.resize());

    // Legend doubles as a filter — the only way to read a dense graph is to
    // subtract from it.
    const legend = $('#legend');
    legend.innerHTML = Object.entries(bundle.graph.kinds).map(([k, label]) => {
      const c = window.GALAXY_KINDS[k] || [.6, .7, .9];
      const col = `rgb(${c.map(x => Math.round(x * 255)).join(',')})`;
      return `<button class="k" data-kind="${k}" style="color:${col}">
                <i></i><span style="color:var(--ink-soft)">${esc(label)}</span>
              </button>`;
    }).join('');
    $$('.k', legend).forEach(b => {
      b.addEventListener('click', () => {
        const off = galaxy.toggleKind(b.dataset.kind);
        b.classList.toggle('off', off);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Header stats
  // ---------------------------------------------------------------------------

  function renderStats() {
    const c = bundle.manifest.counts;
    const map = {
      documents: c.documents, passages: c.passages,
      entities: c.entities, relationships: c.relationships
    };
    $$('[data-stat]').forEach(el => {
      const v = map[el.dataset.stat];
      if (v != null) countUp(el, v, 1500);
    });
  }

  function renderSuggestions() {
    const box = $('#suggest');
    box.innerHTML = bundle.manifest.questions.map(q =>
      `<button class="chip" data-q="${esc(q)}"><span class="dot"></span>${esc(q)}</button>`
    ).join('');
    $$('.chip', box).forEach(b => b.addEventListener('click', () => {
      $('#q').value = b.dataset.q;
      ask(b.dataset.q);
    }));
  }

  // ---------------------------------------------------------------------------
  // Ask
  // ---------------------------------------------------------------------------

  function ask(query) {
    const q = (query || $('#q').value || '').trim();
    if (!q) return;

    const out = $('#answer');
    out.innerHTML = `<div class="skel" style="height:14px;margin-bottom:10px"></div>
                     <div class="skel" style="height:14px;width:88%;margin-bottom:10px"></div>
                     <div class="skel" style="height:14px;width:72%"></div>`;

    $('#answerSection').scrollIntoView({ behavior: 'smooth', block: 'start' });

    // A short deliberate delay. Retrieval is instant, but an answer that
    // appears with zero latency reads as canned; a beat of work reads as
    // considered. This is the only place we fake anything, and it fakes
    // nothing about the content.
    setTimeout(() => {
      const r = engine.answer(q);
      current = r;
      renderAnswer(r);
      renderFindings(r);
      renderLineage(r);
      if (galaxy) {
        if (r.ok) {
          galaxy.illuminate(r.entities);
          // Trace the exact hops taken, so the graph is seen being walked.
          if (r.graph && r.graph.paths && r.graph.paths.length) {
            galaxy.tracePaths(r.graph.paths.slice(0, 12));
          } else {
            galaxy.clearPulses();
          }
        } else {
          galaxy.clearHighlight();
          galaxy.clearPulses();
        }
      }
    }, 340);
  }

  function renderAnswer(r) {
    const out = $('#answer');
    const meta = $('#answerMeta');

    if (!r.ok) {
      // An honest non-answer. Returning a confident-sounding paragraph
      // assembled from weak matches is the failure mode this whole design
      // exists to avoid.
      const why = {
        degenerate: 'That query is made entirely of function words, so there is no ' +
                    'retrievable intent in it. Try naming a process, a document type ' +
                    'or a standard.',
        'no-match': 'Neither the lexical nor the semantic retriever found a passage ' +
                    'above threshold. The subject may genuinely not be covered by ' +
                    'this corpus.',
        'no-coverage': 'Passages were retrieved, but none contained a sentence that ' +
                    'actually addresses the terms asked about. Quoting one anyway ' +
                    'would misrepresent the corpus.'
      }[r.reason] || 'The retrieval signal was too weak to cite a source.';

      out.innerHTML =
        `<div class="badge red mb">No grounded answer</div>
         <p>Nothing in this corpus supports an answer to
         <strong>${esc(r.query)}</strong>.</p>
         <p class="small muted">${esc(why)}</p>
         <p class="small muted" style="margin:0">This is a deliberate non-answer.
         Assembling a confident-sounding paragraph from loosely related passages
         is the failure mode this design exists to prevent.</p>`;
      meta.innerHTML = '';
      return;
    }

    out.innerHTML =
      `<div class="answer-body">` +
      r.sentences.map((s, i) =>
        `<p>${esc(s.text)} <button class="cite" data-c="${i}">${i + 1}</button></p>`
      ).join('') +
      `</div>` +
      `<div class="mt" style="border-top:1px solid var(--line);padding-top:1rem">
         <div class="eyebrow" style="margin-bottom:.7rem">Sources</div>
         ${r.citations.map(c => `
           <button class="chip" data-open="${esc(c.doc.id)}" data-hl="${esc(c.passage.id)}"
                   style="width:100%;justify-content:flex-start;text-align:left;margin-bottom:.4rem">
             <span class="mono tiny" style="color:var(--qz-blue-lift)">${c.n}</span>
             <span style="flex:1">
               <b style="display:block;color:var(--ink)">${esc(c.doc.title)}</b>
               <span class="tiny muted mono">${esc(c.doc.id)} · ${esc(c.passage.section)} ¶${c.passage.para} · ${esc(c.doc.unit)}</span>
             </span>
           </button>`).join('')}
       </div>`;

    const band = r.confidence >= 70 ? 'good' : r.confidence >= 45 ? '' : 'warm';
    const d = r.run.diagnostics;
    const modeLabel = { hybrid: 'Lexical + semantic', lexical: 'Lexical only',
                        semantic: 'Semantic only' }[r.run.mode] || r.run.mode;

    meta.innerHTML =
      `<div class="row between mb">
         <span class="eyebrow" style="margin:0">Confidence</span>
         <span class="mono" style="color:var(--ink);font-size:1.1rem">${r.confidence}%</span>
       </div>
       <div class="meter ${band}"><i style="width:0"></i></div>
       <p class="tiny muted" style="margin:.7rem 0 0">
         Weighted geometric mean of five measured signals. Conjunctive, so a
         single weak signal pulls the whole score down rather than averaging out.
       </p>

       <div class="mt" style="border-top:1px solid var(--line);padding-top:1rem">
         <div class="eyebrow" style="margin-bottom:.9rem">Signal breakdown</div>
         ${r.signals.parts.map(p => `
           <div style="margin-bottom:.85rem" title="${esc(p.why)}">
             <div class="row between" style="margin-bottom:.3rem">
               <span class="small">${esc(p.label)}</span>
               <span class="mono tiny muted">${p.pct}% · w${p.weight}</span>
             </div>
             <div class="meter" style="height:5px"><i data-w="${p.pct}" style="width:0"></i></div>
           </div>`).join('')}
       </div>

       <div class="mt" style="border-top:1px solid var(--line);padding-top:1rem">
         <div class="eyebrow" style="margin-bottom:.7rem">Retrieval</div>
         <div class="row" style="gap:1.4rem;flex-wrap:wrap">
           <div><div class="mono tiny muted">MODE</div><b class="small">${esc(modeLabel)}</b></div>
           <div><div class="mono tiny muted">LATENCY</div><b class="small">${r.run.latencyMs} ms</b></div>
           <div><div class="mono tiny muted">BM25</div><b class="small">${d.lexicalHits}</b></div>
           <div><div class="mono tiny muted">LSA</div><b class="small">${d.semanticHits}</b></div>
           <div><div class="mono tiny muted">AGREEMENT</div><b class="small">${Math.round(d.agreement*100)}%</b></div>
           <div><div class="mono tiny muted">SOURCES</div><b class="small">${r.sources}</b></div>
         </div>
       </div>

       <div class="mt" style="border-top:1px solid var(--line);padding-top:1rem">
         <div class="eyebrow" style="margin-bottom:.7rem">Coverage</div>
         <div class="cloud tiny">
           ${[...new Set(r.sentences.map(s => s.doc.unit))].map(u => `<span>${esc(u)}</span>`).join('')}
         </div>
         <div class="cloud tiny" style="margin-top:.5rem">
           ${[...new Set(r.sentences.map(s => s.doc.authority))].map(a => `<span class="mono">${esc(a)}</span>`).join('')}
         </div>
       </div>`;

    requestAnimationFrame(() => {
      const bar = $('i', $('.meter', meta));
      if (bar) bar.style.width = r.confidence + '%';
      $$('i[data-w]', meta).forEach(i => { i.style.width = i.dataset.w + '%'; });
    });

    $$('[data-c]', out).forEach(b => b.addEventListener('click', () => {
      const c = r.citations[+b.dataset.c];
      openDoc(c.doc.id, c.passage.id);
    }));
    $$('[data-open]', out).forEach(b => b.addEventListener('click', () =>
      openDoc(b.dataset.open, b.dataset.hl)));
  }

  function scoreRow(label, pct, note) {
    return `<div style="margin-bottom:.7rem">
      <div class="row between" style="margin-bottom:.3rem">
        <span class="small">${esc(label)}</span>
        <span class="mono tiny muted">${pct}% · ${esc(note)}</span>
      </div>
      <div class="meter" style="height:5px"><i style="width:${pct}%"></i></div>
    </div>`;
  }

  /* The graph's actual contribution: entities resolved by traversal that no
     single document lists. Rendered separately from quotations because it is a
     different KIND of answer — derived rather than quoted. */
  function renderFindings(r) {
    const box = $('#findings');
    if (!box) return;
    const g = r.ok && r.graph;
    if (!g || !g.findingCount) {
      box.innerHTML = `<p class="muted small">This question named no entity the
        graph could traverse from, so retrieval alone answered it. Try naming a
        specific identifier, or ask what something is connected to.</p>`;
      return;
    }
    box.innerHTML =
      `<p class="small" style="color:var(--ink)">
         Traversal resolved <strong>${g.findingCount}</strong> connected entities from
         <strong>${g.seeds.length || g.seedIds.length}</strong> starting point(s).
         These are assembled by joining relationships across documents — no single
         document contains this list.
       </p>
       <div class="grid g2" style="margin-top:1.2rem">
       ${g.findings.map(f => `
         <div class="card"><div class="card-pad">
           <div class="row between mb">
             <h3 style="font-size:.95rem">${esc(f.label)}</h3>
             <span class="badge">${f.total}</span>
           </div>
           <div class="cloud tiny">
             ${f.items.map(i => `<span class="mono" data-ent="${esc(i.id)}"
                title="${esc(i.path)}" style="cursor:pointer">${esc(i.ref || i.label)}</span>`).join('')}
           </div>
           ${f.items[0] ? `<p class="tiny muted mono" style="margin:.9rem 0 0">
              ${esc(f.items[0].path)}</p>` : ''}
         </div></div>`).join('')}
       </div>`;

    $$('[data-ent]', box).forEach(el => el.addEventListener('click', () => {
      const node = bundle.graph.nodes.find(n => n.id === el.dataset.ent);
      if (node) { openEntity(node); if (galaxy) galaxy.focusNode(node.id); }
    }));
  }

  function renderLineage(r) {
    const rail = $('#lineage');
    if (!r.ok) {
      rail.innerHTML = `<p class="muted small">Ask a question to trace the retrieval chain.</p>`;
      return;
    }
    const steps = engine.lineage(r);
    rail.innerHTML = steps.map((s, i) => `
      <div class="step done rise">
        <div class="pip">${i + 1}</div>
        <div>
          <div class="mono tiny muted" style="letter-spacing:.16em;text-transform:uppercase">${esc(s.k)}</div>
          <h4>${esc(s.h)}</h4>
          <p>${esc(s.d)}</p>
        </div>
      </div>`).join('');
    observeRises(rail);
  }

  // ---------------------------------------------------------------------------
  // Health
  // ---------------------------------------------------------------------------

  /* ---------------------------------------------------------------------------
     Knowledge health.

     Five concentric arcs rather than one ring and five bars. A ring plus bars
     reads as a form; nested arcs read as a single finding, comparable at a
     glance from across a room — which is the actual viewing condition on a
     screen share. Outermost is the broadest measure, innermost the most
     specific, each drawing in on a stagger so the composition assembles.

     Cards flip on click to show the formula and the raw inputs. A score with
     no derivation is a number someone has to take on trust, and the first
     question a technical reviewer asks is how it was calculated.
     --------------------------------------------------------------------------- */

  const RING_COLOURS = {
    depth:         ['#0096FF', '#7FD8FF'],
    connectedness: ['#6D5BD0', '#B388FF'],
    traceability:  ['#F6B44C', '#FFD98A'],
    readability:   ['#F53E5A', '#FF9AAB'],
    currency:      ['#16A34A', '#7FE3B0'],
  };

  const SWEEP = 270, START = 135;

  function polar(cx, cy, r, deg) {
    const a = deg * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }

  function arcPath(cx, cy, r, start, sweep) {
    const [ax, ay] = polar(cx, cy, r, start);
    const [bx, by] = polar(cx, cy, r, start + sweep);
    return `M ${ax.toFixed(2)} ${ay.toFixed(2)} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${bx.toFixed(2)} ${by.toFixed(2)}`;
  }

  function renderRings(metrics, overall) {
    const SIZE = 300, cx = SIZE / 2, cy = SIZE / 2;
    const OUTER = 132, STEP = 19, W = 12;

    const rings = metrics.map((m, i) => {
      const r = OUTER - i * STEP;
      const pct = Math.max(0, Math.min(100, m.value)) / 100;
      const len = 2 * Math.PI * r * (SWEEP / 360);
      const [from, to] = RING_COLOURS[m.key] || ['#0096FF', '#7FD8FF'];
      return { m, r, pct, len, from, to, i };
    });

    return `
      <svg viewBox="0 0 ${SIZE} ${SIZE}" class="rings" role="img"
           aria-label="Knowledge health, five measures">
        <defs>
          ${rings.map(r => `
            <linearGradient id="rg-${r.m.key}" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="${r.from}"/>
              <stop offset="100%" stop-color="${r.to}"/>
            </linearGradient>`).join('')}
        </defs>
        ${rings.map(r => `
          <path class="ring-track" d="${arcPath(cx, cy, r.r, START, SWEEP)}"
                stroke-width="${W}"/>`).join('')}
        ${rings.map(r => `
          <path class="ring-arc" data-ring="${r.m.key}"
                d="${arcPath(cx, cy, r.r, START, SWEEP)}"
                stroke="url(#rg-${r.m.key})" stroke-width="${W}"
                stroke-dasharray="${r.len.toFixed(1)}"
                stroke-dashoffset="${r.len.toFixed(1)}"
                style="--len:${r.len.toFixed(1)};--off:${(r.len * (1 - r.pct)).toFixed(1)};--delay:${260 + r.i * 130}ms">
            <title>${esc(r.m.label)}: ${r.m.value} out of 100</title>
          </path>`).join('')}
        ${rings.map(r => {
          const [x, y] = polar(cx, cy, r.r, START + SWEEP * r.pct);
          return `<circle class="ring-cap" data-ring="${r.m.key}"
                          cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3.4"
                          fill="${r.to}"
                          style="--delay:${420 + r.i * 130}ms"/>`;
        }).join('')}
        <text x="${cx}" y="${cy - 4}" text-anchor="middle" class="rings-n">${overall}</text>
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="rings-l">READINESS</text>
      </svg>`;
  }

  function kvRows(obj) {
    return Object.entries(obj).map(([k, v]) => {
      const val = (v && typeof v === 'object') ? Object.entries(v)
        .map(([a, b]) => `${a} ${b}`).join(', ') : v;
      const label = k.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase());
      return `<div class="kv"><span>${esc(label)}</span><b>${esc(val)}</b></div>`;
    }).join('');
  }

  function renderHealth() {
    const h = bundle.health;
    if (!h || !h.metrics) return;

    $('#healthRing').innerHTML = renderRings(h.metrics, h.overall);

    $('#healthSummary').innerHTML = `
      <p class="small" style="margin:0 0 .5rem;color:var(--ink)">${esc(h.strongest || '')}</p>
      <p class="small" style="margin:0;color:var(--qz-red)">${esc(h.weakest || '')}</p>`;

    $('#healthDims').innerHTML = h.metrics.map(m => `
      <div class="flip" data-ring="${m.key}" tabindex="0" role="button"
           aria-label="${esc(m.label)}, ${m.value} out of 100. Activate to see how this is calculated.">
        <div class="flip-in">
          <div class="flip-face card">
            <div class="card-pad">
              <div class="row between">
                <h3 style="font-size:1rem">${esc(m.label)}</h3>
                <span class="metric-n" style="color:${(RING_COLOURS[m.key] || [])[0]}">${m.value}</span>
              </div>
              <div class="meter" style="margin:.9rem 0">
                <i data-w="${m.value}" style="width:0;background:linear-gradient(90deg, ${(RING_COLOURS[m.key] || ['#0096FF','#7FD8FF'])[0]}, ${(RING_COLOURS[m.key] || ['#0096FF','#7FD8FF'])[1]})"></i>
              </div>
              <p class="small" style="margin:0 0 .5rem">${esc(m.what)}</p>
              <p class="tiny muted" style="margin:0"><b>Why it matters:</b> ${esc(m.risk)}</p>
              <span class="flip-hint">How is this calculated? &rarr;</span>
            </div>
          </div>
          <div class="flip-face flip-back card">
            <div class="card-pad">
              <div class="eyebrow" style="margin-bottom:.7rem">${esc(m.label)} — derivation</div>
              <code class="formula">${esc(m.formula)}</code>
              <div class="kvs">${kvRows(m.inputs)}</div>
              <p class="tiny muted" style="margin:.9rem 0 0">${esc(m.note)}</p>
              <span class="flip-hint">&larr; Back</span>
            </div>
          </div>
        </div>
      </div>`).join('');

    $('#healthRisks').innerHTML = h.risks.map(r => `
      <div class="flip risk" tabindex="0" role="button"
           aria-label="${esc(r.label)}, ${esc(String(r.value))}. Activate to see how this is counted.">
        <div class="flip-in">
          <div class="flip-face card">
            <div class="card-pad">
              <div class="row between">
                <h3 style="font-size:.95rem">${esc(r.label)}</h3>
                <span class="metric-n" style="color:var(--qz-red)">${esc(String(r.value))}</span>
              </div>
              <p class="small" style="margin:.7rem 0 0">${esc(r.why)}</p>
              <span class="flip-hint">How is this counted? &rarr;</span>
            </div>
          </div>
          <div class="flip-face flip-back card">
            <div class="card-pad">
              <div class="eyebrow" style="margin-bottom:.7rem">How this is counted</div>
              <p class="small" style="margin:0 0 .8rem;color:var(--ink)">${esc(r.detail)}</p>
              <code class="formula">${esc(r.how)}</code>
              ${r.breakdown ? `<div class="kvs">${kvRows(r.breakdown)}</div>` : ''}
              ${r.items && r.items.length ? `<div class="cloud tiny" style="margin-top:.8rem">
                 ${r.items.slice(0, 6).map(x => `<span class="mono">${esc(x)}</span>`).join('')}
               </div>` : ''}
              <span class="flip-hint">&larr; Back</span>
            </div>
          </div>
        </div>
      </div>`).join('');

    // Flip on click or keyboard; the card is a button, so it must behave like one.
    $$('.flip').forEach(el => {
      const toggle = () => el.classList.toggle('flipped');
      el.addEventListener('click', toggle);
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    });

    // Hovering a metric card isolates its arc, tying the two views together.
    $$('#healthDims .flip').forEach(el => {
      const key = el.dataset.ring;
      el.addEventListener('pointerenter', () => {
        $$('.ring-arc, .ring-cap').forEach(a =>
          a.classList.toggle('dim', a.dataset.ring !== key));
      });
      el.addEventListener('pointerleave', () => {
        $$('.ring-arc, .ring-cap').forEach(a => a.classList.remove('dim'));
      });
    });

    // Draw the arcs when the section scrolls into view, not on load — the
    // animation is the point, and it is wasted if it plays off-screen.
    const io2 = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      $$('.ring-arc', e.target).forEach(a => { a.style.strokeDashoffset = 'var(--off)'; });
      $$('.ring-cap', e.target).forEach(c => c.classList.add('in'));
      $$('#healthDims i[data-w]').forEach(i => { i.style.width = i.dataset.w + '%'; });
      io2.unobserve(e.target);
    }), { threshold: 0.25 });
    io2.observe($('#health'));
  }

  // ---------------------------------------------------------------------------
  // Insights
  // ---------------------------------------------------------------------------

  function bars(rows, max) {
    const m = max || Math.max(...rows.map(r => r.n), 1);
    return `<div class="bars">` + rows.map(r => `
      <div class="bar-row">
        <span>${esc(r.k)}</span><span class="v">${r.n}</span>
        <span class="track"><i data-w="${(r.n / m * 100).toFixed(1)}"></i></span>
      </div>`).join('') + `</div>`;
  }

  function renderInsights() {
    const ins = bundle.insights;
    $('#insDocTypes').innerHTML = bars(ins.by_type.slice(0, 9));
    $('#insUnits').innerHTML = bars(ins.by_unit.slice(0, 10));
    $('#insHubs').innerHTML = bars(
      ins.hubs.slice(0, 10).map(h => ({ k: h.label, n: h.degree })));

    const maxC = Math.max(...ins.concepts.map(c => c.n), 1);
    $('#insConcepts').innerHTML = `<div class="cloud">` +
      ins.concepts.slice(0, 44).map(c => {
        const s = 0.76 + (c.n / maxC) * 0.72;
        const o = 0.5 + (c.n / maxC) * 0.5;
        return `<span style="font-size:${s.toFixed(2)}rem;opacity:${o.toFixed(2)}"
                      title="${c.n} passages">${esc(c.term)}</span>`;
      }).join('') + `</div>`;

    // Timeline sparkline. Drawn as an SVG path rather than bars because the
    // point is the shape of the seasonal cycle, not any individual month.
    const t = ins.timeline;
    if (t.length > 1) {
      const w = 720, hgt = 150, max = Math.max(...t.map(x => x.n), 1);
      const pts = t.map((x, i) => [
        (i / (t.length - 1)) * w,
        hgt - (x.n / max) * (hgt - 20) - 10
      ]);
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
      const area = d + ` L${w} ${hgt} L0 ${hgt} Z`;
      $('#insTimeline').innerHTML = `
        <svg viewBox="0 0 ${w} ${hgt}" style="width:100%;height:150px;overflow:visible">
          <defs>
            <linearGradient id="tlg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="var(--accent)" stop-opacity=".45"/>
              <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <path d="${area}" fill="url(#tlg)"/>
          <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2.5"
                stroke-linejoin="round" stroke-linecap="round"
                style="filter:drop-shadow(0 0 8px rgba(var(--accent-rgb),.7))"/>
          ${pts.filter((_, i) => i % 3 === 0).map(p =>
            `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3"
                     fill="var(--qz-sky)"/>`).join('')}
        </svg>
        <div class="row between tiny muted mono" style="margin-top:.4rem">
          <span>${esc(t[0].k)}</span><span>${esc(t[t.length - 1].k)}</span>
        </div>`;
    }

    const mo = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) {
        $$('i[data-w]', e.target).forEach(i => { i.style.width = i.dataset.w + '%'; });
      }
    }), { threshold: 0.15 });
    $$('#insights .panel').forEach(p => mo.observe(p));
  }

  /* ---------------------------------------------------------------------------
     Circular dendrogram.

     Every other insights panel answers "how much of X is there". This one
     answers "how does the corpus organise itself" — documents are clustered in
     concept space, so two land on the same branch because they discuss the
     same things, not because they were filed together. Branches that span
     several owning units are the interesting ones: the same subject documented
     independently in more than one place.

     Radial, because the meaningful structure is the top-level split near the
     centre, and a circle gives every leaf equal room on the rim.
     --------------------------------------------------------------------------- */
  const CLUSTER_HUES = [206, 12, 168, 268, 32, 190, 330, 96, 250];

  function clusterColour(c, l) {
    const h = CLUSTER_HUES[(c || 0) % CLUSTER_HUES.length];
    return `hsl(${h} 78% ${l || 52}%)`;
  }

  function renderDendrogram() {
    const box = $('#insDendro');
    if (!box) return;
    const d = bundle.dendrogram;
    if (!d || !d.enabled) {
      box.innerHTML = '<p class="muted small">Not enough documents to cluster.</p>';
      return;
    }

    const SIZE = 720, R = 268, CX = SIZE / 2, CY = SIZE / 2;

    // Collect leaves in traversal order; their index sets the angle.
    const leaves = [];
    (function walk(n) {
      if (n.leaf) { leaves.push(n); return; }
      n.children.forEach(walk);
    })(d.tree);
    const N = leaves.length || 1;

    // Assign polar coordinates: leaves on the rim, internal nodes at a radius
    // proportional to merge height, angled at the mean of their children.
    let maxH = 0;
    (function h(n) { maxH = Math.max(maxH, n.height || 0); if (!n.leaf) n.children.forEach(h); })(d.tree);

    let li = 0;
    (function place(n) {
      if (n.leaf) {
        n._a = (li++ + 0.5) / N * Math.PI * 2 - Math.PI / 2;
        n._r = R;
        return;
      }
      n.children.forEach(place);
      const as = n.children.map(c => c._a);
      n._a = as.reduce((x, y) => x + y, 0) / as.length;
      // Height maps inward: an early merge (similar documents) sits far out,
      // a late merge (joining unlike groups) sits near the centre.
      n._r = R * (1 - Math.min(1, (n.height || 0) / (maxH || 1)) * 0.92);
    })(d.tree);

    const pol = (r, a) => [CX + r * Math.cos(a), CY + r * Math.sin(a)];

    // Elbow links: radial segment then an arc, which is what makes a radial
    // dendrogram legible as a tree rather than a starburst of straight lines.
    const paths = [];
    (function link(n) {
      if (n.leaf) return;
      for (const c of n.children) {
        const [x1, y1] = pol(n._r, c._a);
        const [x2, y2] = pol(c._r, c._a);
        const [ax, ay] = pol(n._r, n._a);
        const sweep = ((c._a - n._a) + Math.PI * 2) % (Math.PI * 2) > Math.PI ? 0 : 1;
        const cl = c.cluster || n.cluster || 0;
        paths.push(
          `<path d="M${ax.toFixed(1)} ${ay.toFixed(1)} A${n._r.toFixed(1)} ${n._r.toFixed(1)} 0 0 ${sweep} ${x1.toFixed(1)} ${y1.toFixed(1)} L${x2.toFixed(1)} ${y2.toFixed(1)}"
                 fill="none" stroke="${clusterColour(cl, cl ? 62 : 82)}"
                 stroke-width="${cl ? 1.7 : 1.1}" stroke-linecap="round" opacity="${cl ? 0.85 : 0.5}"/>`);
        link(c);
      }
    })(d.tree);

    const leafMarks = leaves.map(l => {
      const [x, y] = pol(R, l._a);
      const deg = l._a * 180 / Math.PI;
      const flip = deg > 90 || deg < -90;
      const [tx, ty] = pol(R + 10, l._a);
      const name = l.name.length > 26 ? l.name.slice(0, 25) + '\u2026' : l.name;
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${l.collapsed ? 4.4 : 3}"
                      fill="${clusterColour(l.cluster, 54)}"/>
        <text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}"
              transform="rotate(${(flip ? deg + 180 : deg).toFixed(1)} ${tx.toFixed(1)} ${ty.toFixed(1)})"
              text-anchor="${flip ? 'end' : 'start'}" dominant-baseline="middle"
              font-size="9.5" font-family="var(--f-body)" fill="var(--ink-mute)">${esc(name)}</text>`;
    }).join('');

    box.innerHTML = `
      <div class="dendro-wrap">
        <svg viewBox="0 0 ${SIZE} ${SIZE}" class="dendro" role="img"
             aria-label="Circular dendrogram of document clusters">
          <circle cx="${CX}" cy="${CY}" r="${R}" fill="none"
                  stroke="var(--line)" stroke-dasharray="2 6"/>
          ${paths.join('')}
          ${leafMarks}
          <circle cx="${CX}" cy="${CY}" r="4" fill="var(--ink-faint)"/>
        </svg>
        <div class="dendro-key">
          <div class="eyebrow" style="margin-bottom:.8rem">Clusters</div>
          <p class="tiny muted" style="margin:0 0 1rem">
            ${d.documents} documents clustered by Ward linkage in concept space.
            Documents sit on the same branch because they discuss the same
            things — not because they were filed together.
          </p>
          ${d.clusters.map(c => `
            <div class="dendro-cl">
              <span class="swatch" style="background:${clusterColour(c.cluster, 54)}"></span>
              <div>
                <b>${c.n} documents</b>
                ${c.spread > 2 ? `<span class="badge red" style="margin-left:.4rem">spans ${c.spread} units</span>` : ''}
                <div class="tiny muted">${esc(c.subjects.slice(0, 3).join(' · '))}</div>
              </div>
            </div>`).join('')}
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------------------
  // Corpus browser
  // ---------------------------------------------------------------------------

  function renderCorpus(filter) {
    const list = $('#corpusList');
    const q = (filter || '').toLowerCase();
    const rows = bundle.documents.filter(d =>
      !q || d.title.toLowerCase().includes(q) || d.id.toLowerCase().includes(q) ||
      d.unit.toLowerCase().includes(q) || d.type.toLowerCase().includes(q));

    $('#corpusCount').textContent = `${rows.length} of ${bundle.documents.length}`;
    list.innerHTML = rows.slice(0, 60).map(d => `
      <button class="card tilt" data-open="${esc(d.id)}"
              style="text-align:left;border:1px solid var(--line);cursor:pointer;width:100%">
        <div class="sheen"></div>
        <div class="card-pad">
          <div class="row between" style="align-items:flex-start">
            <span class="badge">${esc(d.abbrev)}</span>
            <span class="mono tiny muted">${esc(d.effective)}</span>
          </div>
          <h3 style="font-size:1rem;margin:.7rem 0 .4rem">${esc(d.title)}</h3>
          <div class="tiny muted mono">${esc(d.id)} · rev ${esc(d.revision)}</div>
          <div class="tiny muted" style="margin-top:.5rem">${esc(d.unit)} · ${esc(d.authority)}</div>
        </div>
      </button>`).join('');
    $$('[data-open]', list).forEach(b =>
      b.addEventListener('click', () => openDoc(b.dataset.open)));
    bindTilt(list);
  }

  // ---------------------------------------------------------------------------
  // Document sheet
  // ---------------------------------------------------------------------------

  /* The viewer reconstructs a document from the passage index rather than
     fetching a markdown file. The index already holds every paragraph with its
     section and ordinal, so shipping the corpus twice bought nothing. */
  function openDoc(id, highlightPassageId) {
    const d = bundle.documents.find(x => x.id === id);
    if (!d) return;
    const sheet = $('#sheet');
    $('#sheetTitle').textContent = d.title;
    $('#sheetMeta').innerHTML =
      `<span class="mono tiny muted">${esc(d.id)} · rev ${esc(d.revision)} · ` +
      `${esc(d.unit)} · ${esc(d.classification)}</span>`;

    const control = [
      ['Document ID', d.id], ['Type', d.type], ['Revision', d.revision],
      ['Effective', d.effective], ['Next review', d.review],
      ['Owning unit', d.unit], ['Owner', `${d.owner}, ${d.owner_role}`],
      ['Authority', d.authority], ['System of record', d.system],
      ['Classification', d.classification], ['Site', d.site],
    ];

    const sections = engine.documentText(id);
    $('#sheetBody').innerHTML =
      `<table style="width:100%;border-collapse:collapse;margin:0 0 1.6rem;font-size:.84rem">
         ${control.map(([k, v]) => `<tr>
           <td style="padding:.4rem .6rem;border-bottom:1px solid var(--line);color:var(--ink-mute);
                      font-family:var(--f-mono);font-size:.72rem;letter-spacing:.06em;
                      text-transform:uppercase;width:38%">${esc(k)}</td>
           <td style="padding:.4rem .6rem;border-bottom:1px solid var(--line);color:var(--ink)">${esc(v)}</td>
         </tr>`).join('')}
       </table>
       ${sections.map(sec => `
         <h3 style="margin:1.7rem 0 .7rem;color:var(--qz-blue-lift)">${sec.no}. ${esc(sec.section)}</h3>
         ${sec.paras.map(p => `<p${p.id === highlightPassageId ? ' id="hl"' : ''}
            style="${p.id === highlightPassageId
              ? 'background:rgba(255,51,0,.18);border-left:2px solid var(--qz-red);padding:.6rem .8rem;border-radius:4px'
              : ''}">${inlineMd(p.text)}</p>`).join('')}
       `).join('')}
       <blockquote style="border-left:2px solid var(--qz-red);padding-left:1rem;margin:1.6rem 0 0;
                          color:var(--ink-mute);font-size:.82rem">
         Synthetic document. ${esc(bundle.manifest.tenant)} is a fictional organisation;
         all names, identifiers and events are invented.
       </blockquote>`;

    sheet.classList.add('on');
    document.body.style.overflow = 'hidden';
    const hl = $('#hl');
    if (hl) setTimeout(() => hl.scrollIntoView({ behavior: 'smooth', block: 'center' }), 140);
  }

  function closeSheet() {
    $('#sheet').classList.remove('on');
    document.body.style.overflow = '';
  }

  /* A deliberately small Markdown subset. The corpus only ever emits headings,
     paragraphs, pipe tables, blockquotes, list items and inline code, so a
     full parser would be 40KB of dependency to handle syntax we never write. */
  function mdToHtml(md) {
    const lines = md.split('\n');
    const out = [];
    let inTable = false, para = [];

    const flush = () => {
      if (para.length) {
        out.push('<p>' + inline(para.join(' ')) + '</p>');
        para = [];
      }
    };
    const inline = s => esc(s)
      .replace(/`([^`]+)`/g, '<code class="mono">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    for (const raw of lines) {
      const l = raw.trim();
      if (/^\|/.test(l)) {
        if (/^\|[\s\-|:]+\|$/.test(l)) continue;
        const cells = l.split('|').slice(1, -1).map(c => inline(c.trim()));
        if (!inTable) {
          flush();
          out.push('<table style="width:100%;border-collapse:collapse;margin:1rem 0;font-size:.86rem">');
          out.push('<tr>' + cells.map(c =>
            `<th style="text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--line-hot);color:var(--ink);font-family:var(--f-mono);font-size:.7rem;letter-spacing:.1em;text-transform:uppercase">${c}</th>`).join('') + '</tr>');
          inTable = true;
        } else {
          out.push('<tr>' + cells.map(c =>
            `<td style="padding:.45rem .6rem;border-bottom:1px solid var(--line);color:var(--ink-soft)">${c}</td>`).join('') + '</tr>');
        }
        continue;
      }
      if (inTable) { out.push('</table>'); inTable = false; }

      if (!l) { flush(); continue; }
      if (l.startsWith('#### ')) { flush(); out.push(`<h4 style="margin:1.2rem 0 .5rem">${inline(l.slice(5))}</h4>`); }
      else if (l.startsWith('### ')) { flush(); out.push(`<h3 style="margin:1.4rem 0 .6rem">${inline(l.slice(4))}</h3>`); }
      else if (l.startsWith('## ')) { flush(); out.push(`<h3 style="margin:1.7rem 0 .7rem;color:var(--qz-blue-lift)">${inline(l.slice(3))}</h3>`); }
      else if (l.startsWith('# ')) { flush(); out.push(`<h2 style="margin:0 0 1rem">${inline(l.slice(2))}</h2>`); }
      else if (l.startsWith('> ')) { flush(); out.push(`<blockquote style="border-left:2px solid var(--qz-red);padding-left:1rem;margin:1.2rem 0;color:var(--ink-mute);font-size:.85rem">${inline(l.slice(2))}</blockquote>`); }
      else if (l.startsWith('- ')) { flush(); out.push(`<div style="padding-left:1.1rem;position:relative;margin:.3rem 0"><span style="position:absolute;left:0;color:var(--accent)">•</span>${inline(l.slice(2))}</div>`); }
      else para.push(l);
    }
    flush();
    if (inTable) out.push('</table>');
    return out.join('\n');
  }

  // ---------------------------------------------------------------------------
  // Entity drill-in
  // ---------------------------------------------------------------------------

  function openEntity(node) {
    const sheet = $('#sheet');
    $('#sheetTitle').textContent = node.label;
    $('#sheetMeta').innerHTML =
      `<span class="mono tiny muted">${esc(bundle.graph.kinds[node.kind] || node.kind)} · ` +
      `${node.degree} connections</span>`;

    const docs = node.docs.map(id => bundle.documents.find(d => d.id === id)).filter(Boolean);
    const rels = bundle.graph.edges
      .filter(e => e.s === node.id || e.t === node.id)
      .slice(0, 14);
    const label = id => {
      const n = bundle.graph.nodes.find(x => x.id === id);
      return n ? n.label : id;
    };

    $('#sheetBody').innerHTML = `
      <div class="eyebrow">Relationships</div>
      <div class="cloud mb">
        ${rels.map(e => `<span class="tiny mono">${esc(label(e.s))}
          <span style="color:var(--qz-red-warm)">—${esc(e.rel)}→</span>
          ${esc(label(e.t))}</span>`).join('')}
      </div>
      <div class="eyebrow" style="margin-top:1.6rem">Documents (${docs.length})</div>
      ${docs.slice(0, 20).map(d => `
        <button class="chip" data-open="${esc(d.id)}"
                style="width:100%;justify-content:flex-start;text-align:left;margin-bottom:.4rem">
          <span style="flex:1"><b style="display:block;color:var(--ink)">${esc(d.title)}</b>
          <span class="tiny muted mono">${esc(d.id)} · ${esc(d.unit)}</span></span>
        </button>`).join('') || '<p class="muted small">No documents attached.</p>'}`;

    $$('[data-open]', $('#sheetBody')).forEach(b =>
      b.addEventListener('click', () => openDoc(b.dataset.open)));

    sheet.classList.add('on');
    document.body.style.overflow = 'hidden';
  }

  // ---------------------------------------------------------------------------
  // Pointer-tracked card sheen
  // ---------------------------------------------------------------------------

  /* Passage text carries inline code spans from the source document. Render
     them rather than leaking backticks into the viewer. */
  function inlineMd(t) {
    return esc(t)
      .replace(/`([^`]+)`/g, '<code class="mono" style="background:rgba(255,255,255,.07);' +
               'padding:.08rem .32rem;border-radius:4px;color:var(--qz-mist)">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  }

  function bindTilt(root) {
    $$('.tilt', root || document).forEach(el => {
      if (el._tilt) return;
      el._tilt = true;
      el.addEventListener('pointermove', e => {
        const r = el.getBoundingClientRect();
        el.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100) + '%');
        el.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100) + '%');
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Wire up
  // ---------------------------------------------------------------------------

  document.addEventListener('DOMContentLoaded', () => {
    $('#askBtn') && $('#askBtn').addEventListener('click', () => ask());
    $('#q') && $('#q').addEventListener('keydown', e => {
      if (e.key === 'Enter') ask();
    });
    $('#sheetClose') && $('#sheetClose').addEventListener('click', closeSheet);
    $('#sheet') && $('#sheet').addEventListener('click', e => {
      if (e.target.id === 'sheet') closeSheet();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeSheet();
    });

    $('#corpusSearch') && $('#corpusSearch').addEventListener('input', e =>
      renderCorpus(e.target.value));

    $$('.tabs').forEach(t => {
      $$('button', t).forEach(b => b.addEventListener('click', () => {
        $$('button', t).forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        const group = t.dataset.tabs;
        $$(`[data-panel-group="${group}"]`).forEach(p =>
          p.classList.toggle('on', p.dataset.panel === b.dataset.tab));
      }));
    });

    bindTilt();
    boot().catch(err => {
      console.error(err);
      const l = $('#loading');
      if (l) l.innerHTML =
        `<p class="muted">Could not load this tenant's fabric. Run
         <code class="mono">python -m pipeline.build_tenants</code> then
         <code class="mono">python -m pipeline.build_site</code>.</p>`;
    });
  });
})();
