/* =============================================================================
   Galaxy — the 3D knowledge graph.

   Design notes
   ------------
   Nodes are additive point sprites rather than meshes. A corpus graph runs to
   a thousand nodes; a thousand lit spheres costs a thousand draw calls and
   melts a phone. One Points object with a custom shader is a single draw call
   and gives us something meshes cannot: true additive accumulation, so
   overlapping nodes bloom into each other the way real light does. That
   accumulation is what makes the dense core look luminous rather than cluttered.

   Edges are a single LineSegments buffer with per-vertex colour, faded toward
   the midpoint so lines read as connections rather than a wire cage.

   Layout is force-directed in 3D, simulated on the main thread but frozen once
   it settles. Continuing to integrate forces forever would burn battery for no
   visual gain, so the sim cools and stops, and only camera drift continues.
   ============================================================================= */

(function (global) {
  'use strict';

  // Activation palette. The base tone is deliberately close to the page so
  // unrelated nodes recede rather than compete.
  const TIER = {
    ACTIVE:   [1.00, 0.20, 0.00],   // QualiZeal signal red
    RELATED:  [0.04, 0.40, 0.88],   // QualiZeal primary blue
    BASE:     [0.62, 0.68, 0.78],   // muted slate
  };

  const KIND_COLOR = {
    unit:      [0.15, 0.55, 1.00],
    system:    [0.00, 0.72, 0.96],
    authority: [1.00, 0.32, 0.05],
    site:      [0.19, 0.77, 0.69],
    subject:   [0.68, 0.74, 0.95],
    doctype:   [0.43, 0.36, 0.82],
    role:      [0.95, 0.70, 0.25],
    code:      [0.55, 0.85, 1.00],

    // Domain instances. Warmer and more saturated than the structural nodes so
    // the concrete things — the tail numbers, batches, claims — read as the
    // foreground layer they are.
    aircraft:  [0.32, 0.72, 1.00],  component: [0.62, 0.80, 1.00],
    ad:        [1.00, 0.42, 0.16],  workorder: [0.98, 0.74, 0.30],
    shop:      [0.24, 0.80, 0.70],  station:   [0.40, 0.86, 0.92],
    flight:    [0.55, 0.66, 1.00],  melitem:   [1.00, 0.60, 0.30],
    delaycode: [1.00, 0.50, 0.42],  pathway:   [0.30, 0.85, 0.74],
    orderset:  [0.55, 0.78, 1.00],  condition: [1.00, 0.52, 0.34],
    unitward:  [0.42, 0.88, 0.80],  interface: [0.62, 0.72, 1.00],
    policy:    [0.62, 0.55, 1.00],  carc:      [1.00, 0.45, 0.30],
    provider:  [0.45, 0.80, 1.00],  claimbatch:[0.72, 0.66, 1.00],
    edifile:   [0.55, 0.85, 0.95],  product:   [0.35, 0.85, 0.50],
    batch:     [0.60, 0.90, 0.62],  deviation: [1.00, 0.48, 0.24],
    capa:      [0.98, 0.78, 0.34],  line:      [0.40, 0.82, 0.68],
    device:    [0.90, 0.62, 0.28],  hazard:    [1.00, 0.40, 0.22],
    control:   [0.50, 0.82, 0.95],  complaint: [1.00, 0.58, 0.38],
    softitem:  [0.66, 0.70, 1.00],  borrower:  [0.42, 0.68, 1.00],
    facility:  [0.55, 0.78, 1.00],  covenant:  [0.98, 0.76, 0.36],
    alert:     [1.00, 0.44, 0.26],  model:     [0.62, 0.60, 1.00],
    engagement:[0.55, 0.62, 0.90],  account:   [0.70, 0.76, 0.95],
    risk:      [1.00, 0.46, 0.28],  workpaper: [0.60, 0.72, 0.92],
    vessel:    [0.30, 0.76, 1.00],  port:      [0.40, 0.86, 0.88],
    deficiency:[1.00, 0.44, 0.24],  equipment: [0.62, 0.80, 0.95],
    voyage:    [0.50, 0.78, 1.00],  vendor:    [1.00, 0.62, 0.28],
    item:      [0.98, 0.78, 0.42],  po:        [1.00, 0.55, 0.24],
    dc:        [0.45, 0.82, 0.72],  chargeback:[1.00, 0.42, 0.30],
    release:   [0.62, 0.58, 1.00],  requirement:[0.55, 0.76, 1.00],
    testcase:  [0.48, 0.86, 0.90],  defect:    [1.00, 0.46, 0.30],
    environment:[0.42, 0.80, 0.70]
  };

  // Instance nodes are the foreground; structural nodes are context. Sizing
  // them apart is what stops the graph reading as undifferentiated confetti.
  const DEFAULT_INSTANCE_SIZE = 13;

  const KIND_SIZE = {
    unit: 30, system: 26, authority: 27, site: 21,
    subject: 15, doctype: 24, role: 18, code: 12
  };

  // ---------------------------------------------------------------------------
  // Shaders
  // ---------------------------------------------------------------------------

  // Node sprite: a soft radial core with a tighter hot centre. Two falloffs
  // rather than one, because a single gaussian reads as fog while a core plus
  // halo reads as a light source.
  const NODE_VS = `
    attribute float size;
    attribute vec3  tint;
    attribute float glow;
    attribute float alpha;
    varying   vec3  vTint;
    varying   float vGlow;
    varying   float vAlpha;
    uniform   float uScale;
    void main() {
      vAlpha = alpha;
      vTint = tint;
      vGlow = glow;
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = size * uScale * (620.0 / -mv.z);
      gl_Position = projectionMatrix * mv;
    }
  `;

  const NODE_FS = `
    varying vec3  vTint;
    varying float vGlow;
    varying float vAlpha;
    void main() {
      vec2 uv = gl_PointCoord - vec2(0.5);
      float d = length(uv) * 2.0;
      if (d > 1.0) discard;

      // Solid disc with an antialiased rim, plus a soft outer ring that
      // strengthens with activation so a lit node reads as haloed rather than
      // merely larger.
      float disc = 1.0 - smoothstep(0.62, 0.78, d);
      float ring = (1.0 - smoothstep(0.70, 1.0, d)) * 0.22 * vGlow;

      // Lit nodes gain a white specular centre, which on paper reads as gloss.
      vec3 c = mix(vTint, mix(vTint, vec3(1.0), 0.45),
                   vGlow * (1.0 - smoothstep(0.0, 0.42, d)));

      float a = (disc + ring) * vAlpha;
      if (a < 0.01) discard;
      gl_FragColor = vec4(c, a);
    }
  `;

  // ---------------------------------------------------------------------------

  class Galaxy {
    constructor(canvas, opts) {
      // Label layer. Nova draws a name on every node, and that single feature
      // is most of why its graph is usable — an unlabelled particle cloud is
      // pretty and tells a viewer nothing about what they are looking at.
      //
      // Labels are HTML rather than sprite textures: 460 canvas textures cost
      // memory and render blurry under DPR scaling, whereas projecting node
      // positions and moving absolutely-positioned divs gives crisp text at
      // any zoom for the price of one transform per visible label.
      this.labels = null;
      this.canvas = canvas;
      this.opts = Object.assign({
        maxNodes: 300,
        onHover: null,
        onSelect: null,
        autorotate: 0.00035,
        dpr: Math.min(window.devicePixelRatio || 1, 2)
      }, opts || {});

      this.nodes = [];
      this.edges = [];
      this.hidden = new Set();
      this.active = new Set();
      this.hovered = -1;
      this.raf = null;
      this.alpha = 1;          // simulation temperature
      this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      this._initGL();
      this._initLabels();
      this._bind();
    }

    _initLabels() {
      const host = this.canvas.parentElement;
      if (!host) return;
      const layer = document.createElement('div');
      layer.className = 'glabels';
      host.appendChild(layer);
      this.labels = layer;
      this._labelPool = [];
    }

    _initGL() {
      const THREE = global.THREE;
      this.scene = new THREE.Scene();
      this.scene.fog = new THREE.FogExp2(0x060b16, 0.0016);

      this.camera = new THREE.PerspectiveCamera(52, 1, 1, 4000);
      this.camera.position.set(0, 0, 640);

      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas,
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance'
      });
      this.renderer.setClearColor(0x000000, 0);
      this.renderer.setPixelRatio(this.opts.dpr);

      this.root = new THREE.Group();
      this.scene.add(this.root);

      // Depth cue: a faint starfield well behind the graph. It parallaxes as
      // the camera drifts, which is what sells the volume as three-dimensional
      // rather than a flat projection that happens to rotate.
      this._addStars();
    }

    _addStars() {
      const THREE = global.THREE;
      const N = 900, pos = new Float32Array(N * 3);
      for (let i = 0; i < N; i++) {
        const r = 1400 + Math.random() * 900;
        const t = Math.random() * Math.PI * 2;
        const p = Math.acos(2 * Math.random() - 1);
        pos[i * 3]     = r * Math.sin(p) * Math.cos(t);
        pos[i * 3 + 1] = r * Math.sin(p) * Math.sin(t);
        pos[i * 3 + 2] = r * Math.cos(p);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      this.stars = new THREE.Points(g, new THREE.PointsMaterial({
        color: 0x8fb4e8, size: 1.6, sizeAttenuation: true,
        transparent: true, opacity: 0.42, depthWrite: false
      }));
      this.scene.add(this.stars);
    }

    // -- data ---------------------------------------------------------------

    setGraph(graph, accentHex) {
      const THREE = global.THREE;

      // Keep the highest-degree nodes. A full corpus graph has a long tail of
      // degree-1 leaves that add cost and read as noise; the hubs are the
      // structure a viewer can actually perceive.
      // Rank by how often an entity is actually MENTIONED, then by degree.
      //
      // Ranking on degree alone kept the organisational scaffolding and
      // dropped the subjects and instances that questions actually activate —
      // so the retrieval highlight had nothing to light up. Mentionable nodes
      // must survive the cap or the graph cannot react.
      const score = n => (n.mentions || 0) * 3 + n.degree;
      const all = graph.nodes.slice().sort((a, b) => score(b) - score(a));
      const keep = all.slice(0, this.opts.maxNodes);
      const idset = new Set(keep.map(n => n.id));

      this.byId = new Map();
      const maxDeg = Math.max(1, ...keep.map(n => n.degree));
      this.nodes = keep.map((n, i) => {
        const v = {
          mass: 1 + 2.6 * (n.degree / maxDeg),
          i, id: n.id, label: n.label, kind: n.kind,
          docs: n.docs || [], degree: n.degree,
          x: (Math.random() - 0.5) * 460,
          y: (Math.random() - 0.5) * 380,
          z: (Math.random() - 0.5) * 380,
          vx: 0, vy: 0, vz: 0,
          glow: 0, tier: 0, alpha: 1, sizeMul: 1,
          tAlpha: 1, tSize: 1,
          baseSize: (KIND_SIZE[n.kind] || DEFAULT_INSTANCE_SIZE) *
                    (1 + Math.min(n.degree, 30) / 52)
        };
        this.byId.set(n.id, v);
        return v;
      });

      this.edges = graph.edges
        .filter(e => idset.has(e.s) && idset.has(e.t))
        .map(e => ({
          s: this.byId.get(e.s), t: this.byId.get(e.t),
          rel: e.rel, docs: e.docs || []
        }));

      // Adjacency, used for neighbour highlighting on hover.
      this.adj = new Map();
      this.edges.forEach(e => {
        if (!this.adj.has(e.s.id)) this.adj.set(e.s.id, new Set());
        if (!this.adj.has(e.t.id)) this.adj.set(e.t.id, new Set());
        this.adj.get(e.s.id).add(e.t.id);
        this.adj.get(e.t.id).add(e.s.id);
      });

      if (accentHex) this.accent = new THREE.Color(accentHex);
      this._buildMeshes();
      this._settle(360);

      // Frame to content rather than to a constant. The composite overview
      // graph is several times the extent of a single tenant's.
      let ext = 0;
      for (const n of this.nodes) {
        ext = Math.max(ext, Math.hypot(n.x, n.y, n.z));
      }
      this.dist = Math.max(360, Math.min(1500, ext * 2.35));
      this.start();
    }

    _buildMeshes() {
      const THREE = global.THREE;
      if (this.pts) { this.root.remove(this.pts); this.pts.geometry.dispose(); }
      if (this.lines) { this.root.remove(this.lines); this.lines.geometry.dispose(); }

      const N = this.nodes.length;
      const pos = new Float32Array(N * 3);
      const size = new Float32Array(N);
      const tint = new Float32Array(N * 3);
      const glow = new Float32Array(N);
      const alpha = new Float32Array(N).fill(1);

      this.nodes.forEach((n, i) => {
        const c = KIND_COLOR[n.kind] || [0.6, 0.7, 0.9];
        tint[i * 3] = c[0]; tint[i * 3 + 1] = c[1]; tint[i * 3 + 2] = c[2];
        size[i] = n.baseSize;
        glow[i] = 0;
      });

      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setAttribute('size', new THREE.BufferAttribute(size, 1));
      g.setAttribute('tint', new THREE.BufferAttribute(tint, 3));
      g.setAttribute('glow', new THREE.BufferAttribute(glow, 1));
      g.setAttribute('alpha', new THREE.BufferAttribute(alpha, 1));

      this.pts = new THREE.Points(g, new THREE.ShaderMaterial({
        uniforms: { uScale: { value: 1.0 } },
        vertexShader: NODE_VS,
        fragmentShader: NODE_FS,
        transparent: true,
        depthWrite: false,
        blending: THREE.NormalBlending
      }));
      this.root.add(this.pts);

      const E = this.edges.length;
      const lp = new Float32Array(E * 6);
      const lc = new Float32Array(E * 6);
      const lg = new THREE.BufferGeometry();
      lg.setAttribute('position', new THREE.BufferAttribute(lp, 3));
      lg.setAttribute('color', new THREE.BufferAttribute(lc, 3));
      this.lines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.55,
        depthWrite: false, blending: THREE.NormalBlending
      }));
      this.root.add(this.lines);

      this._sync();
    }

    // -- force simulation ---------------------------------------------------

    /* Barnes-Hut would be the textbook answer for repulsion, but at n<500 the
       tree construction costs more than it saves. Instead we sample a random
       subset of pairs each tick: over many ticks every pair is visited, the
       layout converges to the same place, and each tick stays O(n). */
    _tick() {
      const nodes = this.nodes, n = nodes.length;
      if (!n) return;
      const REP = 8200, SPRING = 0.0120, LEN = 146, CENTRE = 0.0009, DAMP = 0.84;
      const sample = Math.min(n, 90);

      for (let i = 0; i < n; i++) {
        const a = nodes[i];
        for (let s = 0; s < sample; s++) {
          const b = nodes[(Math.random() * n) | 0];
          if (a === b) continue;
          let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          let d2 = dx * dx + dy * dy + dz * dz;
          if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
          const f = REP / d2 * (n / sample) * 0.02 * a.mass * b.mass;
          const d = Math.sqrt(d2);
          a.vx += dx / d * f; a.vy += dy / d * f; a.vz += dz / d * f;
        }
        a.vx -= a.x * CENTRE; a.vy -= a.y * CENTRE; a.vz -= a.z * CENTRE;
      }

      // Overlap avoidance. A short-range hard push that only acts when two
      // nodes are closer than their combined radii, so it separates the core
      // without inflating the whole layout.
      for (let i = 0; i < n; i++) {
        const a = nodes[i];
        for (let s2 = 0; s2 < 14; s2++) {
          const b = nodes[(Math.random() * n) | 0];
          if (a === b) continue;
          const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          const minD = (a.baseSize + b.baseSize) * 0.5 + 14;
          if (d < minD) {
            const push = (minD - d) / d * 0.55;
            a.vx += dx * push; a.vy += dy * push; a.vz += dz * push;
            b.vx -= dx * push; b.vy -= dy * push; b.vz -= dz * push;
          }
        }
      }

      for (const e of this.edges) {
        const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, dz = e.t.z - e.s.z;
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
        const f = (d - LEN) * SPRING / Math.sqrt(e.s.mass * e.t.mass);
        const ux = dx / d * f, uy = dy / d * f, uz = dz / d * f;
        e.s.vx += ux; e.s.vy += uy; e.s.vz += uz;
        e.t.vx -= ux; e.t.vy -= uy; e.t.vz -= uz;
      }

      const a = this.alpha;
      for (const p of nodes) {
        p.vx *= DAMP; p.vy *= DAMP; p.vz *= DAMP;
        p.x += p.vx * a; p.y += p.vy * a; p.z += p.vz * a;
      }
      this.alpha *= 0.994;
    }

    _settle(iterations) {
      for (let i = 0; i < iterations; i++) this._tick();
    }

    _sync() {
      const attrs = this.pts.geometry.attributes;
      const pa = attrs.position.array, ga = attrs.glow.array;
      const aa = attrs.alpha.array, sa = attrs.size.array, ta = attrs.tint.array;
      const anyActive = this.active.size > 0;

      this.nodes.forEach((n, i) => {
        const off = this.hidden.has(n.kind) ? 100000 : 0;
        pa[i * 3] = n.x + off; pa[i * 3 + 1] = n.y; pa[i * 3 + 2] = n.z;
        ga[i] = n.glow;
        aa[i] = n.alpha;
        sa[i] = n.baseSize * n.sizeMul;

        // Recolour by activation tier rather than by entity kind while a
        // query is live. Kind colour is useful for browsing, but during an
        // answer the only question that matters is "did this light up?" —
        // and eight hues competing for attention is what made activation
        // unreadable before.
        let c;
        if (!anyActive) c = KIND_COLOR[n.kind] || TIER.BASE;
        else if (n.tier === 2) c = TIER.ACTIVE;
        else if (n.tier === 1) c = TIER.RELATED;
        else c = TIER.BASE;
        ta[i * 3] = c[0]; ta[i * 3 + 1] = c[1]; ta[i * 3 + 2] = c[2];
      });
      attrs.position.needsUpdate = true;
      attrs.glow.needsUpdate = true;
      attrs.alpha.needsUpdate = true;
      attrs.size.needsUpdate = true;
      attrs.tint.needsUpdate = true;

      const la = this.lines.geometry.attributes.position.array;
      const lc = this.lines.geometry.attributes.color.array;
      this.edges.forEach((e, i) => {
        // While a question is active, edges outside the activated subgraph are
        // not drawn at all.
        //
        // Fading them was not enough: a thousand strokes at 3% each still
        // accumulate into a dark scribble on white, and the handful of edges
        // that answer the question disappear inside it. Removing them entirely
        // is the only thing that makes the activated relations prominent —
        // which is the whole purpose of the highlight.
        const offGraph = anyActive && !(this.activeEdges && this.activeEdges.has(i));
        const hid = this.hidden.has(e.s.kind) || this.hidden.has(e.t.kind) || offGraph;
        const o = i * 6;
        if (hid) { for (let k = 0; k < 6; k++) la[o + k] = 100000; }
        else {
          la[o] = e.s.x; la[o + 1] = e.s.y; la[o + 2] = e.s.z;
          la[o + 3] = e.t.x; la[o + 4] = e.t.y; la[o + 5] = e.t.z;
        }
        // An edge is active only when BOTH ends are in the activated set —
        // that is what makes the lit subgraph read as a connected path rather
        // than a spray of highlighted dots.
        let cs, ct;
        if (!anyActive) {
          cs = ct = [0.84, 0.89, 0.95];
        } else {
          // An edge touching an activated node is drawn red: requiring BOTH
          // ends to be active made almost no edge qualify, so the lit nodes
          // floated unconnected and the path through the graph was invisible.
          const both = e.s.tier === 2 && e.t.tier === 2;
          const touching = this.activeEdges && this.activeEdges.has(i);
          if (both) { cs = ct = TIER.ACTIVE; }
          else if (touching) { cs = ct = [0.55, 0.76, 0.96]; }
          else {
            // Unrelated edges fade almost to the page. Nova pushed these to
            // 0.04 opacity for exactly this reason: without an aggressive
            // fade the activation is invisible from across a room.
            cs = ct = [0.972, 0.978, 0.986];
          }
        }
        lc[o] = cs[0]; lc[o + 1] = cs[1]; lc[o + 2] = cs[2];
        lc[o + 3] = ct[0]; lc[o + 4] = ct[1]; lc[o + 5] = ct[2];
      });
      this.lines.geometry.attributes.position.needsUpdate = true;
      this.lines.geometry.attributes.color.needsUpdate = true;
    }

    // -- highlight ----------------------------------------------------------

    /** Light a set of node ids and their immediate neighbours. */
    /**
     * Three-tier activation.
     *
     * Modulating glow alone — which is what this did before — is far too
     * subtle: the graph stayed uniformly colourful and a viewer could not tell
     * which nodes had fired. Legibility comes from collapsing SIZE and OPACITY
     * together, so unrelated structure physically recedes:
     *
     *   activated   full size x1.7, opaque, signal red
     *   neighbour   normal size,    opaque, primary blue
     *   unrelated   size x0.34,     12% alpha, muted slate
     */
    illuminate(ids) {
      this.active = new Set((ids || []).filter(i => this.byId.has(i)));

      // Neighbours are derived from the EDGES, exactly as Nova does it: an
      // edge with one active end promotes its other end and is itself drawn
      // active. Deriving neighbours from an adjacency set instead leaves the
      // connecting edges unlit, and the highlight reads as scattered dots
      // rather than a traversed region.
      const near = new Set();
      this.activeEdges = new Set();
      this.edges.forEach((e, i) => {
        const a = this.active.has(e.s.id), b = this.active.has(e.t.id);
        if (a && b) { this.activeEdges.add(i); }
        else if (a) { near.add(e.t.id); this.activeEdges.add(i); }
        else if (b) { near.add(e.s.id); this.activeEdges.add(i); }
      });
      this.nodes.forEach(n => {
        if (this.active.has(n.id)) {
          n.tier = 2; n.target = 1;    n.tAlpha = 1.0;  n.tSize = 1.32;
        } else if (near.has(n.id)) {
          n.tier = 1; n.target = 0.35; n.tAlpha = 0.90; n.tSize = 0.86;
        } else {
          n.tier = 0; n.target = 0;    n.tAlpha = 0.14; n.tSize = 0.30;
        }
      });
      if (this.lines) this.lines.material.opacity = 0.55;
      // A pulse of heat re-energises the layout so lit nodes visibly settle
      // into a new arrangement — motion confirms the answer changed something.
      this.alpha = Math.max(this.alpha, 0.35);
    }

    /**
     * Animate light travelling along a traversal path.
     *
     * This is the thing a 2D network library cannot do convincingly: pulses of
     * light moving through the volume, occluded and revealed by depth, that
     * show the graph being WALKED rather than merely coloured. When an answer
     * lands, the pulses trace the exact hops the engine took.
     *
     * Implemented as a second additive Points buffer rather than animated
     * geometry, so an arbitrary number of pulses costs one draw call.
     */
    tracePaths(paths) {
      const THREE = global.THREE;
      this.pulses = [];
      for (const p of (paths || [])) {
        for (const step of (p.path || p)) {
          const a = this.byId.get(step.from), b = this.byId.get(step.to);
          if (a && b) this.pulses.push({ a, b, t: Math.random(), speed: 0.006 + Math.random() * 0.006 });
        }
      }
      if (!this.pulseObj) {
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 512), 3));
        g.setAttribute('size', new THREE.BufferAttribute(new Float32Array(512).fill(34), 1));
        g.setAttribute('tint', new THREE.BufferAttribute(new Float32Array(3 * 512), 3));
        g.setAttribute('glow', new THREE.BufferAttribute(new Float32Array(512).fill(1.6), 1));
        this.pulseObj = new THREE.Points(g, this.pts.material);
        this.root.add(this.pulseObj);
      }
      const tint = this.pulseObj.geometry.attributes.tint.array;
      for (let i = 0; i < 512; i++) {
        // Signal red, so a traversal pulse is never confused with a node.
        tint[i * 3] = 1.0; tint[i * 3 + 1] = 0.25; tint[i * 3 + 2] = 0.05;
      }
      this.pulseObj.geometry.attributes.tint.needsUpdate = true;
    }

    _stepPulses() {
      if (!this.pulseObj || !this.pulses || !this.pulses.length) return;
      const pa = this.pulseObj.geometry.attributes.position.array;
      const n = Math.min(this.pulses.length, 512);
      for (let i = 0; i < 512; i++) {
        if (i >= n) { pa[i * 3] = 100000; continue; }
        const p = this.pulses[i];
        p.t += p.speed;
        if (p.t > 1) p.t = 0;
        // Ease so the pulse accelerates out of a node and decelerates into the
        // next, which reads as intent rather than uniform drift.
        const e = p.t * p.t * (3 - 2 * p.t);
        pa[i * 3]     = p.a.x + (p.b.x - p.a.x) * e;
        pa[i * 3 + 1] = p.a.y + (p.b.y - p.a.y) * e;
        pa[i * 3 + 2] = p.a.z + (p.b.z - p.a.z) * e;
      }
      this.pulseObj.geometry.attributes.position.needsUpdate = true;
    }

    clearPulses() { this.pulses = []; this._stepPulses(); }

    clearHighlight() {
      this.active.clear();
      this.activeEdges = null;
      this.nodes.forEach(n => {
        n.tier = 0; n.target = 0; n.tAlpha = 1; n.tSize = 1;
      });
      if (this.lines) this.lines.material.opacity = 0.14;
    }

    toggleKind(kind) {
      if (this.hidden.has(kind)) this.hidden.delete(kind);
      else this.hidden.add(kind);
      this._sync();
      return this.hidden.has(kind);
    }

    focusNode(id) {
      const n = this.byId && this.byId.get(id);
      if (!n) return;
      this.flyTo = { x: n.x, y: n.y, z: n.z };
      this.illuminate([id]);
    }

    // -- interaction --------------------------------------------------------

    _bind() {
      const c = this.canvas;
      this.rot = { x: 0.18, y: 0.4 };
      this.drag = null;
      this.dist = 640;

      const pick = (ev) => {
        const r = c.getBoundingClientRect();
        const mx = ((ev.clientX - r.left) / r.width) * 2 - 1;
        const my = -((ev.clientY - r.top) / r.height) * 2 + 1;
        // Project every node and take the nearest within a screen radius.
        // A raycaster against Points needs a threshold that scales with
        // distance; projecting is cheaper and gives exact screen-space hit
        // radii, which is what a user's pointer actually expects.
        let best = -1, bestD = 0.055;
        const v = new global.THREE.Vector3();
        for (let i = 0; i < this.nodes.length; i++) {
          const n = this.nodes[i];
          if (this.hidden.has(n.kind)) continue;
          v.set(n.x, n.y, n.z).applyMatrix4(this.root.matrixWorld).project(this.camera);
          if (v.z > 1) continue;
          const d = Math.hypot(v.x - mx, v.y - my);
          if (d < bestD) { bestD = d; best = i; }
        }
        return best;
      };

      c.addEventListener('pointerdown', e => {
        this.drag = { x: e.clientX, y: e.clientY, moved: 0 };
        c.setPointerCapture(e.pointerId);
      });

      c.addEventListener('pointermove', e => {
        if (this.drag) {
          const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
          this.drag.moved += Math.abs(dx) + Math.abs(dy);
          this.rot.y += dx * 0.005;
          this.rot.x = Math.max(-1.3, Math.min(1.3, this.rot.x + dy * 0.005));
          this.drag.x = e.clientX; this.drag.y = e.clientY;
          return;
        }
        const i = pick(e);
        if (i !== this.hovered) {
          this.hovered = i;
          c.style.cursor = i >= 0 ? 'pointer' : 'grab';
          if (this.opts.onHover) {
            this.opts.onHover(i >= 0 ? this.nodes[i] : null, e);
          }
        }
      });

      const end = (e) => {
        if (this.drag && this.drag.moved < 6) {
          const i = pick(e);
          if (i >= 0 && this.opts.onSelect) this.opts.onSelect(this.nodes[i]);
        }
        this.drag = null;
      };
      c.addEventListener('pointerup', end);
      c.addEventListener('pointercancel', () => { this.drag = null; });
      c.addEventListener('pointerleave', () => {
        this.drag = null; this.hovered = -1;
        if (this.opts.onHover) this.opts.onHover(null);
      });

      c.addEventListener('wheel', e => {
        e.preventDefault();
        this.dist = Math.max(180, Math.min(1400, this.dist + e.deltaY * 0.6));
      }, { passive: false });
    }

    /**
     * Position labels for the nodes worth naming.
     *
     * Showing all 460 at once is unreadable, so the set is chosen by relevance:
     * every activated node always gets a label, then the highest-degree nodes
     * fill the remaining budget. During a query that means the lit subgraph is
     * named and everything else recedes, which is exactly the question a viewer
     * is asking.
     */
    _syncLabels() {
      if (!this.labels || !this.nodes.length) return;
      const THREE = global.THREE;
      const r = this.canvas.getBoundingClientRect();
      if (!r.width) return;

      const anyActive = this.active.size > 0;
      const budget = r.width < 760 ? 10 : (anyActive ? 18 : 16);

      const cands = [];
      const v = new THREE.Vector3();
      for (const n of this.nodes) {
        if (this.hidden.has(n.kind)) continue;
        if (anyActive && n.tier === 0) continue;
        v.set(n.x, n.y, n.z).applyMatrix4(this.root.matrixWorld).project(this.camera);
        if (v.z > 1 || v.x < -1.05 || v.x > 1.05 || v.y < -1.05 || v.y > 1.05) continue;
        const priority = (n.tier === 2 ? 1e6 : n.tier === 1 ? 1e3 : 0) + n.degree;
        const sy = (-v.y * 0.5 + 0.5) * r.height;
        cands.push({
          n, priority,
          sx: (v.x * 0.5 + 0.5) * r.width,
          sy,
          // Collide on where the label will actually be drawn, not on the
          // node's projected centre.
          ly: sy - 16 - n.baseSize * n.sizeMul * 0.22,
          depth: v.z,
        });
      }
      cands.sort((a, b) => b.priority - a.priority);

      // Screen-space collision rejection. Overlapping labels are worse than
      // fewer labels — they are unreadable AND they hide the graph.
      const placed = [];
      const shown = [];
      for (const c of cands) {
        if (shown.length >= budget) break;
        const w = Math.min(150, 7.2 * c.n.label.length + 14);
        let clash = false;
        for (const p of placed) {
          if (Math.abs(c.sx - p.sx) < (w + p.w) * 0.5 + 8 &&
              Math.abs(c.ly - p.ly) < 26) {
            clash = true; break;
          }
        }
        if (clash) continue;
        placed.push({ sx: c.sx, ly: c.ly, w });
        shown.push(c);
      }

      while (this._labelPool.length < shown.length) {
        const el = document.createElement('span');
        el.className = 'glabel';
        el.addEventListener('click', () => {
          const d = el._node;
          if (d && this.opts.onSelect) this.opts.onSelect(d);
        });
        this.labels.appendChild(el);
        this._labelPool.push(el);
      }

      this._labelPool.forEach((el, i) => {
        const c = shown[i];
        if (!c) { el.style.display = 'none'; return; }
        el.style.display = 'block';
        el._node = c.n;
        const text = c.n.label.length > 26 ? c.n.label.slice(0, 25) + '\u2026' : c.n.label;
        if (el.textContent !== text) el.textContent = text;
        el.className = 'glabel' + (c.n.tier === 2 ? ' on' : c.n.tier === 1 ? ' near' : '');
        el.style.transform =
          `translate(-50%,-50%) translate(${c.sx.toFixed(1)}px, ${c.ly.toFixed(1)}px)`;
        // Fade with depth so far labels do not compete with near ones.
        el.style.opacity = (1 - Math.max(0, Math.min(0.62, (c.depth - 0.2) * 1.4))).toFixed(2);
      });
    }

    /**
     * Frame the camera on a set of nodes.
     *
     * Nova refits after activation so the lit subgraph fills the viewport.
     * Without it a user has to hunt for what changed, which defeats the point
     * of highlighting.
     */
    fitTo(ids) {
      const list = (ids && ids.length)
        ? ids.map(i => this.byId.get(i)).filter(Boolean)
        : this.nodes;
      if (!list.length) return;
      let cx = 0, cy = 0, cz = 0;
      for (const n of list) { cx += n.x; cy += n.y; cz += n.z; }
      cx /= list.length; cy /= list.length; cz /= list.length;
      let rad = 0;
      for (const n of list) {
        rad = Math.max(rad, Math.hypot(n.x - cx, n.y - cy, n.z - cz));
      }
      this.flyTarget = { x: cx, y: cy, z: cz };
      this.flyDist = Math.max(230, Math.min(1400, rad * 2.15 + 130));
    }

    resize() {
      const r = this.canvas.getBoundingClientRect();
      if (!r.width || !r.height) return;
      this.renderer.setSize(r.width, r.height, false);
      this.camera.aspect = r.width / r.height;
      this.camera.updateProjectionMatrix();
      this.pts && (this.pts.material.uniforms.uScale.value =
        Math.min(1.5, Math.max(0.70, r.width / 980)));
    }

    start() {
      if (this.raf) return;
      this.resize();
      const loop = () => {
        this.raf = requestAnimationFrame(loop);
        this.render();
      };
      loop();
    }

    stop() { cancelAnimationFrame(this.raf); this.raf = null; }

    render() {
      if (this.alpha > 0.012) { this._tick(); }

      // Ease glow toward target so highlight changes feel physical.
      let dirty = this.alpha > 0.012;
      for (const n of this.nodes) {
        const t = n.target || 0;
        if (Math.abs(n.glow - t) > 0.004) { n.glow += (t - n.glow) * 0.12; dirty = true; }
        if (Math.abs(n.alpha - n.tAlpha) > 0.004) { n.alpha += (n.tAlpha - n.alpha) * 0.14; dirty = true; }
        if (Math.abs(n.sizeMul - n.tSize) > 0.004) { n.sizeMul += (n.tSize - n.sizeMul) * 0.14; dirty = true; }
      }
      if (dirty) this._sync();

      if (!this.reduced) this._stepPulses();
      if (!this.reduced && !this.drag) this.rot.y += this.opts.autorotate;

      const cx = Math.cos(this.rot.x), sx = Math.sin(this.rot.x);
      const cy = Math.cos(this.rot.y), sy = Math.sin(this.rot.y);
      // Ease toward the framing target rather than cutting, so the viewer can
      // follow where the camera went.
      this.target = this.target || { x: 0, y: 0, z: 0 };
      if (this.flyTarget) {
        this.target.x += (this.flyTarget.x - this.target.x) * 0.07;
        this.target.y += (this.flyTarget.y - this.target.y) * 0.07;
        this.target.z += (this.flyTarget.z - this.target.z) * 0.07;
      }
      if (this.flyDist) this.dist += (this.flyDist - this.dist) * 0.07;

      this.camera.position.set(
        this.target.x + this.dist * cx * sy,
        this.target.y + this.dist * sx,
        this.target.z + this.dist * cx * cy
      );
      this.camera.lookAt(this.target.x, this.target.y, this.target.z);

      this.stars.rotation.y += 0.00006;
      this.renderer.render(this.scene, this.camera);

      // Labels are throttled; re-projecting 460 nodes every frame is wasted
      // work when the camera moves a fraction of a degree between frames.
      this._lf = (this._lf || 0) + 1;
      if (this._lf % 3 === 0) this._syncLabels();
    }
  }

  global.Galaxy = Galaxy;
  global.GALAXY_KINDS = KIND_COLOR;
})(window);
