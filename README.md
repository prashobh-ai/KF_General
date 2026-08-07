# Knowledge Fabric

Eleven industry knowledge fabrics, each built from a fully synthetic enterprise
corpus. Ask a question and get an answer quoted verbatim from source documents,
a live 3D entity graph, a traversal that assembles answers no single document
contains, and knowledge-health scoring measured from the corpus itself.

Everything runs from static files. There is no inference service behind it.

```bash
pip install -r requirements.txt
python -m pipeline.build_tenants     # generate the corpora
python -m pytest                     # safety and integrity gate
python -m eval.evaluate              # retrieval accuracy
python -m pipeline.build_site        # build the site
python -m http.server -d site 8000   # http://localhost:8000
```

## What this is

| | |
|---|---|
| Tenants | 11, one per industry vertical |
| Documents | 660 · ~1.0M words |
| Passages | ~9,000, individually addressable |
| Entities | ~1,700 |
| Relationships | ~11,000 |
| Per-demo payload | ~1.6 MB |

Routes are `/demo/<slug>/`. Each tenant is a fictional QualiZeal demonstration
brand — **Q-Airlines, Q-Aerotech, Q-Health, Q-Assure Claims, Q-Pharma,
Q-DeviceLab, Q-Bank, Q-Assurance, Q-Cruise, Q-Retail, Q-Quality** — each with
its own lockup in `site/assets/brand/`.

## Synthetic content, real scaffolding

The governing idea is *a real Form X with fake data in it*. Realism in
enterprise documents comes almost entirely from structure — a reader who has
seen a hundred Airworthiness Directives recognises one by its shape long before
reading a word. So the shape is real and sourced; everything in it is invented.

Real, and cited as public fact: ATA iSpec 2200 chapters, IATA delay codes,
FAR Part 117/121, EASA Part-145; ICD-10-CM, LOINC, HL7 FHIR R4, HL7 v2 message
types; X12 transaction sets with CARC/RARC codes; ICH guidelines and eCTD
modules; ISO 13485, ISO 14971, IEC 62304; Basel III, SR 11-7, FinCEN thresholds;
PCAOB AS and IAASB ISA, ISQM 1/2, SOC 2 Trust Services Criteria; SOLAS, MARPOL,
ISM, Paris MOU deficiency codes, CDC VSP; GS1 keys and Incoterms 2020;
ISO/IEC/IEEE 29119 and WCAG 2.2.

Invented: every company, person, site, system, date, event and identifier.

### Identifiers cannot resolve

Identifiers are structurally valid — they pass the same check-digit rules a real
one would — and drawn from ranges the issuing authority reserves for
documentation and testing:

| Kind | Reserved range |
|---|---|
| IPv4 / IPv6 | RFC 5737 TEST-NET-1/2/3, RFC 3849 `2001:db8::/32` |
| Domains, email | RFC 2606 `example.com`, `.test`, `.invalid` |
| Phone | NANPA `555-0100`–`555-0199` |
| Country | ISO 3166 user-assigned `QM`–`QZ`, `XA`–`XZ`, `ZZ` |
| Aerodrome | ICAO `ZZZZ` (no assigned code) |
| Tail number | `N9xxZZ`, outside the issued registry |
| NPI | `9`-prefixed, Luhn-valid, never allocated by NPPES |
| GTIN / SSCC / GLN | GS1 restricted-circulation prefixes `02`, `04`, `20`–`29` |

`tests/test_fabric.py` fails the build if any generated identifier falls outside
these ranges, or if any real organisation is named.

### Statistically shaped

Uniformly random data is the fastest way to make a synthetic corpus feel fake.
Document volume follows each industry's seasonal cycle (airlines peak in July,
retail in November, health systems in January), code frequency follows a Zipf
tail so a handful of codes carry most of the volume, and effective dates decay
toward the present the way a live document set does.

## Architecture

```
pipeline/
  packs/          domain scaffolding — units, doc types, code systems, workflows
  world.py        entity instances and the typed relationships between them
  docgen.py       controlled-document generation
  fabric.py       passage extraction, BM25, graph, health, insights
  semantic.py     LSA semantic index
  build_tenants.py / build_site.py
site/assets/js/
  galaxy.js       WebGL 3D graph
  engine.js       hybrid retrieval, graph traversal, answer composition
  app.js          page controller
eval/             gold set and retrieval evaluation
tests/            safety and integrity gate
```

### Retrieval

Two retrievers with different failure modes, fused on rank:

- **BM25** over paragraph passages, with field boosting — a query term matching
  a document's subject counts for more than the same term in body prose.
- **LSA** (truncated SVD over TF-IDF, int8-quantised) for vocabulary mismatch:
  the user asks about "kidney function", the corpus says "creatinine".

Fused by **Reciprocal Rank Fusion** (k=60) rather than score blending, because
BM25 scores are unbounded and corpus-dependent while cosine is bounded — putting
them on a common scale needs constants that go stale the moment the corpus
changes. RRF fuses on rank position and needs no tuning. The semantic run is
weighted inversely to lexical coverage, so it contributes most exactly when the
query's words are absent from the index.

