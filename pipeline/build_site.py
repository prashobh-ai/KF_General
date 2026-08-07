"""Assemble the static site.

    python -m pipeline.build_site

Routes produced:

    site/index.html                the landing page, all eleven tenants
    site/demo/<slug>/index.html    one tenant demonstration
    site/data/<slug>/*.json        that tenant's fabric artefacts
    site/data/<slug>/docs/*.md     the corpus, fetched by the document viewer

The route segment is `demo`, not `t` — a URL is part of the interface, and
`/demo/q-airlines/` tells a reader what they are about to open while `/t/…`
tells them nothing.

Everything under site/ except assets/ is generated. Do not hand-edit.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
TENANTS = ROOT / "tenants"

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Sora:wght@400;600;700;800&'
    'family=Manrope:wght@400;500;600;700;800&'
    'family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
)

# The QualiZeal mark, rebuilt as inline SVG: the blue ring with the red
# ascending stroke through it. Inline so it renders before any network round
# trip and inherits currentColor in the dark theme.
# The authentic QualiZeal mark, extracted from ppt/media in the delivery deck
# rather than approximated in SVG. The hand-drawn version had the wrong ring
# geometry and both brand colours wrong.
def logo(base: str = "", size: int = 30) -> str:
    return (f'<img src="{base}assets/brand/qualizeal-icon.png" alt="QualiZeal" '
            f'height="{size}" width="{size}" decoding="async">')


FIELD = ('<div class="field"><span class="drift a"></span>'
         '<span class="drift b"></span><span class="drift c"></span></div>')


def head(title: str, desc: str, base: str, accent: str = "#0B66E1") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<meta name="theme-color" content="#FFFFFF">
<link rel="icon" href="{base}assets/brand/favicon.png" type="image/png">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:type" content="website">
{FONTS}
<link rel="stylesheet" href="{base}assets/css/fabric.css">
<style>:root{{--accent:{accent}}}</style>
</head>
<body>
{FIELD}
"""


def topbar(base: str, links: list[tuple[str, str]], active: str = "") -> str:
    nav = "".join(
        f'<a href="{h}"{" class=\'on\'" if t == active else ""}>{html.escape(t)}</a>'
        for t, h in links
    )
    return f"""
<header class="topbar">
  <div class="wrap topbar-in">
    <a class="brand" href="{base}index.html">
      {logo(base, 32)}
      <span class="brand-text">
        <span class="brand-wordmark">QUALI<em>ZEAL</em></span>
        <span class="sub">Knowledge Fabric · AI Center of Excellence</span>
      </span>
    </a>
    <nav class="navlinks">{nav}</nav>
  </div>
</header>
"""


FOOTER_TPL = """
<footer>
  <div class="wrap">
    <div class="row between" style="align-items:flex-start;gap:2rem">
      <div>
        <div class="row" style="gap:.6rem;margin-bottom:.6rem">%LOGO%
          <strong class="brand-wordmark" style="font-size:1rem">QUALI<em>ZEAL</em></strong></div>
        <p class="note">Every tenant, document, person and identifier in this
        demonstration is fictional. Standards, code systems and regulations are
        cited as public reference. Identifiers are drawn from ranges reserved
        for documentation and testing, so they are well-formed and cannot
        resolve to a real record.</p>
      </div>
      <div class="tiny muted mono" style="text-align:right">
        QualiZeal AI Center of Excellence<br>Synthetic demonstration build
      </div>
    </div>
  </div>
</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

def build_index(registry: dict) -> str:
    t = registry["totals"]
    tiles = []
    for x in registry["tenants"]:
        c = x["counts"]
        rgb = tuple(int(x["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        tiles.append(f"""
      <a class="card tile tilt rise" href="demo/{x['slug']}/"
         style="--accent:{x['accent']};--accent-rgb:{rgb[0]},{rgb[1]},{rgb[2]}">
        <div class="sheen"></div>
        <div class="card-pad">
          <div class="top">
            <img class="tile-logo" src="assets/brand/{x['slug']}-lockup.png"
                 alt="{html.escape(x['slug'])}" loading="lazy" decoding="async">
          </div>
          <span class="ind">{html.escape(x['industry'])}</span>
          <span class="nm">{html.escape(x['tenant'])}</span>
          <p class="small muted" style="margin:0">{html.escape(x['tagline'])}</p>
          <div class="facts">
            <div><b>{c['documents']}</b>documents</div>
            <div><b>{c['entities']}</b>entities</div>
            <div><b>{c['relationships']}</b>edges</div>
            <div><b>{x['health']}</b>health</div>
          </div>
          <div class="go">Open demonstration
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
              <path d="M5 12h13M13 6l6 6-6 6"/></svg>
          </div>
        </div>
      </a>""")

    links = [("Domains", "#domains"), ("How it works", "#how"),
             ("Provenance", "#provenance")]

    return head(
        "Knowledge Fabric · Enterprise Knowledge Intelligence",
        "Eleven industry knowledge fabrics. Citation-grounded answers, a live "
        "entity graph, and knowledge health measured directly from the corpus.",
        "", "#0B66E1"
    ) + topbar("", links) + f"""
