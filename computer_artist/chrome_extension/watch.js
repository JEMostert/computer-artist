// Reports the human's own input so the worker can end a claim on this tab.
// Declared for every frame at document_start, and injected into documents that
// were already open when a tab is claimed. Only trusted events count: ca's own
// DOM events are untrusted. Sends the event type only, at most every 250 ms.
if (!globalThis.__caHumanWatch) {
  globalThis.__caHumanWatch = true;
  let last = -Infinity;
  const report = (event) => {
    if (!event.isTrusted || event.timeStamp - last < 250) return;
    last = event.timeStamp;
    try {
      chrome.runtime.sendMessage({ type: 'ca-human-input', kind: event.type }).catch(() => {});
    } catch (error) {
      // The extension was reloaded; this document's watcher is orphaned.
    }
  };
  for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    addEventListener(type, report, { capture: true, passive: true });
  }
}
