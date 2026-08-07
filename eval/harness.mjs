// Headless harness: runs site/assets/js/engine.js against built tenant data
// in Node, so question confidence can be MEASURED rather than asserted.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve(process.argv[2] || '.');
const DATA = path.join(ROOT, 'site/data');

const sandbox = { console, performance: { now: () => Date.now() } };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(ROOT, 'site/assets/js/engine.js'), 'utf8'), sandbox);

export function loadTenant(slug) {
  const j = n => JSON.parse(fs.readFileSync(path.join(DATA, slug, n), 'utf8'));
  const bundle = {
    manifest: j('tenant.json'), graph: j('graph.json'), index: j('index.json'),
    documents: j('documents.json'), health: j('health.json'),
    insights: j('insights.json'), semantic: j('semantic.json'),
    dendrogram: j('dendrogram.json'),
  };
  return { bundle, engine: new sandbox.Engine(bundle) };
}

export function tenants() {
  return fs.readdirSync(DATA).filter(d =>
    fs.existsSync(path.join(DATA, d, 'tenant.json')));
}

export function measure(engine, q) {
  const r = engine.answer(q);
  if (!r || !r.ok) return { q, ok: false, conf: 0, reason: r && r.reason };
  return {
    q, ok: true,
    conf: r.confidence != null ? r.confidence : (r.score || 0),
    intent: r.intent || (r.run && r.run.intent) || null,
    entities: (r.entities || []).length,
    sentences: (r.sentences || []).length,
    sources: r.sources || 0,
    docs: [...new Set((r.sentences || []).map(s => s.doc && s.doc.id))].length,
  };
}