<main>

  <section style="padding-top:clamp(3rem,7vh,6rem)">
    <div class="wrap">
      <div class="grid" style="grid-template-columns:minmax(320px,1.02fr) minmax(320px,1fr);align-items:center;gap:3.2rem">
        <div>
          <div class="eyebrow rise">Eleven industries · one fabric</div>
          <h1 class="rise" style="font-size:clamp(2.3rem,4.4vw,3.9rem)">Every answer,<br><span class="grad">traced to the page it came from.</span></h1>
          <p class="lede rise" style="margin-top:1.5rem">
            Knowledge Fabric indexes an enterprise corpus down to the paragraph,
            builds an entity graph from the documents themselves, and answers
            questions by quoting sources verbatim — never by paraphrasing them.
            Open any of the eleven domains below and interrogate a full
            synthetic corpus.
          </p>
          <div class="row rise" style="margin-top:2rem">
            <a class="btn" href="#domains">
              Explore the domains
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                <path d="M12 5v13M6 13l6 6 6-6"/></svg>
            </a>
            <a class="btn btn-ghost" href="#how">How it works</a>
          </div>
          <div class="grid g4 rise" style="margin-top:3rem;gap:1.6rem">
            <div class="stat"><div class="n" data-to="{t['documents']}">0</div><div class="l">Documents</div></div>
            <div class="stat"><div class="n" data-to="{t['passages']}">0</div><div class="l">Cited passages</div></div>
            <div class="stat"><div class="n" data-to="{t['entities']}">0</div><div class="l">Entities</div></div>
            <div class="stat"><div class="n" data-to="{t['relationships']}">0</div><div class="l">Relationships</div></div>
          </div>
        </div>

        <div class="stage rise" style="aspect-ratio:1/.92;min-height:400px">
          <canvas id="heroGalaxy"></canvas>
          <div class="stage-ui">
            <div class="stage-hint">Drag to orbit · scroll to zoom</div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section id="domains">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">The domains</div>
        <h2 class="rise">Eleven corpora, built from how each industry
          actually documents itself.</h2>
        <p class="lede rise" style="margin-top:1rem">
          Each tenant is fictional, but its scaffolding is not. Document types,
          code systems, organisational units, approval chains and identifier
          formats are drawn from the real standards that govern that industry —
          ATA chapters, HL7 FHIR, X12, PCAOB, MARPOL, GS1, ISO 29119.
        </p>
      </div>
      <div class="grid g3">{''.join(tiles)}</div>
    </div>
  </section>

  <section id="how">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">How it works</div>
        <h2 class="rise">No model writes the answer.</h2>
        <p class="lede rise" style="margin-top:1rem">
          The demonstration runs entirely from static files. That constraint is
          the point: with no generation step, there is nothing that can invent a
          citation. Every sentence in an answer is lifted unmodified from an
          indexed paragraph, and the citation resolves to that paragraph.
        </p>
      </div>
      <div class="grid g4">
        <div class="card tilt rise"><div class="sheen"></div><div class="card-pad">
          <span class="badge">01</span>
          <h3 style="margin:.9rem 0 .5rem">Index to the paragraph</h3>
          <p class="small">Documents are split into addressable passages —
          document, section, paragraph — so a citation points somewhere
          specific enough to verify in seconds.</p>
        </div></div>
        <div class="card tilt rise"><div class="sheen"></div><div class="card-pad">
          <span class="badge">02</span>
          <h3 style="margin:.9rem 0 .5rem">Derive the graph</h3>
          <p class="small">Entities and relationships come from document
          structure, not inference. Every edge traces to a field in a specific
          document, so "why are these connected?" has a real answer.</p>
        </div></div>
        <div class="card tilt rise"><div class="sheen"></div><div class="card-pad">
          <span class="badge">03</span>
          <h3 style="margin:.9rem 0 .5rem">Quote, never paraphrase</h3>
          <p class="small">Ranking blends lexical score, field weighting and
          graph activation. The answer is assembled from the source sentences
          themselves.</p>
        </div></div>
        <div class="card tilt rise"><div class="sheen"></div><div class="card-pad">
          <span class="badge red">04</span>
          <h3 style="margin:.9rem 0 .5rem">Decline when weak</h3>
          <p class="small">Below the confidence threshold the system returns an
          explicit non-answer. A plausible paragraph built from weak matches is
          worse than nothing.</p>
        </div></div>
      </div>
    </div>
  </section>

  <section id="provenance">
    <div class="wrap-narrow">
      <div class="card rise"><div class="card-pad" style="padding:2.2rem">
        <div class="eyebrow">Provenance and safety</div>
        <h2 style="font-size:clamp(1.5rem,2.6vw,2.1rem)">Synthetic by construction,
          realistic by scaffolding.</h2>
        <p style="margin-top:1.2rem">
          No client data was used to build any part of this. Company names,
          people, sites, dates and events are invented. What is real is the
          structure: the standards each industry is governed by, the document
          types it produces, and the shape of its identifiers.
        </p>
        <p>
          Identifiers are generated from ranges the issuing authorities reserve
          for documentation and testing — RFC 5737 address blocks, RFC 2606
          domains, ISO 3166 user-assigned country codes, ICAO
          <code class="mono">ZZZZ</code>, GS1 restricted-circulation prefixes,
          never-issued number ranges. They pass check-digit validation and
          cannot resolve to a real record.
        </p>
        <p style="margin:0">
          Corpora are also statistically shaped rather than uniformly random:
          document volume follows each industry's seasonal cycle, code frequency
          follows a long tail, and revision dates decay toward the present the
          way a live document set does.
        </p>
      </div></div>
    </div>
  </section>

