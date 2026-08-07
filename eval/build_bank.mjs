/* =============================================================================
   Question bank builder — the tested set.

   Nova's question bank is credible because every question in it was RUN against
   the built corpus and kept only if it cleared a confidence bar. Hand-writing
   questions and hoping they score well is how you end up demoing a 22% answer.

   This does the same thing, generically, for every tenant:

     1. GENERATE broad candidates from the corpus itself — section headings,
        document subjects, doctypes, authorities, and high-mention entities.
        Templates are deliberately over-generated; most will be discarded.
     2. MEASURE each candidate through the real Engine — the same retrieval,
        composition and confidence path the browser runs. No separate scorer,
        so the numbers cannot drift from what a user actually sees.
     3. KEEP those at or above MIN_CONF that also cite more than one document.
     4. PICK the on-screen five across DISTINCT template families, so the first
        thing a viewer clicks shows the system reading the question rather than
        matching one phrasing repeatedly.

   Output: site/data/<slug>/questions.json  { top, bank, meta }
   ============================================================================= */
import fs from 'node:fs';
import path from 'node:path';
import { loadTenant, tenants, measure } from './harness.mjs';

const ROOT = path.resolve(process.argv[2] || '.');
const DATA = path.join(ROOT, 'site/data');

const MIN_CONF   = Number(process.env.KF_MIN_CONF || 56);  // floor for the bank
const TOP_CONF   = Number(process.env.KF_TOP_CONF || 62);  // floor for on-screen five
const BANK_MAX   = 90;
const TOP_N      = 5;

const titleish = s => String(s || '').trim();
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;

