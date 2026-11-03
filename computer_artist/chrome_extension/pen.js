// The agent's pen inside a claimed tab: the native ink pen (computer_artist/ink_cursor.py)
// in teal "Chrome lane" ink with the owner's name beside it, so a human can tell DOM
// control in Chrome from computer use at a glance. It glides to each target before ca
// acts, splashes on clicks, writes along fields and drips at rest. When the claim ends
// it lifts away. Injected on demand into the top frame of claimed tabs only. Purely
// visual: a closed shadow root that ignores pointer events and holds no page text.
if (!globalThis.__caPen) {
  const SVG = 'http://www.w3.org/2000/svg';
  // Pen parts as (along, across) the pen from the nib tip; see ink_cursor.py.
  const PARTS = [
    ['18.6,-5.2 36,-5.2 38,-3.9 38.8,0 38,3.9 36,5.2 18.6,5.2', 'barrel'],
    ['18.6,.8 36.4,.8 38.3,2.6 38,3.9 36,5.2 18.6,5.2', 'shade'],
    ['19.6,-3.7 34.6,-3.7 34.6,-2.2 19.6,-2.2', 'sheen'],
    ['12.5,-4.4 17,-4.7 17,4.7 12.5,4.4', 'section'],
    ['17,-5.1 18.6,-5.1 18.6,5.1 17,5.1', 'band'],
    ['0,0 3,-1.2 6.5,-2.6 10,-3.8 12.5,-4.2 12.5,4.2 10,3.8 6.5,2.6 3,1.2', 'nib'],
    ['0,0 12.5,0 12.5,4.2 10,3.8 6.5,2.6 3,1.2', 'nib-shade'],
  ];
  const OUTLINE =
    '0,0 3,-1.2 6.5,-2.6 10,-3.8 12.5,-4.2 12.5,-4.4 17,-4.7 17,-5.1 18.6,-5.2 36,-5.2 38,-3.9 ' +
    '38.8,0 38,3.9 36,5.2 18.6,5.2 17,5.1 17,4.7 12.5,4.4 12.5,4.2 10,3.8 6.5,2.6 3,1.2';
  const CSS = `
    :host { all: initial; }
    * { pointer-events: none; }
    .layer { position: fixed; inset: 0; overflow: visible; }
    .pen { position: fixed; left: 0; top: 0; will-change: transform; transition: opacity .2s; }
    .pen svg { position: absolute; left: 0; top: 0; overflow: visible; }
    .body { transform: rotate(-50deg) scale(1.15); transition: transform .12s ease-out; }
    .pressed .body { transform: rotate(-50deg) scale(1.02); }
    .glow { fill: #10b48a; opacity: .55; filter: blur(3.5px); }
    .barrel { fill: #10b48a; } .shade { fill: #0a7c60; } .sheen { fill: #9cf2d6; }
    .section { fill: #053b2f; } .band { fill: #d2ece3; } .nib { fill: #fcfffd; }
    .nib-shade { fill: #d3eee5; } .line { fill: none; stroke: #053b2f; stroke-width: 1; stroke-linejoin: round; }
    .clip { stroke: #d2ece3; stroke-width: 1.6; stroke-linecap: round; }
    .drop { fill: #10b48a; opacity: 0; }
    .resting .drop { animation: drip 2.6s ease-in infinite; }
    @keyframes drip {
      0% { opacity: 1; transform: translateY(3px) scale(.35); }
      80% { opacity: 1; transform: translateY(5px) scale(1); }
      82% { opacity: 1; transform: translateY(5px) scale(1); }
      100% { opacity: 0; transform: translateY(24px) scale(0); }
    }
    .chip {
      position: absolute; left: 12px; top: 10px; padding: 3px 8px 3px 7px; border-radius: 9px 9px 9px 2px;
      font: 600 11px/14px system-ui, -apple-system, "Segoe UI", sans-serif; letter-spacing: .01em;
      color: #fff; background: #0a7c60; white-space: nowrap; box-shadow: 0 1px 2px rgba(5, 59, 47, .35);
      max-width: 180px; overflow: hidden; text-overflow: ellipsis;
    }
    .trail line { stroke-linecap: round; }
    @media (prefers-reduced-motion: reduce) { .resting .drop { animation: none; } }
  `;
  const motion = () => !matchMedia('(prefers-reduced-motion: reduce)').matches;
  const visible = () => document.visibilityState === 'visible';
  const node = (tag, attrs = {}, parent) => {
    const el = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
    parent?.append(el);
    return el;
  };

  let host = null;
  let root = null;
  let pen = null;
  let chip = null;
  let trail = null;
  let at = null; // Nib tip in viewport pixels.
  let samples = []; // [time, x, y] behind the nib.
  let frame = 0;
  let glide = null;
  let leaving = null;
  let lift = null;

  const build = () => {
    host = document.createElement('ca-agent-pen');
    // Inline !important beats page styles on the host; the shadow root holds the rest.
    host.style.cssText =
      'all:initial!important;position:fixed!important;inset:0!important;margin:0!important;' +
      'padding:0!important;border:0!important;background:transparent!important;width:auto!important;' +
      'height:auto!important;overflow:visible!important;pointer-events:none!important;' +
      'z-index:2147483647!important;display:block!important;contain:layout style!important';
    host.setAttribute('aria-hidden', 'true');
    root = host.attachShadow({ mode: 'closed' });
    // A constructed sheet, unlike a <style> element, is not subject to the page's CSP.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    trail = node('svg', { class: 'layer trail', width: '100%', height: '100%' });
    pen = document.createElement('div');
    pen.className = 'pen';
    const svg = node('svg', { width: 1, height: 1 }, pen);
    node('ellipse', { class: 'drop', cx: 0, cy: 0, rx: 4.2, ry: 4.8 }, svg);
    const body = node('g', { class: 'body' }, svg);
    node('polygon', { class: 'glow', points: OUTLINE }, body);
    for (const [points, name] of PARTS) node('polygon', { class: name, points }, body);
    node('polygon', { class: 'line', points: OUTLINE }, body);
    node('path', { class: 'line', d: 'M1.4 0H8.2' }, body);
    node('circle', { class: 'section', cx: 8.9, cy: 0, r: 0.9 }, body);
    node('path', { class: 'clip', d: 'M22 -6.1H33.5' }, body);
    node('circle', { class: 'band', cx: 33.5, cy: -6.1, r: 1.2 }, body);
    chip = document.createElement('div');
    chip.className = 'chip';
    pen.append(chip);
    root.append(trail, pen);
  };

  const attach = () => {
    if (!host) build();
    if (!host.isConnected) (document.documentElement || document).append(host);
    // The top layer keeps the pen above the page's own modal dialogs and popovers.
    // Re-showing moves it above any that opened since.
    if (typeof host.showPopover === 'function') {
      try {
        host.popover = 'manual';
        if (host.matches(':popover-open')) host.hidePopover();
        host.showPopover();
      } catch (error) {
        // Detached or unsupported: the fixed, topmost host still shows the pen.
      }
    }
  };

  const clamp = (x, y) => ({
    x: Math.max(4, Math.min(innerWidth - 4, x)),
    y: Math.max(4, Math.min(innerHeight - 4, y)),
  });

  const place = (x, y, now = performance.now()) => {
    at = { x, y };
    pen.style.transform = `translate(${x}px, ${y}px)`;
    samples.push([now, x, y]);
    draw(now);
  };

  // Tapered, fading ink behind the nib, like the native trail.
  const draw = (now) => {
    samples = samples.filter((s) => now - s[0] < 450).slice(-24);
    trail.replaceChildren();
    for (let i = 1; i < samples.length; i++) {
      const [t, x1, y1] = samples[i - 1];
      const [, x2, y2] = samples[i];
      const fade = Math.max(0, 1 - (now - t) / 450);
      if (fade <= 0 || (x1 === x2 && y1 === y2)) continue;
      node('line', {
        x1, y1, x2, y2,
        stroke: i % 3 ? '#10b48a' : '#5fe0bb',
        'stroke-width': (0.7 + 5.5 * fade ** 0.8).toFixed(2),
        'stroke-opacity': (0.85 * fade ** 1.2).toFixed(3),
      }, trail); // prettier-ignore
    }
    if (samples.length > 1 && !frame) {
      frame = requestAnimationFrame(() => {
        frame = 0;
        draw(performance.now());
      });
    }
  };

  const rest = (resting) => pen.classList.toggle('resting', resting && motion());

  const animate = (el, keyframes, options) =>
    el.animate(keyframes, options).finished.then(() => el.remove(), () => el.remove());

  globalThis.__caPen = {
    // Glide to (x, y) and resolve on arrival, so ca acts where the pen lands.
    to(x, y, { owner } = {}) {
      leaving = null;
      lift?.cancel();
      lift = null;
      attach();
      chip.textContent = owner || 'ca';
      pen.style.opacity = '1';
      ({ x, y } = clamp(x, y));
      if (!visible() || !motion()) {
        samples = [];
        place(x, y);
        rest(true);
        return Promise.resolve();
      }
      rest(false);
      const from = at || { x: x + 46, y: y - 46 };
      if (!at) pen.style.opacity = '0';
      const distance = Math.hypot(x - from.x, y - from.y);
      const duration = distance < 4 ? 0 : Math.min(320, 120 + distance * 0.35);
      if (glide) glide.cancel();
      return new Promise((resolve) => {
        const began = performance.now();
        let id = 0;
        glide = { cancel: () => { cancelAnimationFrame(id); resolve(); } }; // prettier-ignore
        const step = (now) => {
          const progress = duration ? Math.min(1, (now - began) / duration) : 1;
          const eased = 1 - (1 - progress) ** 3;
          place(from.x + (x - from.x) * eased, from.y + (y - from.y) * eased, now);
          pen.style.opacity = String(Math.min(1, 0.25 + progress * 1.5));
          if (progress < 1 && visible()) {
            id = requestAnimationFrame(step);
          } else {
            if (progress < 1) place(x, y);
            glide = null;
            rest(true);
            resolve();
          }
        };
        id = requestAnimationFrame(step);
      });
    },

    // A click splashes an ink ring and droplets; a key press makes a small dab.
    tap(kind = 'click') {
      if (!at || !visible()) return;
      pen.classList.add('pressed');
      setTimeout(() => pen.classList.remove('pressed'), 140);
      if (!motion()) return;
      const big = kind === 'click';
      const splash = node('svg', { class: 'layer', width: '100%', height: '100%' });
      const ring = node('circle', { cx: at.x, cy: at.y, r: 4, fill: 'none', stroke: '#10b48a' }, splash);
      root.insertBefore(splash, pen);
      const span = big ? 28 : 14;
      ring.animate(
        [
          { r: '4px', strokeWidth: 3.4, strokeOpacity: 1 },
          { r: `${span}px`, strokeWidth: 0.8, strokeOpacity: 0 },
        ],
        { duration: 550, easing: 'cubic-bezier(.2,.8,.3,1)', fill: 'forwards' },
      );
      const drops = big ? 6 : 3;
      for (let i = 0; i < drops; i++) {
        const angle = (i / drops) * Math.PI * 2 + Math.random() * 0.8;
        const reach = span * (0.8 + Math.random() * 0.5);
        const drop = node('circle', { cx: at.x, cy: at.y, r: 1.6 + Math.random() * 1.4, fill: '#10b48a' }, splash);
        drop.animate(
          [
            { transform: 'translate(0, 0)', opacity: 0.9 },
            { transform: `translate(${Math.cos(angle) * reach}px, ${Math.sin(angle) * reach}px)`, opacity: 0 },
          ],
          { duration: 600, easing: 'cubic-bezier(.1,.7,.3,1)', fill: 'forwards' },
        );
      }
      animate(splash, [{ opacity: 1 }, { opacity: 1 }], { duration: 650 });
    },

    // After typing, the pen writes along the field; this does not delay ca.
    write(length, characters) {
      if (!at || !visible() || !motion() || length < 4) return;
      const start = { ...at };
      const duration = Math.min(600, 160 + characters * 22);
      const began = performance.now();
      rest(false);
      const step = (now) => {
        if (leaving || glide) return;
        const progress = Math.min(1, (now - began) / duration);
        const wave = Math.sin(progress * Math.PI * Math.max(2, Math.min(9, characters))) * 2.2;
        place(start.x + length * progress, start.y + wave, now);
        if (progress < 1 && visible()) {
          requestAnimationFrame(step);
        } else {
          rest(true);
        }
      };
      requestAnimationFrame(step);
    },

    // The claim ended: lift the pen off the page and remove it.
    leave(reason) {
      // A pending action reads this so a claim that ended mid-glide does not act,
      // including takeovers seen elsewhere, such as input inside an iframe.
      globalThis.__caClaimEnded = { at: performance.now(), reason: reason || 'released' };
      if (!host?.isConnected || leaving) return;
      if (glide) glide.cancel();
      leaving = reason || 'released';
      if (reason === 'human_input' || reason === 'human_activated_tab') chip.textContent = 'Back to you';
      rest(false);
      const done = () => {
        if (leaving !== (reason || 'released')) return;
        host.remove();
        at = null;
        samples = [];
        leaving = null;
      };
      // In a background tab this finishes once the human looks, which shows the handback.
      if (!motion()) return done();
      lift = pen.animate(
        [
          { transform: pen.style.transform, opacity: 1 },
          { transform: `${pen.style.transform} translate(10px, -18px)`, opacity: 0 },
        ],
        { duration: 700, delay: 500, easing: 'ease-in', fill: 'forwards' },
      );
      lift.finished.then(done, () => {});
    },
  };
}
