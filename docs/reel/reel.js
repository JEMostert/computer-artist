// Computer Artist — agent reel. 15 s, 1920x1080, 60 fps, 128 BPM (8 bars, one section per bar).
// Every frame is a pure function of time t; motion blur averages sub-frames around t.
const Reel = (() => {
  const W = 1920, H = 1080, FPS = 60, BPM = 128, BEAT = 60 / BPM, BAR = 4 * BEAT, DUR = 15;
  const C = {
    ink: '#0d0f0e', paper: '#f1ede4', pen: '#10b48a', penDeep: '#0a7c60', penDark: '#053b2f',
    sheen: '#9cf2d6', cobalt: '#2438ff', lemon: '#ffd83d', coral: '#ff5b3a', white: '#fbfbf8',
  };
  const F = { x: '"ArchivoXB"', xm: '"ArchivoXM"', serif: '"Instrument Serif"', mono: '"JBMono"' };
  const IMG = {};
  const SECTIONS = ['NIB', 'IDENTITY', 'TWO POINTERS', 'ENVIRONMENTS', 'BROWSER', 'PROGRAMS', 'ARTIST', 'YOURS'];

  // ------------------------------------------------------------------ math
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, k) => a + (b - a) * k;
  const map = (t, a, b) => clamp((t - a) / (b - a));
  const E = {
    outExpo: (k) => (k >= 1 ? 1 : 1 - Math.pow(2, -10 * k)),
    inExpo: (k) => (k <= 0 ? 0 : Math.pow(2, 10 * k - 10)),
    inOut: (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2),
    outCubic: (k) => 1 - Math.pow(1 - k, 3),
    inCubic: (k) => k * k * k,
    outQuint: (k) => 1 - Math.pow(1 - k, 5),
    outBack: (k, s = 1.9) => 1 + (s + 1) * Math.pow(k - 1, 3) + s * Math.pow(k - 1, 2),
    spring: (k, f = 3.2, d = 5.5) => (k <= 0 ? 0 : 1 - Math.exp(-d * k) * Math.cos(f * Math.PI * k)),
  };
  let seed = 1;
  const rand = (s) => { const x = Math.sin(s * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

  // ------------------------------------------------------------- drawing
  function txt(ctx, s, x, y, o = {}) {
    ctx.save();
    ctx.font = `${o.weight || ''} ${o.size || 24}px ${o.font || F.mono}`;
    ctx.fillStyle = o.color || C.ink;
    ctx.textAlign = o.align || 'left';
    ctx.textBaseline = o.base || 'alphabetic';
    ctx.letterSpacing = (o.spacing || 0) + 'px';
    if (o.alpha != null) ctx.globalAlpha *= o.alpha;
    if (o.stroke) { ctx.strokeStyle = o.stroke; ctx.lineWidth = o.lw || 2; ctx.lineJoin = 'round'; ctx.strokeText(s, x, y); }
    else ctx.fillText(s, x, y);
    ctx.restore();
  }
  function measure(ctx, s, size, font, spacing = 0) {
    ctx.save(); ctx.font = `${size}px ${font}`; ctx.letterSpacing = spacing + 'px';
    const w = ctx.measureText(s).width; ctx.restore(); return w;
  }
  function rrect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.roundRect(x, y, w, h, r);
  }
  // The agent pen, from pen.js geometry (along/across from the nib tip).
  const PEN_PARTS = [
    ['18.6,-5.2 36,-5.2 38,-3.9 38.8,0 38,3.9 36,5.2 18.6,5.2', 'pen'],
    ['18.6,.8 36.4,.8 38.3,2.6 38,3.9 36,5.2 18.6,5.2', 'penDeep'],
    ['19.6,-3.7 34.6,-3.7 34.6,-2.2 19.6,-2.2', 'sheen'],
    ['12.5,-4.4 17,-4.7 17,4.7 12.5,4.4', 'penDark'],
    ['17,-5.1 18.6,-5.1 18.6,5.1 17,5.1', '#d2ece3'],
    ['0,0 3,-1.2 6.5,-2.6 10,-3.8 12.5,-4.2 12.5,4.2 10,3.8 6.5,2.6 3,1.2', '#fcfffd'],
    ['0,0 12.5,0 12.5,4.2 10,3.8 6.5,2.6 3,1.2', '#d3eee5'],
  ].map(([p, c]) => [p.split(' ').map((q) => q.split(',').map(Number)), c]);
  const PEN_OUTLINE = '0,0 3,-1.2 6.5,-2.6 10,-3.8 12.5,-4.2 12.5,-4.4 17,-4.7 17,-5.1 18.6,-5.2 36,-5.2 38,-3.9 38.8,0 38,3.9 36,5.2 18.6,5.2 17,5.1 17,4.7 12.5,4.4 12.5,4.2 10,3.8 6.5,2.6 3,1.2'
    .split(' ').map((q) => q.split(',').map(Number));
  function poly(ctx, pts) { ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); }
  function pen(ctx, x, y, scale = 1, rot = -50, glow = true) {
    ctx.save(); ctx.translate(x, y); ctx.rotate((rot * Math.PI) / 180); ctx.scale(1.15 * scale, 1.15 * scale);
    if (glow) { ctx.save(); ctx.filter = `blur(${3.5 * scale}px)`; ctx.globalAlpha *= 0.5; poly(ctx, PEN_OUTLINE); ctx.fillStyle = C.pen; ctx.fill(); ctx.restore(); }
    for (const [p, c] of PEN_PARTS) { poly(ctx, p); ctx.fillStyle = C[c] || c; ctx.fill(); }
    poly(ctx, PEN_OUTLINE); ctx.strokeStyle = C.penDark; ctx.lineWidth = 1; ctx.lineJoin = 'round'; ctx.stroke();
    ctx.beginPath(); ctx.moveTo(1.4, 0); ctx.lineTo(8.2, 0); ctx.stroke();
    ctx.beginPath(); ctx.arc(8.9, 0, 0.9, 0, 7); ctx.fillStyle = C.penDark; ctx.fill();
    ctx.beginPath(); ctx.moveTo(22, -6.1); ctx.lineTo(33.5, -6.1); ctx.strokeStyle = '#d2ece3'; ctx.lineWidth = 1.6; ctx.lineCap = 'round'; ctx.stroke();
    ctx.restore();
  }
  // Owner tag, shaped like pen.js's chip: rounded, near-square bottom-left.
  function tag(ctx, x, y, label, size = 18, bg = C.penDeep, fg = '#fff', alpha = 1) {
    if (alpha <= 0) return;
    ctx.save(); ctx.globalAlpha *= alpha;
    const w = measure(ctx, label, size, F.mono) + size * 1.2, h = size * 1.7, r = h * 0.55;
    ctx.beginPath(); ctx.roundRect(x, y, w, h, [r, r, r, size * 0.15]); ctx.fillStyle = bg; ctx.fill();
    txt(ctx, label, x + size * 0.6, y + h * 0.68, { size, color: fg, weight: 800 });
    ctx.restore();
    return w;
  }
  function arrow(ctx, x, y, s = 1, color = C.ink, edge = '#fff') {
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, 30); ctx.lineTo(7, 23); ctx.lineTo(12, 35); ctx.lineTo(17, 33); ctx.lineTo(12, 21); ctx.lineTo(22, 21); ctx.closePath();
    ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = edge; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke(); ctx.restore();
  }
  // A tapered ink stroke along pts (array of [x,y]) up to fraction k, fading toward its tail.
  function ink(ctx, pts, k, width = 10, color = C.pen, fade = 1) {
    const n = Math.max(2, Math.floor(pts.length * clamp(k)));
    ctx.save(); ctx.lineCap = 'round'; ctx.strokeStyle = color;
    for (let i = 1; i < n; i++) {
      const age = (n - i) / pts.length;
      ctx.globalAlpha = clamp(1 - age * fade * 1.2, 0.08, 1);
      ctx.lineWidth = width * (i > n - 6 ? 0.55 + 0.45 * ((n - i) / 6) : 1) * (0.7 + 0.3 * Math.sin(i * 0.15) ** 2);
      ctx.beginPath(); ctx.moveTo(...pts[i - 1]); ctx.lineTo(...pts[i]); ctx.stroke();
    }
    ctx.restore();
    return pts[n - 1];
  }
  function splash(ctx, x, y, k, color = C.pen, r = 34) {
    if (k <= 0 || k >= 1) return;
    ctx.save(); ctx.globalAlpha = 1 - k; ctx.strokeStyle = color; ctx.lineWidth = 4 * (1 - k) + 1;
    ctx.beginPath(); ctx.arc(x, y, r * E.outExpo(k), 0, 7); ctx.stroke();
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2, d = r * 0.6 + r * 0.9 * E.outExpo(k);
      ctx.beginPath(); ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, 3 * (1 - k), 0, 7); ctx.fillStyle = color; ctx.fill();
    }
    ctx.restore();
  }
  function frame(ctx, img, cx, cy, h, rot = 0, alpha = 1) {
    const w = (h * img.width) / img.height, mat = h * 0.05, fr = Math.max(3, h * 0.012);
    ctx.save(); ctx.globalAlpha *= alpha; ctx.translate(cx, cy); ctx.rotate(rot);
    ctx.shadowColor = 'rgba(0,0,0,.35)'; ctx.shadowBlur = h * 0.08; ctx.shadowOffsetY = h * 0.03;
    ctx.fillStyle = C.ink; ctx.fillRect(-w / 2 - mat - fr, -h / 2 - mat - fr, w + 2 * (mat + fr), h + 2 * (mat + fr));
    ctx.shadowColor = 'transparent';
    ctx.fillStyle = C.white; ctx.fillRect(-w / 2 - mat, -h / 2 - mat, w + 2 * mat, h + 2 * mat);
    ctx.drawImage(img, -w / 2, -h / 2, w, h);
    ctx.restore();
  }

  // ------------------------------------------------------------------ HUD
  function hud(ctx, t, bar, light) {
    const fg = light ? C.ink : C.paper, dim = light ? 'rgba(13,15,14,.55)' : 'rgba(241,237,228,.6)';
    ctx.save(); ctx.strokeStyle = fg; ctx.lineWidth = 2;
    const m = 46, L = 26;
    for (const [x, y, dx, dy] of [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]]) {
      ctx.beginPath(); ctx.moveTo(x, y + dy * L); ctx.lineTo(x, y); ctx.lineTo(x + dx * L, y); ctx.stroke();
    }
    ctx.restore();
    txt(ctx, 'COMPUTER ARTIST', 82, 92, { size: 17, weight: 800, color: fg, spacing: 2.5 });
    txt(ctx, 'AGENT REEL — 2026', 290, 92, { size: 17, color: dim, spacing: 2.5 });
    txt(ctx, `0${bar + 1} — ${SECTIONS[bar]}`, W - 82, 92, { size: 17, weight: 800, color: fg, spacing: 2.5, align: 'right' });
    const f = Math.min(Math.floor(t * FPS + 1e-6), DUR * FPS - 1), s = Math.floor(f / FPS), fr = f % FPS;
    txt(ctx, `00:00:${String(s).padStart(2, '0')}:${String(fr).padStart(2, '0')}`, 82, H - 82, { size: 17, weight: 800, color: fg, spacing: 2.5 });
    txt(ctx, '60 FPS', 290, H - 82, { size: 17, color: dim, spacing: 2.5 });
    ctx.save(); ctx.fillStyle = fg; ctx.fillRect(46, H - 52, (W - 92) * clamp(t / DUR), 2); ctx.restore();
    txt(ctx, `BAR ${bar + 1}/8`, W - 82, H - 82, { size: 17, weight: 800, color: fg, spacing: 2.5, align: 'right' });
    const beat = Math.floor((t - bar * BAR) / BEAT);
    for (let i = 0; i < 4; i++) {
      const x = W - 300 + i * 22, y = H - 97;
      ctx.save(); ctx.strokeStyle = fg; ctx.lineWidth = 2; ctx.strokeRect(x, y, 14, 14);
      if (i === beat) { ctx.fillStyle = fg; ctx.fillRect(x, y, 14, 14); }
      ctx.restore();
    }
    txt(ctx, '128 BPM', W - 330, H - 82, { size: 17, color: dim, spacing: 2.5, align: 'right' });
  }

  // ------------------------------------------------------------- sections
  // u: seconds since the section's downbeat (0..BAR)
  function s1(ctx, u) {
    ctx.fillStyle = C.ink; ctx.fillRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2;
    for (let i = 0; i < 3; i++) {
      const k = map(u, 0.02 + i * 0.12, 0.9 + i * 0.12);
      if (k > 0 && k < 1) { ctx.save(); ctx.globalAlpha = (1 - k) * 0.7; ctx.strokeStyle = C.pen; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, 20 + 520 * E.outExpo(k), 0, 7); ctx.stroke(); ctx.restore(); }
    }
    // Rotating ring of words.
    const ringA = map(u, 0.15, 0.5);
    if (ringA > 0) {
      const words = 'THEIR OWN POINTER — REAL APPS — VERIFIED OUTCOMES — REMEMBERED WORK — ';
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(u * 0.9 - 1.2); ctx.globalAlpha = ringA * 0.55;
      ctx.font = `17px ${F.mono}`; ctx.fillStyle = C.paper; ctx.textAlign = 'center';
      const R = 360;
      for (let i = 0; i < words.length; i++) { ctx.save(); ctx.rotate((i / words.length) * Math.PI * 2); ctx.fillText(words[i], 0, -R); ctx.restore(); }
      ctx.restore();
      ctx.save(); ctx.globalAlpha = ringA * 0.25; ctx.strokeStyle = C.paper; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, 330, 0, 7); ctx.stroke(); ctx.restore();
    }
    // The pen writes an infinity loop.
    const pts = [];
    for (let i = 0; i <= 240; i++) { const s = i / 240; pts.push([cx + 300 * Math.sin(2 * Math.PI * s), cy + 140 * Math.sin(4 * Math.PI * s)]); }
    const wk = E.inOut(map(u, 0.38, 1.5));
    const dot = map(u, 0, 0.12);
    if (u < 0.42) { ctx.save(); ctx.fillStyle = C.pen; ctx.beginPath(); ctx.arc(cx, cy, 11 * E.outBack(dot), 0, 7); ctx.fill(); ctx.restore(); }
    if (wk > 0) {
      const tip = ink(ctx, pts, wk, 12, C.pen, 0.6);
      const ps = E.outBack(map(u, 0.3, 0.5));
      pen(ctx, tip[0], tip[1], 4.2 * ps);
      tag(ctx, tip[0] + 40, tip[1] + 30, 'agent', 20, C.penDeep, '#fff', map(u, 0.45, 0.6));
    } else if (u > 0.3) pen(ctx, cx, cy, 4.2 * E.outBack(map(u, 0.3, 0.5)));
    txt(ctx, 'every agent gets', cx, cy - 230, { font: F.serif, size: 54, color: C.paper, align: 'center', alpha: map(u, 0.6, 0.9) });
    txt(ctx, 'a hand of its own', cx, cy + 270, { font: F.serif, size: 54, color: C.paper, align: 'center', alpha: map(u, 0.8, 1.1) });
    // Green iris wipe into bar 2.
    const wipe = E.inCubic(map(u, 1.62, BAR));
    if (wipe > 0) { ctx.save(); ctx.fillStyle = C.pen; ctx.beginPath(); ctx.arc(cx, cy, 1150 * wipe, 0, 7); ctx.fill(); ctx.restore(); }
  }

  function slamWord(ctx, word, x, y, size, u, delay, dir) {
    // Letters arrive stretched along x, settle with a spring.
    const lw = [...word].map((ch) => measure(ctx, ch, size, F.x));
    let cx = x;
    [...word].forEach((ch, i) => {
      const k = map(u, delay + i * 0.018, delay + i * 0.018 + 0.42);
      if (k <= 0) { cx += lw[i]; return; }
      const sp = E.spring(k, 2.2, 6.5);
      const sx = lerp(2.4, 1, E.outExpo(k));
      ctx.save(); ctx.translate(cx + lw[i] / 2 + dir * (1 - sp) * 320, y); ctx.scale(sx, 1 + 0.05 * Math.sin(k * 9) * (1 - k));
      txt(ctx, ch, 0, 0, { font: F.x, size, color: C.ink, align: 'center' });
      ctx.restore();
      cx += lw[i];
    });
    return lw.reduce((a, b) => a + b, 0);
  }

  function s2(ctx, u) {
    ctx.fillStyle = C.pen; ctx.fillRect(0, 0, W, H);
    const rule = E.outExpo(map(u, 0, 0.45));
    ctx.save(); ctx.fillStyle = C.ink; ctx.fillRect(150, 236, 1620 * rule, 3); ctx.restore();
    txt(ctx, 'N° 01', 150, 220, { size: 17, weight: 800, spacing: 2.5, alpha: rule });
    txt(ctx, 'COMPUTER USE, BROWSER, PRIVATE DESKTOPS', 1770, 220, { size: 17, weight: 800, spacing: 2.5, align: 'right', alpha: rule });
    const size = Math.floor(236 * 1560 / measure(ctx, 'COMPUTER', 236, F.x));
    const w1 = measure(ctx, 'COMPUTER', size, F.x), w2 = measure(ctx, 'ARTIST', size, F.x);
    const beatPunch = 1 + 0.012 * Math.max(0, Math.cos(((u % BEAT) / BEAT) * Math.PI * 0.5) - 0.3) * (u > BEAT ? 1 : 0);
    ctx.save(); ctx.translate(W / 2, 560); ctx.scale(beatPunch, beatPunch); ctx.translate(-W / 2, -560);
    slamWord(ctx, 'COMPUTER', (W - w1) / 2, 520, size, u, 0.0, -1);
    const endX = (W - w2) / 2 + slamWord(ctx, 'ARTIST', (W - w2) / 2, 760, size, u, 0.1, 1);
    ctx.restore();
    const sub = E.outCubic(map(u, 0.55, 0.95));
    txt(ctx, 'agents with a pointer of their own', W / 2, 905 + 30 * (1 - sub), { font: F.serif, size: 92, color: C.paper, align: 'center', alpha: sub });
    // The pen underlines ARTIST and its tag pops up.
    const ul = E.inOut(map(u, 0.95, 1.45));
    if (ul > 0) {
      const x0 = (W - w2) / 2, pts = [];
      for (let i = 0; i <= 80; i++) { const s = i / 80; pts.push([x0 + (endX - x0) * s, 790 + 6 * Math.sin(s * 7)]); }
      const tip = ink(ctx, pts, ul, 9, C.penDark, 0.2);
      pen(ctx, tip[0], tip[1], 2.6, -50, false);
    }
    tag(ctx, endX + 24, 612, 'ca', 30, C.ink, C.pen, E.outBack(map(u, 0.85, 1.05)));
  }

  function s3(ctx, u) {
    ctx.fillStyle = C.paper; ctx.fillRect(0, 0, W, H);
    const a = E.outCubic(map(u, 0, 0.3));
    txt(ctx, 'Your pointer, and its own.', 150, 230, { font: F.serif, size: 92, alpha: a });
    txt(ctx, 'INDEPENDENT INPUT — KDE / MACOS / WINDOWS', 1770, 226, { size: 17, weight: 800, spacing: 2.5, align: 'right', alpha: a, color: 'rgba(13,15,14,.6)' });
    const win = (x, y, w, h, title, k) => {
      ctx.save(); ctx.globalAlpha = k; ctx.translate(0, 40 * (1 - k));
      rrect(ctx, x, y, w, h, 14); ctx.fillStyle = C.white; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = C.ink; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x, y + 52); ctx.lineTo(x + w, y + 52); ctx.stroke();
      for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.arc(x + 28 + i * 24, y + 26, 7, 0, 7); ctx.stroke(); }
      txt(ctx, title, x + w / 2, y + 33, { size: 17, weight: 800, align: 'center', spacing: 1.5 });
      ctx.restore();
    };
    const wk = E.outQuint(map(u, 0.05, 0.4));
    win(150, 300, 760, 620, 'notes.md — yours', wk);
    win(1010, 300, 760, 620, 'app under test — the agent', E.outQuint(map(u, 0.1, 0.45)));
    // You type in your editor.
    const lines = [520, 610, 460, 680, 390, 560];
    const typed = map(u, 0.35, 1.8) * lines.reduce((a, b) => a + b, 0);
    let acc = 0, caret = [190, 400];
    lines.forEach((L, i) => {
      const w = clamp(typed - acc, 0, L); acc += L;
      if (w > 0) { ctx.fillStyle = 'rgba(13,15,14,.82)'; ctx.fillRect(190, 395 + i * 64, w, 16); caret = [190 + w, 395 + i * 64]; }
    });
    // Your arrow wanders in your window.
    const hx = 600 + 160 * Math.sin(u * 2.3) + 60 * Math.sin(u * 5.1), hy = 640 + 120 * Math.cos(u * 1.7);
    if (wk > 0.5) { arrow(ctx, hx, hy, 1.6); tag(ctx, hx + 36, hy + 52, 'you', 17, C.ink, C.paper); }
    // The agent works the app: buttons, a slider and a checkbox.
    const bx = 1080, by = 400;
    const targets = [[bx + 120, by + 40], [bx + 380, by + 40], [bx + 600, by + 40], [bx + 120, by + 300], [bx + 560, by + 300], [bx + 120, by + 430]];
    const stops = [0.3516, 0.7031, 1.1719, 1.29, 1.52, 1.7578]; // on the soundtrack's stabs
    const gk = E.outQuint(map(u, 0.1, 0.45));
    if (gk > 0) {
      ctx.save(); ctx.globalAlpha = gk;
      ['Save', 'Export', 'Share'].forEach((label, i) => {
        const on = u > stops[i] + 0.05;
        rrect(ctx, bx + 30 + i * 230, by, 180, 80, 12); ctx.fillStyle = on ? C.pen : C.paper; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = C.ink; ctx.stroke();
        txt(ctx, label, bx + 120 + i * 230, by + 50, { font: F.xm, size: 26, align: 'center' });
      });
      // Slider dragged from the left to the right.
      const sk = E.inOut(map(u, stops[3], stops[4]));
      ctx.fillStyle = C.ink; ctx.fillRect(bx + 120, by + 297, 440, 6);
      ctx.fillStyle = C.pen; ctx.fillRect(bx + 120, by + 297, 440 * sk, 6);
      ctx.beginPath(); ctx.arc(bx + 120 + 440 * sk, by + 300, 22, 0, 7); ctx.fillStyle = C.white; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = C.ink; ctx.stroke();
      // Checkbox.
      rrect(ctx, bx + 100, by + 410, 40, 40, 8); ctx.fillStyle = u > stops[5] + 0.03 ? C.pen : C.paper; ctx.fill(); ctx.lineWidth = 3; ctx.stroke();
      if (u > stops[5] + 0.03) { ctx.beginPath(); ctx.moveTo(bx + 110, by + 432); ctx.lineTo(bx + 118, by + 441); ctx.lineTo(bx + 132, by + 420); ctx.strokeStyle = C.ink; ctx.lineWidth = 4; ctx.stroke(); }
      txt(ctx, 'run nightly', bx + 165, by + 442, { font: F.xm, size: 24 });
      ctx.restore();
      // Pen path between targets.
      let px = bx + 700, py = by + 520;
      const path = [[px, py]];
      for (let i = 0; i < stops.length; i++) {
        const k0 = i ? stops[i - 1] + 0.06 : 0.2, k = E.inOut(map(u, k0, stops[i]));
        const from = i ? targets[i - 1] : [bx + 700, by + 520];
        if (u >= k0) { px = lerp(from[0], targets[i][0], k); py = lerp(from[1], targets[i][1], k) - 40 * Math.sin(Math.PI * k); }
      }
      for (let i = 0; i < stops.length; i++) splash(ctx, targets[i][0], targets[i][1], map(u, stops[i], stops[i] + 0.35));
      pen(ctx, px, py, 2.6);
      tag(ctx, px + 30, py + 26, 'agent', 17, C.penDeep, '#fff');
    }
  }

  function miniApp(ctx, kind, x, y, w, h, t, i) {
    ctx.save(); rrect(ctx, x, y, w, h, Math.max(4, w * 0.04)); ctx.fillStyle = C.paper; ctx.fill(); ctx.clip();
    ctx.fillStyle = 'rgba(13,15,14,.1)'; ctx.fillRect(x, y, w, h * 0.12);
    const s = w / 400;
    if (kind === 0) { // game: a hopping block and coins
      ctx.fillStyle = C.ink; ctx.fillRect(x, y + h * 0.82, w, h * 0.04);
      const jx = x + ((t * 120 * s + i * 50) % (w * 0.8)) + w * 0.05, jy = y + h * 0.82 - h * 0.12 - Math.abs(Math.sin(t * 5 + i)) * h * 0.3;
      ctx.fillStyle = C.coral; ctx.fillRect(jx, jy, h * 0.12, h * 0.12);
      for (let c = 0; c < 4; c++) { ctx.beginPath(); ctx.arc(x + w * (0.2 + c * 0.2), y + h * 0.45, h * 0.04, 0, 7); ctx.fillStyle = C.lemon; ctx.fill(); }
    } else if (kind === 1) { // form filling
      for (let r = 0; r < 3; r++) { ctx.strokeStyle = C.ink; ctx.lineWidth = Math.max(1, 2 * s); ctx.strokeRect(x + w * 0.1, y + h * (0.24 + r * 0.22), w * 0.8, h * 0.13); ctx.fillStyle = C.ink; ctx.fillRect(x + w * 0.13, y + h * (0.29 + r * 0.22), w * 0.6 * clamp(((t * 1.4 + i * 0.3) % 2) - r * 0.5), h * 0.04); }
    } else if (kind === 2) { // chart
      for (let b = 0; b < 6; b++) { const bh = h * 0.6 * (0.3 + 0.7 * Math.abs(Math.sin(t * 2 + b + i))); ctx.fillStyle = b % 2 ? C.cobalt : C.pen; ctx.fillRect(x + w * (0.1 + b * 0.14), y + h * 0.9 - bh, w * 0.1, bh); }
    } else { // paint
      ctx.strokeStyle = C.pen; ctx.lineWidth = Math.max(2, 10 * s); ctx.lineCap = 'round'; ctx.beginPath();
      for (let k = 0; k <= 30; k++) { const q = k / 30; const px = x + w * (0.1 + 0.8 * q), py = y + h * (0.55 + 0.25 * Math.sin(q * 8 + t * 3 + i)); k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
      ctx.stroke();
    }
    ctx.restore();
    // The agent's own pen in each desktop.
    const px = x + w * (0.5 + 0.35 * Math.sin(t * 1.7 + i * 1.3)), py = y + h * (0.55 + 0.3 * Math.cos(t * 2.1 + i));
    pen(ctx, px, py, Math.max(0.5, 1.6 * s), -50, false);
  }

  function s4(ctx, u, t) {
    ctx.fillStyle = C.cobalt; ctx.fillRect(0, 0, W, H);
    const beat = Math.min(3, Math.floor(u / BEAT)), n = beat + 1, N = n * n;
    const k = E.spring(map(u - beat * BEAT, 0, 0.42), 2.4, 6);
    const area = { x: 760, y: 170, w: 1010, h: 740 };
    const layout = (m, j) => { const gw = area.w / m, gh = area.h / m, r = Math.floor(j / m), c = j % m; return [area.x + c * gw + gw * 0.06, area.y + r * gh + gh * 0.06, gw * 0.88, gh * 0.88]; };
    for (let j = 0; j < N; j++) {
      const to = layout(n, j);
      const from = beat > 0 && j < beat * beat ? layout(beat, j) : [to[0] + to[2] / 2, to[1] + to[3] / 2, 0, 0];
      const kk = beat === 0 ? E.outBack(map(u, 0, 0.35)) : k;
      const r = from.map((v, q) => lerp(v, to[q], kk));
      if (r[2] < 4) continue;
      miniApp(ctx, j % 4, r[0], r[1], r[2], r[3], t, j);
      if (n <= 3) txt(ctx, `qa-${String(j + 1).padStart(2, '0')}`, r[0] + 10, r[1] + r[3] * 0.09, { size: Math.max(10, 18 - n * 2), weight: 800 });
    }
    const count = [1, 4, 9, 16][beat];
    txt(ctx, 'ca env start qa-' + String(count).padStart(2, '0'), 150, 300, { size: 26, weight: 800, color: C.paper });
    const pop = E.outBack(map(u - beat * BEAT, 0, 0.25));
    ctx.save(); ctx.translate(150, 640); ctx.scale(pop, pop);
    txt(ctx, String(count), 0, 0, { font: F.x, size: 330, color: C.paper });
    ctx.restore();
    txt(ctx, count === 1 ? 'private desktop' : 'private desktops', 155, 740, { font: F.serif, size: 70, color: C.paper });
    txt(ctx, 'OWN KWIN / SEAT / CLIPBOARD / BUS', 155, 800, { size: 17, weight: 800, spacing: 2.5, color: 'rgba(241,237,228,.75)' });
    txt(ctx, 'apps, games, a browser kept apart', 155, 845, { size: 20, color: 'rgba(241,237,228,.75)' });
  }

  const TABS = ['qa-signup', 'qa-cart', 'qa-search', 'qa-billing', 'qa-profile'];
  function s5(ctx, u, t) {
    ctx.fillStyle = C.ink; ctx.fillRect(0, 0, W, H);
    const bx = 150, bw = 1620, by = 170;
    rrect(ctx, bx, by, bw, 720, 18); ctx.fillStyle = '#1b2023'; ctx.fill();
    // Tab strip: your tab, then a CA group with one tab per agent.
    rrect(ctx, bx + 16, by + 14, 220, 44, 10); ctx.fillStyle = C.paper; ctx.fill();
    txt(ctx, 'your tab', bx + 40, by + 43, { size: 18, weight: 800 });
    rrect(ctx, bx + 256, by + 22, 52, 30, 8); ctx.fillStyle = C.penDeep; ctx.fill();
    txt(ctx, 'CA', bx + 282, by + 43, { size: 17, weight: 800, color: '#fff', align: 'center' });
    const colW = 250, gap = 18, x0 = bx + 328;
    TABS.forEach((name, i) => {
      const k = E.outBack(map(u, 0.05 + i * 0.09, 0.32 + i * 0.09));
      if (k <= 0) return;
      ctx.save(); ctx.globalAlpha = clamp(k); ctx.translate(0, -30 * (1 - k));
      rrect(ctx, x0 + i * (colW + gap), by + 14, colW, 44, 10); ctx.strokeStyle = C.pen; ctx.lineWidth = 2; ctx.stroke();
      txt(ctx, 'localhost:5173', x0 + i * (colW + gap) + 20, by + 43, { size: 16, color: C.paper });
      // Page per tab.
      const px = x0 + i * (colW + gap), py = by + 90, ph = 600;
      rrect(ctx, px, py, colW, ph, 10); ctx.fillStyle = C.paper; ctx.fill();
      txt(ctx, name.replace('qa-', ''), px + 18, py + 44, { font: F.xm, size: 24 });
      const act = map(u, 0.45 + i * 0.07, 1.6 + i * 0.05);
      // Field being typed, a button clicked.
      ctx.strokeStyle = C.ink; ctx.lineWidth = 2; ctx.strokeRect(px + 18, py + 90, colW - 36, 48);
      ctx.fillStyle = C.ink; ctx.fillRect(px + 30, py + 108, (colW - 70) * clamp(act * 1.8), 12);
      const clicked = act > 0.7;
      rrect(ctx, px + 18, py + 170, colW - 36, 56, 10); ctx.fillStyle = clicked ? C.pen : C.ink; ctx.fill();
      txt(ctx, clicked ? 'done' : 'submit', px + colW / 2, py + 206, { size: 20, weight: 800, align: 'center', color: clicked ? C.ink : C.paper });
      for (let r = 0; r < 5; r++) { ctx.fillStyle = 'rgba(13,15,14,.12)'; ctx.fillRect(px + 18, py + 270 + r * 44, (colW - 36) * (0.5 + 0.5 * rand(i * 7 + r)), 14); }
      if (act > 0.9) { txt(ctx, '✓ verified', px + 18, py + 560, { size: 20, weight: 800, color: C.penDeep }); }
      // Pen glides from field to button.
      const pk = E.inOut(map(act, 0.5, 0.7));
      const pxn = act < 0.5 ? px + 30 + (colW - 70) * clamp(act * 1.8) : lerp(px + colW - 40, px + colW / 2, pk);
      const pyn = act < 0.5 ? py + 128 : lerp(py + 128, py + 198, pk);
      splash(ctx, px + colW / 2, py + 198, map(act, 0.7, 0.95));
      pen(ctx, pxn, pyn, 1.8);
      tag(ctx, pxn + 22, pyn + 20, name, 14, C.penDeep, '#fff');
      ctx.restore();
    });
    // Your arrow rests in your own tab, untouched.
    arrow(ctx, bx + 120, by + 300, 1.3, C.paper, C.ink);
    txt(ctx, 'yours stays yours', bx + 30, by + 380, { font: F.serif, size: 30, color: C.paper });
    const cap = E.outCubic(map(u, 0.9, 1.25));
    txt(ctx, 'Five features, tested at once.', W / 2, 965, { font: F.serif, size: 66, color: C.paper, align: 'center', alpha: cap });
  }

  const WORDS = ['OBSERVE', 'ACT', 'VERIFY', 'REMEMBER'];
  function s6(ctx, u, t) {
    ctx.fillStyle = C.lemon; ctx.fillRect(0, 0, W, H);
    const beat = Math.min(3, Math.floor(u / BEAT)), word = WORDS[beat], bk = (u - beat * BEAT) / BEAT;
    const size = 200;
    const rows = [-1, 0, 1];
    rows.forEach((r) => {
      const y = 610 + r * 225;
      const w = measure(ctx, word + ' ', size, F.x);
      const off = ((u * (r % 2 ? 520 : -520) + r * 300) % w + w) % w;
      for (let x = -off - w; x < W + w; x += w) {
        if (r === 0) continue;
        txt(ctx, word, x, y, { font: F.x, size, stroke: 'rgba(13,15,14,.55)', lw: 2.5 });
      }
    });
    const slam = E.outExpo(clamp(bk / 0.35));
    const w0 = measure(ctx, word, size * 1.25, F.x);
    ctx.save(); ctx.translate(W / 2, 610); ctx.scale(lerp(1.8, 1, slam), 1);
    txt(ctx, word, 0, 0, { font: F.x, size: size * 1.25, color: C.ink, align: 'center' });
    ctx.restore();
    // Code chip and result.
    const chip = 'ca execute --remember export-report';
    rrect(ctx, 150, 900, 640, 64, 12); ctx.fillStyle = C.ink; ctx.fill();
    txt(ctx, chip.slice(0, Math.floor(chip.length * map(u, 0.1, 1.1))), 178, 942, { size: 22, weight: 800, color: C.pen });
    const res = E.outBack(map(u, 1.2, 1.5));
    ctx.save(); ctx.globalAlpha = clamp(res); rrect(ctx, 1290, 900, 480, 64, 12); ctx.fillStyle = C.penDeep; ctx.fill();
    txt(ctx, 'verified   53.9 s → 19.5 s', 1318, 942, { size: 22, weight: 800, color: '#fff' }); ctx.restore();
    txt(ctx, 'Programs check their own outcome. Memory reuses them.', W / 2, 196, { font: F.serif, size: 46, align: 'center', alpha: map(u, 0.2, 0.5) });
  }

  const ART = [
    ['terra', 'Fjord at golden hour', 'GPT-6.1 TERRA'], ['astra', 'Isometric lighthouse island', 'GPT-6 ASTRA'],
    ['sol', 'Harmonic field', 'GPT-6.1 SOL'], ['opus', 'The cartographer', 'CLAUDE OPUS 5.5'], ['sonnet', 'Lemons and blue jug', 'CLAUDE SONNET 5.5'],
  ];
  function s7(ctx, u) {
    ctx.fillStyle = C.paper; ctx.fillRect(0, 0, W, H);
    const arrive = [0, BEAT, 2 * BEAT, 3 * BEAT, 3.5 * BEAT];
    const slots = ART.map((_, i) => [330 + i * 315, 905]);
    let cur = -1;
    arrive.forEach((a, i) => { if (u >= a) cur = i; });
    // Settled ones in the row.
    ART.forEach(([key], i) => {
      if (i >= cur && u < BAR - 0.18) return;
      const leave = i < cur ? arrive[i + 1] : BAR - 0.18;
      const k = E.outQuint(map(u, leave, leave + 0.25));
      const img = IMG[key];
      const cx = lerp(810, slots[i][0], k), cy = lerp(560, slots[i][1], k);
      const h = lerp(500, 150, k);
      frame(ctx, img, cx, cy, h, lerp(0, (i % 2 ? 1 : -1) * 0.03, k));
    });
    if (cur >= 0 && u < BAR - 0.18) {
      const [key, title, model] = ART[cur];
      const k = E.outExpo(map(u, arrive[cur], arrive[cur] + 0.22));
      frame(ctx, IMG[key], 810, 560, 500 * lerp(1.3, 1, k), lerp((cur % 2 ? -1 : 1) * 0.08, 0, k), clamp(k * 3));
      txt(ctx, title, 1290, 500, { font: F.serif, size: 60, alpha: k, align: 'left' });
      txt(ctx, model, 1292, 552, { size: 18, weight: 800, spacing: 2.5, alpha: k });
      txt(ctx, 'painted in Kleki, through ca browser', 1292, 588, { size: 18, spacing: 2.5, alpha: k, color: 'rgba(13,15,14,.6)' });
    }
    txt(ctx, 'Five models, five tabs, one browser.', 150, 215, { font: F.serif, size: 66 });
  }

  function s8(ctx, u, t) {
    ctx.fillStyle = C.pen; ctx.fillRect(0, 0, W, H);
    const cx = W / 2, cy = 470;
    for (let i = 0; i < 4; i++) {
      const k = map(u, i * 0.05, 0.7 + i * 0.05);
      if (k > 0 && k < 1) { ctx.save(); ctx.globalAlpha = 1 - k; ctx.strokeStyle = i % 2 ? C.paper : C.ink; ctx.lineWidth = 6 * (1 - k) + 1; ctx.beginPath(); ctx.arc(cx, cy, 40 + 900 * E.outExpo(k), 0, 7); ctx.stroke(); ctx.restore(); }
    }
    // Title assembles from scattered letters.
    const word = 'COMPUTER ARTIST', size = Math.floor(150 * 1500 / measure(ctx, 'COMPUTER ARTIST', 150, F.x));
    const total = measure(ctx, word, size, F.x);
    let x = cx - total / 2;
    [...word].forEach((ch, i) => {
      const w = measure(ctx, ch, size, F.x);
      const k = E.outExpo(map(u, 0.04 + i * 0.008, 0.36 + i * 0.008));
      const sx = (rand(i) - 0.5) * 1400, sy = (rand(i + 9) - 0.5) * 900, rot = (rand(i + 3) - 0.5) * 2;
      ctx.save(); ctx.translate(lerp(x + w / 2 + sx, x + w / 2, k), lerp(cy + 60 + sy, cy + 60, k)); ctx.rotate(rot * (1 - k));
      txt(ctx, ch, 0, 0, { font: F.x, size, color: C.ink, align: 'center', alpha: clamp(k * 2) });
      ctx.restore();
      x += w;
    });
    const sub = E.outCubic(map(u, 0.5, 0.85));
    txt(ctx, 'agents with a pointer of their own', cx, 640 + 20 * (1 - sub), { font: F.serif, size: 80, color: C.paper, align: 'center', alpha: sub });
    // The pen signs underneath.
    const sk = E.inOut(map(u, 0.55, 1.15));
    if (sk > 0) {
      const pts = [];
      for (let i = 0; i <= 120; i++) { const s = i / 120; pts.push([cx - 520 + 1040 * s, 735 + 12 * Math.sin(s * 10)]); }
      const tip = ink(ctx, pts, sk, 8, C.penDark, 0.15);
      if (sk < 1) pen(ctx, tip[0], tip[1], 2.2, -50, false);
    }
    const ft = E.outCubic(map(u, 0.9, 1.2));
    const cmds = ['ca setup --codex', 'ca env start NAME', 'ca browser install', 'github.com/JEMostert/computer-artist'];
    let fx = 150;
    cmds.forEach((c, i) => {
      const w = measure(ctx, c, 22, F.mono) + 44;
      ctx.save(); ctx.globalAlpha = clamp(ft * 1.5 - i * 0.15); rrect(ctx, fx, 850, w, 56, 12); ctx.fillStyle = i === 3 ? C.paper : C.ink; ctx.fill();
      txt(ctx, c, fx + 22, 886, { size: 22, weight: 800, color: i === 3 ? C.ink : C.pen }); ctx.restore();
      fx += w + 22;
    });
    if (u > 1.15) pen(ctx, cx + 520, 735 + 12 * Math.sin(10), 2.2, -50, false);
  }

  const SECTION_FN = [s1, s2, s3, s4, s5, s6, s7, s8];
  const LIGHT = [false, true, true, false, false, true, true, true];

  function draw(ctx, t) {
    t = clamp(t, 0, DUR - 1e-6);
    const bar = Math.min(7, Math.floor(t / BAR)), u = t - bar * BAR;
    ctx.save();
    // Entry punch on every downbeat after the first.
    const p = bar > 0 ? E.outExpo(map(u, 0, 0.16)) : 1;
    ctx.translate(W / 2, H / 2); ctx.scale(lerp(1.05, 1, p), lerp(1.05, 1, p)); ctx.translate(-W / 2, -H / 2);
    const st = t - 8.90625;
    if (st >= 0 && st < 0.32) {
      const step = Math.floor(st / (BEAT / 8)), d = 1 - st / 0.32;
      ctx.translate((step % 2 ? 1 : -1) * 26 * d, (step % 3 - 1) * 6 * d);
    }
    SECTION_FN[bar](ctx, u, t);
    ctx.restore();
    if (st >= 0 && st < 0.32) { // a thin slice band that hops with the stutter
      const step = Math.floor(st / (BEAT / 8)), y = 160 + ((step * 337) % 700);
      ctx.save(); ctx.globalAlpha = 0.9 * (1 - st / 0.32); ctx.fillStyle = C.pen; ctx.fillRect(0, y, W, 6 + (step % 3) * 8); ctx.restore();
    }
    // Flash frame on the cut.
    if (bar > 0 && u < 2 / FPS) { ctx.save(); ctx.globalAlpha = 0.35 * (1 - u * FPS / 2); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H); ctx.restore(); }
    hud(ctx, t, bar, LIGHT[bar] && !(bar === 0));
  }

  let off, offCtx, acc;
  function renderFrame(out, i, sub = 5, shutter = 0.6) {
    const t = i / FPS;
    const ctx = out.getContext('2d');
    if (sub <= 1) { draw(ctx, t); return; }
    if (!off) { off = document.createElement('canvas'); off.width = W; off.height = H; offCtx = off.getContext('2d', { willReadFrequently: true }); acc = new Float32Array(W * H * 4); }
    acc.fill(0);
    for (let s = 0; s < sub; s++) {
      const ts = t + ((s + 0.5) / sub - 0.5) * (shutter / FPS);
      offCtx.setTransform(1, 0, 0, 1, 0, 0); offCtx.clearRect(0, 0, W, H);
      draw(offCtx, ts);
      const d = offCtx.getImageData(0, 0, W, H).data;
      for (let k = 0; k < d.length; k++) acc[k] += d[k];
    }
    const img = ctx.createImageData(W, H), o = img.data, inv = 1 / sub;
    for (let k = 0; k < o.length; k++) o[k] = acc[k] * inv;
    ctx.putImageData(img, 0, 0);
  }

  async function load() {
    const faces = [
      new FontFace('ArchivoXB', 'url(fonts/ArchivoXB.ttf)'), new FontFace('ArchivoXM', 'url(fonts/ArchivoXM.ttf)'),
      new FontFace('Instrument Serif', 'url(fonts/InstrumentSerif-Italic.ttf)'),
      new FontFace('JBMono', 'url(fonts/Mono-500.ttf)', { weight: '500' }), new FontFace('JBMono', 'url(fonts/Mono-800.ttf)', { weight: '800' }),
    ];
    for (const f of faces) { await f.load(); document.fonts.add(f); }
    await Promise.all(ART.map(([k]) => new Promise((res, rej) => { const im = new Image(); im.onload = () => { IMG[k] = im; res(); }; im.onerror = rej; im.src = `../assets/gallery/${k}.jpg`; })));
  }

  return { load, renderFrame, draw, W, H, FPS, DUR };
})();