Answers are composed by **Maximal Marginal Relevance** (λ=0.72), so each added
sentence must contribute something the answer does not already contain.
Selecting the top sentence per document produces visible repetition, because the
highest-scoring sentences across documents are frequently paraphrases.

Every sentence is lifted **verbatim** from an indexed passage. Nothing generates
prose, so nothing can hallucinate. Below threshold the system returns an explicit
non-answer naming which check failed.

### Knowledge health

Five measures, each stating its own formula and raw inputs, rendered as
concentric arcs with the derivation on the back of every card:

| Measure | Asks |
|---|---|
| **Depth** | How much usable material does each document contribute? |
| **Connectedness** | How often does the same topic appear across documents? |
| **Traceability** | Can we say when each document is from, and how we know? |
| **Readability** | How much of the text is prose rather than extraction debris? |
| **Currency** | How recent is the material we can actually date? |

Plus a risk register counted rather than estimated: extraction debris, singleton
concepts, isolated documents, undated documents.

Making these honest required making the corpus imperfect. A generator that
emits uniformly complete, uniformly dated, uniformly clean documents produces
five scores of 100, which measure the generator rather than the data. So the
corpus now contains what a real estate contains: stub documents, documents whose
date survives only as a system timestamp or not at all, and extraction debris
whose volume tracks each industry's layout density — a maintenance task card is
mostly tables, an audit planning memorandum is mostly prose.

Two formulas deliberately depart from the reference implementation, and the
reasons are worth stating:

- **Depth** scores the share of documents clearing the threshold rather than
  the mean passages per document. A mean saturates — one richly-documented
  procedure hides ten stubs.
- **Connectedness** reports the raw cross-document share instead of normalising
  against a 0.35 "healthy" threshold, and it excludes organisational
  scaffolding (units, systems, authorities) which is attached to nearly every
  document by construction. Counting it pins every corpus at 100.

A test fails the build if any metric saturates or flattens across tenants,
because a score that reads the same everywhere is decoration.

### Confidence

Five measured signals combined as a weighted **geometric** mean — retrieval
margin, retriever agreement, question coverage, source consensus, authority
spread. Geometric because the signals are conjunctive: an answer with excellent
retrieval but zero query coverage is not average, it is wrong, and an arithmetic
mean would hide that.

### Concept extraction

Ranking concepts by document frequency produced a cloud of *item, work,
against, only, also* — the connective tissue of procedural English, which every
corpus shares and which therefore says nothing about any of them. Terms are now
scored against a domain vocabulary assembled from the pack itself (lexicon,
units, systems, authorities, subjects, document types, code meanings, workflow
states), with everything else required to clear a distinctiveness bar: present
in at least three passages, absent from more than 45%. `q-aerotech` now surfaces
*keelson, ispec, part-145, airworthiness, certifying, disposition, effectivity,
nacelle, calibration* — words a practitioner would recognise as their own.

### The graph, and why it is not a facet index

A graph derived only from document metadata is a facet index with edges drawn
on. Every question it answers, plain RAG answers too, because those
"relationships" carry no information not already in each document's header.

So `world.py` mints concrete instances per domain — tail numbers, batches, claim
ICNs, vessels, GTINs — with typed relationships between them, and documents are
generated *about* those instances and cite them by identifier. Because many
documents cite the same instances, the graph gains genuine cross-document
structure, and every edge traces to the documents asserting it.

That enables the class of question retrieval structurally cannot answer:

> *Which aircraft are affected by open Airworthiness Directives on the wing
> structure?*

No passage contains that answer. It requires joining an AD to the components it
applies to, and those components to the aircraft they are installed on.
Retrieval returns the AD documents and leaves the join to the reader.

Measured live on that question: retrieval cites 5 documents; traversal
additionally resolves **38 connected entities** assembled across 59 further
documents. The **Graph findings** panel shows that resolved set with the
traversal path justifying each entity — and says plainly when a question named
nothing to traverse from, which is the honest answer for definitional queries.

Single-document co-citation is pruned: two entities together in one document may
only mean both were in scope that day; two or more independent documents is a
pattern worth asserting.

## Accuracy

`python -m eval.evaluate` grades **retrieval**, not phrasing — if the right
evidence never surfaces, no amount of answer polish saves the response. 44 cases
across all tenants in four categories: lexical, semantic (vocabulary mismatch),
cross-source, and adversarial (plausible but genuinely absent).

```
              R@5    R@10   MRR    nDCG@10
bm25          0.75   0.77   0.63   0.659
semantic      0.75   0.78   0.64   0.669
hybrid        0.75   0.78   0.66   0.684    +3.7% vs lexical
```

The harness earned its place immediately. An earlier build measured hybrid
fusion as **worse** than BM25 alone (−8.2% nDCG). Investigating why exposed the
real defect: the corpus was lexically flat — section topics like "Barcode
Verification" never reached the prose, so no retriever could find them and LSA
had no co-occurrence structure to learn. Fixing that lifted every configuration
and reversed the sign.

## Visual design