</main>
{FOOTER_TPL.replace('%LOGO%', logo('', 24)).replace('</body></html>', '')}
<script src="assets/vendor/three.min.js"></script>
<script src="assets/js/galaxy.js"></script>
<script>
(function(){{
  var io = new IntersectionObserver(function(es){{
    es.forEach(function(e){{ if(e.isIntersecting){{ e.target.classList.add('in'); io.unobserve(e.target); }} }});
  }}, {{threshold:.12, rootMargin:'0px 0px -8% 0px'}});
  document.querySelectorAll('.rise').forEach(function(el){{ io.observe(el); }});

  document.querySelectorAll('.tilt').forEach(function(el){{
    el.addEventListener('pointermove', function(e){{
      var r = el.getBoundingClientRect();
      el.style.setProperty('--mx', ((e.clientX-r.left)/r.width*100)+'%');
      el.style.setProperty('--my', ((e.clientY-r.top)/r.height*100)+'%');
    }});
  }});

  var co = new IntersectionObserver(function(es){{
    es.forEach(function(e){{
      if(!e.isIntersecting) return;
      var el = e.target, to = +el.dataset.to, t0 = performance.now();
      (function step(t){{
        var k = Math.min(1,(t-t0)/1600), v = 1-Math.pow(1-k,3);
        el.textContent = Math.round(to*v).toLocaleString();
        if(k<1) requestAnimationFrame(step);
      }})(t0);
      co.unobserve(el);
    }});
  }}, {{threshold:.5}});
  document.querySelectorAll('[data-to]').forEach(function(el){{ co.observe(el); }});

  // Hero graph: a composite built from every tenant's hub entities, so the
  // landing page shows the whole fabric rather than one domain's slice.
  fetch('data/overview.json').then(function(r){{return r.json();}}).then(function(g){{
    if(!window.THREE) return;
    var gx = new Galaxy(document.getElementById('heroGalaxy'), {{
      maxNodes: window.innerWidth < 760 ? 220 : 420, autorotate: 0.0009
    }});
    gx.setGraph(g, '#0B66E1');
    window.addEventListener('resize', function(){{ gx.resize(); }});
  }});
}})();
</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Tenant demonstration page
# ---------------------------------------------------------------------------

