// Reports the human's own input so the worker can pause the agent in this tab.
// Declared for every frame at document_start, and injected into documents that
// were already open when a tab is claimed or the extension restarts. Only trusted
// events count: ca's own DOM events are untrusted. Sends the event type only, at
// most every 250 ms.
(() => {
  // After an extension reload the old watcher here is orphaned: replace it.
  const live = () => {
    try {
      return Boolean(chrome.runtime?.id);
    } catch (error) {
      return false;
    }
  };
  const previous = globalThis.__caHumanWatch;
  if (previous?.live?.()) return;
  previous?.stop?.();
  const types = ['pointerdown', 'keydown', 'wheel', 'touchstart'];
  let last = -Infinity;
  const report = (event) => {
    if (!event.isTrusted) return;
    // pageAction reads this to hold back an action the human interrupted mid-glide.
    globalThis.__caHumanAt = performance.now();
    // In a claimed tab's top frame the pen shows the pause at once.
    globalThis.__caPen?.pause?.();
    if (event.timeStamp - last < 250) return;
    last = event.timeStamp;
    try {
      chrome.runtime.sendMessage({ type: 'ca-human-input', kind: event.type }).catch(() => {});
    } catch (error) {
      // The extension was reloaded; a fresh watcher replaces this one.
    }
  };
  for (const type of types) addEventListener(type, report, { capture: true, passive: true });
  // The worker puts the agent's pen on a claimed tab's new document as soon as it exists.
  if (window === top && !previous) {
    const ready = () => {
      try {
        chrome.runtime.sendMessage({ type: 'ca-document-ready' }).catch(() => {});
      } catch (error) {
        // The extension was reloaded; its worker restores pens itself.
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, { once: true });
  }
  globalThis.__caHumanWatch = {
    live,
    stop: () => types.forEach((type) => removeEventListener(type, report, { capture: true })),
  };
})();
