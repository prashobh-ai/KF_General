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

Routes are `/demo/<slug>/`. Each tenant is a fictional company —
Northmark Air, Halcyon Aerotech, Cedarline Health System, Provident Benefit
Partners, Verenda Therapeutics, Arclight Medical Devices, Ridgeport Financial
Group, Thornbury Assurance LLP, Meridian Ocean Lines, Harbourfield Retail
Group, Axiom Quality Engineering.

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

### Confidence

Five measured signals combined as a weighted **geometric** mean — retrieval
margin, retriever agreement, question coverage, source consensus, authority
spread. Geometric because the signals are conjunctive: an answer with excellent
retrieval but zero query coverage is not average, it is wrong, and an arithmetic
mean would hide that.

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

White ground, following the QualiZeal deck: primary blue `#0B66E1`, signal red
`#FF3300` used sparingly as the one moment of heat, navy ink `#1F2A3D`, frost
tints. The QualiZeal mark sits as a fixed page watermark at 5% opacity — a
watermark large enough to notice is a watermark competing with the interface.

Each tenant carries its own **Q-Domain** lockup (`site/assets/brand/`), leading
the card on the landing page and the hero on its demonstration. The lockups
already carry the wordmark, so the trading name sits beneath as secondary
information rather than competing with it.

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