/* -- 1. candidate generation ------------------------------------------------ */
// Each family is a different QUESTION SHAPE. The on-screen five are drawn from
// five different families so they do not all read as the same question with the
// nouns swapped.
function candidates(bundle) {
  const docs = bundle.documents;
  const passages = bundle.index.passages || [];
  const out = [];
  const add = (family, q) => { if (q && q.length < 130) out.push({ q, family }); };

  const count = (arr, key) => {
    const m = new Map();
    for (const x of arr) { const v = key(x); if (v) m.set(v, (m.get(v) || 0) + 1); }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };

  const sections  = count(passages, p => titleish(p.section)).filter(([, n]) => n >= 6);
  const subjects  = count(docs, d => titleish(d.subject));
  const types     = count(docs, d => titleish(d.type));
  const units     = count(docs, d => titleish(d.unit));
  const authority = count(docs, d => titleish(d.authority));
  const systems   = count(docs, d => titleish(d.system));

  // Section-driven: section headings are the most retrievable strings in the
  // corpus — they repeat verbatim across many passages of the same doctype.
  for (const [s] of sections.slice(0, 26)) {
    add('section-covers', `What does the ${s} section cover?`);
    add('section-records', `What is recorded under ${s}?`);
  }
  // Subject-driven: what the documents are actually about.
  for (const [s] of subjects.slice(0, 22)) {
    add('subject-how', `How is ${s} handled?`);
    add('subject-req', `What are the requirements for ${s}?`);
    add('subject-who', `Which unit is accountable for ${s}?`);
    add('subject-escalate', `How is ${s} escalated?`);
  }
  // Doctype-driven.
  for (const [t] of types.slice(0, 14)) {
    add('doctype-contains', `What does a ${t} contain?`);
    add('doctype-when', `When is a ${t} issued?`);
  }
  // Governance-driven.
  for (const [a] of authority.slice(0, 12)) add('authority', `What does ${a} require?`);
  for (const [u] of units.slice(0, 12))     add('unit-owns', `What is ${u} responsible for?`);
  for (const [s] of systems.slice(0, 8))    add('system', `What is ${s} used for?`);

  // Cross-cutting section x subject. This constrains retrieval on two
  // independent axes at once and scores highest of any family — but ONLY pairs
  // that genuinely co-occur may be asked. A blind cross product scores just as
  // well (both terms are individually frequent) while producing mail-merge
  // nonsense like "What does the Flight Identification record for Catering
  // uplift?", which is exactly the kind of thing a reviewer notices and a
  // confidence number cannot catch. Gate on observed co-occurrence.
  const subjectOf = new Map(docs.map(d => [d.id, titleish(d.subject)]));
  const pair = new Map();
  for (const p of passages) {
    const sub = subjectOf.get(p.doc);
    const sec = titleish(p.section);
    if (!sub || !sec) continue;
    const k = `${sec}\u0000${sub}`;
    pair.set(k, (pair.get(k) || 0) + 1);
  }
  const coPairs = [...pair.entries()]
    .filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 40);
  for (const [k] of coPairs) {
    const [sec, sub] = k.split('\u0000');
    add('section-subject', `What does the ${sec} section say about ${sub}?`);
  }

  // Named instances (codes, MEL items, deficiency codes...) — the identifiers a
  // reviewer types verbatim. Only kinds that are genuinely mentioned in prose.
  const byKind = new Map();
  for (const n of bundle.graph.nodes) {
    if (!(n.mentions > 2)) continue;
    if (['unit', 'doctype', 'system', 'site', 'role'].includes(n.kind)) continue;
    if (!byKind.has(n.kind)) byKind.set(n.kind, []);
    byKind.get(n.kind).push(n);
  }
  for (const [, list] of byKind) {
    list.sort((a, b) => (b.mentions || 0) - (a.mentions || 0));
    for (const n of list.slice(0, 8)) {
      // Instance labels carry a human gloss after a separator — "SIU^S12 ·
      // Notification of new appointment booking". Asking the whole string reads
      // as a mangled paste; a reviewer types the identifier alone.
      const id = String(n.label).split(/\s+[·—–]\s+/)[0].trim();
      if (id) add('instance', `What is ${id}?`);
    }
  }

  // Keep the hand-authored set as candidates too — some are strong, and they
  // are the most naturally phrased. They must clear the same bar as the rest.
  for (const q of (bundle.manifest.questions || [])) add('curated', q);

  // de-dup
  const seen = new Set();
  return out.filter(c => {
    const k = c.q.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
}

/* -- 2/3/4. measure, filter, select ----------------------------------------- */
function buildFor(slug) {
  const { bundle, engine } = loadTenant(slug);
  const cands = candidates(bundle);

  const scored = [];
  for (const c of cands) {
    const m = measure(engine, c.q);
    if (!m.ok) continue;
    // More than one document is Nova's corroboration property: an answer
    // resting on a single file is not evidence, it is a quotation.
    if (m.docs < 2) continue;
    if (m.conf < MIN_CONF) continue;
    scored.push({ ...m, family: c.family });
  }
  scored.sort((a, b) => b.conf - a.conf);

  // On-screen five: highest scorer from each of five DISTINCT families.
  const top = [];
  const usedFamily = new Set();
  for (const s of scored) {
    if (top.length >= TOP_N) break;
    if (s.conf < TOP_CONF) break;
    if (usedFamily.has(s.family)) continue;
    usedFamily.add(s.family);
    top.push(s);
  }
  // If family diversity could not fill five, top up by score.
  for (const s of scored) {
    if (top.length >= TOP_N) break;
    if (top.some(t => t.q === s.q)) continue;
    top.push(s);
  }

  const bank = scored.slice(0, BANK_MAX);
  return {
    slug,
    top: top.map(t => t.q),
    bank: bank.map(b => b.q),
    meta: {
      generated: cands.length,
      passed: scored.length,
      banked: bank.length,
      minConf: bank.length ? Math.min(...bank.map(b => b.conf)) : 0,
      maxConf: bank.length ? Math.max(...bank.map(b => b.conf)) : 0,
      medianConf: bank.length ? bank[Math.floor(bank.length / 2)].conf : 0,
      topConf: top.map(t => t.conf),
      topFamilies: top.map(t => t.family),
    },
  };
}

/* -- run -------------------------------------------------------------------- */
const summary = [];
for (const slug of tenants()) {
  if (slug === 'overview') continue;
  const res = buildFor(slug);
  fs.writeFileSync(
    path.join(DATA, slug, 'questions.json'),
    JSON.stringify({ top: res.top, bank: res.bank, meta: res.meta }, null, 0)
  );
  summary.push(res);
  const m = res.meta;
  console.log(
    `  ${slug.padEnd(17)} gen ${String(m.generated).padStart(4)} → bank ${String(m.banked).padStart(3)}` +
    ` · ${m.minConf}-${m.maxConf}% med ${m.medianConf}% · top5 ${m.topConf.join(' ')}`
  );
}

const allTop = summary.flatMap(s => s.meta.topConf);
const allBank = summary.reduce((a, s) => a + s.meta.banked, 0);
console.log('-'.repeat(74));
console.log(`  ${summary.length} tenants · ${allBank} banked questions · ` +
            `on-screen five avg ${(allTop.reduce((a, b) => a + b, 0) / allTop.length).toFixed(1)}% ` +
            `min ${Math.min(...allTop)}%`);
if (Math.min(...allTop) < TOP_CONF) {
  console.log(`  NOTE: a tenant could not fill five at ≥${TOP_CONF}%.`);
}