def build_demo(m: dict) -> str:
    slug = m["slug"]
    rgb = tuple(int(m["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    base = "../../"
    links = [("Graph", "#graph"), ("Answer", "#answerSection"),
             ("Findings", "#findingsSection"),
             ("Lineage", "#lineageSection"), ("Health", "#health"),
             ("Insights", "#insights"), ("Corpus", "#corpus")]

    doc_types = "".join(
        f'<span class="tiny mono">{html.escape(d["abbrev"] or d["name"])}</span>'
        for d in m["doc_types"])
    code_sys = "".join(
        f"""<div class="card tilt rise"><div class="sheen"></div><div class="card-pad">
              <div class="row between"><h3 style="font-size:1rem">{html.escape(c['name'])}</h3>
              <span class="badge">{len(c['codes'])}</span></div>
              <p class="tiny muted mono" style="margin:.5rem 0 .8rem">{html.escape(c['authority'])} · {html.escape(c['fmt'])}</p>
              <div class="cloud tiny">{''.join(f'<span class="mono" title="{html.escape(x["m"])}">{html.escape(x["c"])}</span>' for x in c['codes'][:12])}</div>
            </div></div>"""
        for c in m["code_systems"])
    flows = "".join(
        f"""<div class="card rise"><div class="card-pad">
              <h3 style="font-size:1rem;margin-bottom:.9rem">{html.escape(w['name'])}</h3>
              <div class="row" style="gap:.35rem">
              {''.join(f'<span class="chip tiny" style="pointer-events:none">{html.escape(s)}</span>' for s in w['states'])}
              </div></div></div>"""
        for w in m["workflows"])

    return head(
        f"{m['tenant']} · Knowledge Fabric",
        f"{m['industry']} knowledge fabric — {m['counts']['documents']} documents, "
        f"citation-grounded answers and a live entity graph.",
        base, m["accent"]
    ) + f"""<style>:root{{--accent:{m['accent']};--accent-rgb:{rgb[0]},{rgb[1]},{rgb[2]}}}</style>
""" + topbar(base, links) + f"""
<body-marker>
<main data-tenant="{slug}">

  <section style="padding-top:clamp(2.4rem,5vh,4rem);padding-bottom:2rem">
    <div class="wrap">
      <div class="row between rise" style="margin-bottom:1.6rem">
        <div class="row" style="gap:1.4rem;align-items:center">
          <img class="tenant-lockup" src="{base}assets/brand/{slug}-lockup.png"
               alt="{html.escape(m['tenant'])}" loading="eager" decoding="async">
          <div>
            <div class="eyebrow" style="margin-bottom:.7rem">{html.escape(m['industry'])}</div>
            <h1 style="font-size:clamp(1.7rem,3.6vw,2.9rem)">{html.escape(m['tenant'])}</h1>
            <p class="small muted" style="margin-top:.5rem">{html.escape(m['tagline'])}</p>
          </div>
        </div>
        <a class="btn btn-ghost btn-sm" href="{base}index.html">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2.4" stroke-linecap="round"><path d="M19 12H6M11 6l-6 6 6 6"/></svg>
          All domains</a>
      </div>

      <div class="grid g4 rise" style="margin-bottom:2.2rem">
        <div class="stat"><div class="n" data-stat="documents">0</div><div class="l">Documents</div></div>
        <div class="stat"><div class="n" data-stat="passages">0</div><div class="l">Passages</div></div>
        <div class="stat"><div class="n" data-stat="entities">0</div><div class="l">Entities</div></div>
        <div class="stat"><div class="n" data-stat="relationships">0</div><div class="l">Relationships</div></div>
      </div>

      <div class="ask rise" id="askBar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--ink-mute)"
             stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
        <input id="q" type="text" autocomplete="off"
               placeholder="Ask this corpus a question…"
               aria-label="Ask this corpus a question">
        <button class="btn" id="askBtn">Ask
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2.4" stroke-linecap="round"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
        </button>
      </div>
      <div class="row rise" id="suggest" style="margin-top:1rem;gap:.45rem"></div>
      <div id="loading" class="tiny muted mono" style="margin-top:1rem">Loading fabric…</div>
    </div>
  </section>

  <section id="graph" style="padding-top:1rem">
    <div class="wrap">
      <div class="stage rise" style="height:min(76vh,680px)">
        <canvas id="galaxy"></canvas>
        <div class="stage-ui">
          <div class="legend" id="legend"></div>
          <div class="stage-hint">Drag to orbit · scroll to zoom · click a node</div>
          <div class="tip" id="tip"></div>
        </div>
      </div>
      <p class="tiny muted center" style="margin-top:1rem">
        Entities and edges derived from document structure. Ask a question above
        to light the retrieval path through the graph.
      </p>
    </div>
  </section>

  <section id="answerSection">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Live answer</div>
        <h2 class="rise">Quoted verbatim, never paraphrased.</h2>
      </div>
      <div class="grid" style="grid-template-columns:minmax(320px,1.7fr) minmax(260px,1fr)">
        <div class="card rise"><div class="card-pad" style="padding:1.8rem">
          <div id="answer"><p class="muted small">Ask a question above, or pick one
            of the suggestions, to populate this section with a cited answer.</p></div>
        </div></div>
        <div class="card rise"><div class="card-pad">
          <div id="answerMeta"><p class="muted small">Confidence, source count and
            graph activation appear here.</p></div>
        </div></div>
      </div>
    </div>
  </section>

  <section id="findingsSection">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Graph findings</div>
        <h2 class="rise">The answer no single document contains.</h2>
        <p class="lede rise" style="margin-top:1rem">
          Retrieval finds passages that mention your question. Traversal joins
          relationships across documents to assemble an answer set — a list of
          affected entities that exists in no file, only in the connections
          between them.
        </p>
      </div>
      <div class="card rise"><div class="card-pad" style="padding:1.8rem">
        <div id="findings"><p class="muted small">Ask a question to traverse the graph.</p></div>
      </div></div>
    </div>
  </section>

  <section id="lineageSection">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Explainability</div>
        <h2 class="rise">The full chain, question to citation.</h2>
      </div>
      <div class="card rise"><div class="card-pad" style="padding:1.8rem">
        <div class="rail" id="lineage">
          <p class="muted small">Ask a question to trace the retrieval chain.</p>
        </div>
      </div></div>
    </div>
  </section>

  <section id="health">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Knowledge health</div>
        <h2 class="rise">Find the gaps before they cost you.</h2>
        <p class="lede rise" style="margin-top:1rem">
          Knowledge that sits in one document, that nobody can date, or that
          extraction has turned to debris is a risk you cannot see on a shared
          drive. Every score below is measured from the corpus — click any card
          to see the formula and the raw inputs behind it.
        </p>
      </div>
      <div class="grid" style="grid-template-columns:minmax(260px,320px) 1fr;gap:2.2rem;align-items:start">
        <div class="rise">
          <div class="card"><div class="card-pad center">
            <div id="healthRing"></div>
          </div></div>
          <div class="card" style="margin-top:1rem"><div class="card-pad">
            <div id="healthSummary"></div>
          </div></div>
        </div>
        <div class="grid g2" id="healthDims"></div>
      </div>

      <div class="section-head rise" style="margin:3.2rem 0 1.4rem">
        <div class="eyebrow">Where the risk sits</div>
        <h3>Four exposures, counted rather than estimated.</h3>
      </div>
      <div class="grid g4" id="healthRisks"></div>
    </div>
  </section>

  <section id="insights">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Insights</div>
        <h2 class="rise">The corpus at a glance.</h2>
      </div>
      <div class="tabs rise" data-tabs="ins">
        <button class="on" data-tab="types">Document types</button>
        <button data-tab="units">Units</button>
        <button data-tab="hubs">Graph hubs</button>
        <button data-tab="clusters">Clusters</button>
        <button data-tab="concepts">Concepts</button>
        <button data-tab="timeline">Timeline</button>
        <button data-tab="model">Domain model</button>
      </div>
      <div class="card rise" style="margin-top:1.2rem"><div class="card-pad" style="padding:1.8rem">
        <div class="panel on" data-panel-group="ins" data-panel="types" id="insDocTypes"></div>
        <div class="panel" data-panel-group="ins" data-panel="units" id="insUnits"></div>
        <div class="panel" data-panel-group="ins" data-panel="hubs" id="insHubs"></div>
        <div class="panel" data-panel-group="ins" data-panel="clusters" id="insDendro"></div>
        <div class="panel" data-panel-group="ins" data-panel="concepts" id="insConcepts"></div>
        <div class="panel" data-panel-group="ins" data-panel="timeline" id="insTimeline"></div>
        <div class="panel" data-panel-group="ins" data-panel="model">
          <div class="eyebrow">Code systems</div>
          <div class="grid g3" style="margin-bottom:2rem">{code_sys}</div>
          <div class="eyebrow">Workflow states</div>
          <div class="grid g2">{flows}</div>
        </div>
      </div></div>
    </div>
  </section>

  <section id="corpus">
    <div class="wrap">
      <div class="section-head">
        <div class="eyebrow rise">Corpus</div>
        <h2 class="rise">Read any document in full.</h2>
      </div>
      <div class="ask rise" style="max-width:520px;margin-bottom:1.6rem">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink-mute)"
             stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
        <input id="corpusSearch" type="text" placeholder="Filter by title, ID, unit or type…"
               aria-label="Filter the corpus">
        <span class="mono tiny muted" id="corpusCount" style="padding-right:1rem"></span>
      </div>
      <div class="grid g3" id="corpusList"></div>
    </div>
  </section>

</main>

<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
  <div class="sheet-in">
    <div class="sheet-head">
      <div style="flex:1">
        <h3 id="sheetTitle">Document</h3>
        <div id="sheetMeta"></div>
      </div>
      <button class="x" id="sheetClose" aria-label="Close">&times;</button>
    </div>
    <div class="sheet-body" id="sheetBody"></div>
  </div>
</div>

{FOOTER_TPL.replace('%LOGO%', logo(base, 24)).replace('</body></html>', '')}
<script src="{base}assets/vendor/three.min.js"></script>
<script src="{base}assets/js/galaxy.js"></script>
<script src="{base}assets/js/engine.js"></script>
<script src="{base}assets/js/app.js"></script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Overview graph for the landing hero
# ---------------------------------------------------------------------------

def build_overview(slugs: list[str]) -> dict:
    """A composite graph: each tenant's top hubs, plus the tenant node itself.

    The landing page should show the whole fabric, not one domain. Linking each
    tenant's hubs back to a tenant node produces eleven visible clusters joined
    at a centre — which is a truthful picture of what the product is.
    """
    nodes, edges = [], []
    for slug in slugs:
        g = json.loads((TENANTS / slug / "fabric" / "graph.json").read_text())
        m = json.loads((TENANTS / slug / "tenant.json").read_text())
        tid = f"unit:{m['tenant']}"
        nodes.append({"id": tid, "label": m["tenant"], "kind": "unit",
                      "docs": [], "degree": 40})
        top = sorted(g["nodes"], key=lambda n: -n["degree"])[:34]
        keep = {n["id"] for n in top}
        for n in top:
            nid = f"{slug}|{n['id']}"
            nodes.append({"id": nid, "label": n["label"], "kind": n["kind"],
                          "docs": [], "degree": n["degree"]})
            edges.append({"s": tid, "t": nid, "rel": "IN_DOMAIN", "docs": []})
        for e in g["edges"]:
            if e["s"] in keep and e["t"] in keep:
                edges.append({"s": f"{slug}|{e['s']}", "t": f"{slug}|{e['t']}",
                              "rel": e["rel"], "docs": []})
    return {"nodes": nodes, "edges": edges,
            "kinds": {"unit": "Domain", "system": "System",
                      "authority": "Standard", "site": "Site",
                      "subject": "Subject", "doctype": "Document type",
                      "role": "Role", "code": "Code"},
            "ontology": []}


# ---------------------------------------------------------------------------

def main() -> None:
    reg_path = TENANTS / "registry.json"
    if not reg_path.exists():
        raise SystemExit(
            "tenants/registry.json not found — run "
            "`python -m pipeline.build_tenants` first."
        )
    registry = json.loads(reg_path.read_text())
    slugs = [t["slug"] for t in registry["tenants"]]

    # Clear generated routes but never assets/.
    for p in (SITE / "demo", SITE / "data"):
        if p.exists():
            shutil.rmtree(p)

    print("=" * 74)
    print("  Knowledge Fabric — site build")
    print("=" * 74)

    total_bytes = 0
    for slug in slugs:
        src = TENANTS / slug
        dst = SITE / "data" / slug
        dst.mkdir(parents=True, exist_ok=True)

        for name in ("graph.json", "index.json", "health.json",
                     "insights.json", "documents.json", "semantic.json",
                     "dendrogram.json"):
            shutil.copyfile(src / "fabric" / name, dst / name)
        shutil.copyfile(src / "tenant.json", dst / "tenant.json")
        # Document bodies are NOT copied. The viewer reconstructs them from the
        # passage index, which already holds every paragraph with its section
        # and ordinal. Shipping both would duplicate the entire corpus for no
        # capability — it was 11 MB of the old payload.

        m = json.loads((src / "tenant.json").read_text())
        page = SITE / "demo" / slug / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        # The <main> carries the tenant; move it onto <body> where app.js reads it.
        htmlsrc = build_demo(m).replace(
            "<body-marker>\n<main data-tenant=", "<main data-tenant=")
        htmlsrc = htmlsrc.replace(
            "<body>\n<div class=\"field\">",
            f"<body data-tenant=\"{slug}\" data-base=\"../../\">\n<div class=\"field\">")
        page.write_text(htmlsrc, encoding="utf-8")

        size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
        total_bytes += size
        print(f"  demo/{slug:<18} {m['counts']['documents']:>3} docs · "
              f"{size / 1024 / 1024:>5.1f} MB")

    overview = build_overview(slugs)
    (SITE / "data" / "overview.json").write_text(
        json.dumps(overview, separators=(",", ":")), encoding="utf-8")

    (SITE / "index.html").write_text(build_index(registry), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    print("-" * 74)
    print(f"  {len(slugs)} demonstrations · {total_bytes / 1024 / 1024:.1f} MB payload")
    print(f"  landing: site/index.html")
    print(f"  routes:  site/demo/<slug>/index.html")
    print("=" * 74)


if __name__ == "__main__":
    main()