White ground, following the QualiZeal delivery deck. Brand colours are sampled
directly from the logo embedded in that deck rather than eyeballed from
compressed slide fills:

| | |
|---|---|
| Brand blue | `#0096FF` |
| Brand coral | `#F53E5A` |
| Navy ink | `#1F2A3D` |

The QualiZeal mark itself (`site/assets/brand/qualizeal-icon.png`) is extracted
from `ppt/media` in the deck — an earlier build approximated it in hand-drawn
SVG and had both the ring geometry and both colours wrong. It appears in the
header beside the `QUALI`/`ZEAL` split wordmark, and again held large and
translucent at the right edge as a page watermark, exactly as the deck's title
slide holds it.

Each tenant leads with its own **Q-Domain** lockup, and the tenant name *is*
the Q-Domain name. Pairing a Q-Airlines logo with an invented trading name put
two competing identities on one card.

### The graph is labelled

Every node worth naming carries one. An unlabelled particle cloud is decorative;
a viewer cannot tell what they are looking at or what lit up. Labels are HTML
projected from the WebGL scene rather than sprite textures — 300 canvas textures
cost memory and render blurry under DPR scaling, whereas moving absolutely
positioned divs gives crisp text at any zoom.

The visible set is chosen by relevance: activated nodes always get a label, then
the highest-degree nodes fill a budget of roughly 18, with screen-space
collision rejection because overlapping labels are worse than fewer labels —
unreadable *and* they hide the graph. The camera also reframes onto the
activated subgraph, so the user never has to hunt for what changed.

Layout carries a short-range overlap-avoidance force. Without one, nodes stack
in the core and no label can be placed there, which is what made the dense
centre unusable.

### Clusters — the circular dendrogram

Every other insights panel answers *how much of X is there*. The Clusters panel
answers *how does this corpus organise itself*, which is the question someone
inheriting a document estate actually has.

Documents are embedded in the same LSA concept space the semantic retriever
uses, then merged bottom-up by Ward linkage. The tree is not imposed by the
taxonomy — two documents land on the same branch because they discuss the same
things. That is why clusters routinely cut across owning units, and why each
one is annotated with how many units it spans: a cluster spreading over seven
units means the same subject is being documented independently in seven places.

Rendered radially because the meaningful structure is the top-level split near
the centre, and a circle gives every leaf equal room on the rim.

### Graph activation is driven by mentions, not metadata

This is the difference between a graph that answers a question and one that
decorates it.

An earlier build activated the graph from the *metadata* of retrieved
documents — owning unit, system of record, governing authority. Those fields
are shared across almost every document in a tenant, so every question lit the
same handful of hubs. The graph was colourful, animated, and carried no
information about what had been asked.

Activation now comes from entities **actually mentioned in the retrieved
passages**, linked at build time by literal matching against entity labels and
identifiers. Matching literally under-recalls — it misses paraphrase — but it
never asserts a mention that is not in the text, and a highlight the reader
cannot verify by opening the passage is worse than a missing one.

Two supporting rules make it legible:

- **Boilerplate suppression.** A system of record named in the control header
  of every document is mentioned in a third of all passages. That is a true
  mention and a useless one — it fires on every question and, being a
  high-degree hub, drags hundreds of edges into the highlight. Entities above a
  frequency ceiling are excluded from activation while staying in the graph.
  Same problem as a stop word, same fix.
- **Edges outside the activated subgraph are not drawn.** Fading them was not
  enough: a thousand strokes at three percent each still accumulate into a dark
  scribble on white, burying the handful of edges that answer the question.

Node ranking also changed. The rendered subset was chosen by degree, which kept
the organisational scaffolding and dropped the subjects and instances questions
actually activate — so the highlight had nothing to light. Ranking now weights
mention count above degree.

### Graph activation

Legibility comes from collapsing size and opacity together, not from colour
alone. During a query the graph drops entity-kind hues and switches to three
activation tiers:

| Tier | Colour | Size | Opacity |
|---|---|---|---|
| Activated | signal red | ×1.32 | 1.00 |
| Neighbour | primary blue | ×0.86 | 0.90 |
| Unrelated | muted slate | ×0.30 | 0.14 |

Edges touching an activated node turn red; everything else fades almost into
the page. Eight competing hues is what made activation unreadable in an earlier
build — during an answer the only question that matters is *did this light up*.

Nodes render as solid discs under normal blending in a single draw call.
Additive blending was the right choice on a dark ground and exactly wrong on
white, where adding light can only wash toward invisible. Repulsion is
degree-weighted so hubs push apart into distinct lobes rather than collapsing
into one mass, and the camera frames to the graph's own extent so the composite
overview and a single tenant both fill their stage.

When an answer lands, pulses of light travel the exact traversal hops — the
graph is seen being *walked*, not merely coloured.

Reduced-motion preferences are respected throughout; keyboard focus is visible;
the layout is responsive to mobile.

## Licence and provenance

No client data was used to build any part of this. Standards, regulations and
code systems are cited as public reference. CPT and SNOMED CT are licensed
terminologies — they are referenced by name, authority and format only, and the
test suite fails the build if descriptors are ever embedded.
