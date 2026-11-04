// Computer Artist browser bridge: a service worker that lets the local `ca`
// CLI list, claim and operate tabs through the native host
// `com.computer_artist.browser`. Actions run as DOM operations inside the page
// (untrusted events); nothing here moves the pointer, activates a tab or
// focuses a window. Claimed tabs carry a lease, a "CA" badge and, for tabs ca
// opened, a "CA" tab group. Human input in a claimed tab revokes the lease.
// Actions in a visible tab show the agent's pen (pen.js) in teal "Chrome lane"
// ink, distinct from the blue native pen. The toolbar popup (popup.js) shows the
// bridge and claim state that this worker keeps in session storage.

const HOST = 'com.computer_artist.browser';
const PROTOCOL = 1;
const VERSION = chrome.runtime.getManifest().version;
// ca browser install stamps the installed files' digest here, so ca can tell which
// build is running (the version alone does not change between installs).
const BUILD = '__CA_BUILD__';
const CAPABILITIES = ['pointer', 'frames', 'background_capture', 'agent_window', 'durable_claims', 'reload'];
let inFlight = 0; // requests being handled, so a reload never cuts one off
let reloading = false;
const BADGE = '#0a7c60'; // Chrome lane teal, shared with pen.js and the popup.
const GROUP_TITLE = 'CA';
const GROUP_COLOR = 'green';
const MAX_TEXT = 100000;
const MAX_READ = 200000;
const ALLOWED_SCHEMES = ['http:', 'https:', 'file:'];

let port = null;
let ready = false;
let retryDelay = 1000;
let retryTimer = null;
let identityPromise = null;
let statePromise = null;
// Track activation only to preserve a viewed tab if opening fails.
// Activation is observation, not evidence of human page input.
const opening = new Set();
const activatedWhileOpening = new Set();
const inputWhileOpening = new Set();

class Refusal extends Error {
  constructor(code, detail, extra = {}) {
    super(detail);
    this.code = code;
    this.extra = extra;
  }
}

// ---------------------------------------------------------------- identity

function browserName() {
  const brands = navigator.userAgentData?.brands || [];
  const named = brands.find((b) => !/not.?a.?brand|chromium/i.test(b.brand));
  const version = (navigator.userAgent.match(/Chrome\/([\d.]+)/) || [])[1] || '';
  return `${named?.brand || 'Chromium'} ${version}`.trim();
}

function identity() {
  identityPromise ??= (async () => {
    const stored = await chrome.storage.local.get(['instance', 'label']);
    let instance = stored.instance;
    if (!/^[a-z0-9]{8}$/.test(instance || '')) {
      instance = crypto.randomUUID().replaceAll('-', '').slice(0, 8);
      await chrome.storage.local.set({ instance });
    }
    return { instance, label: stored.label || '' };
  })();
  return identityPromise;
}

async function hello() {
  const id = await identity();
  return {
    type: 'hello',
    protocol: PROTOCOL,
    instance: id.instance,
    label: id.label,
    browser: browserName(),
    extension_version: VERSION,
    build: BUILD,
  };
}

// --------------------------------------------------------------- transport

function setConnected(value, detail) {
  ready = value;
  saveBridge(value ? 'connected' : 'disconnected', value ? null : detail);
  chrome.action.setBadgeText({ text: value ? '' : '!' });
  chrome.action.setBadgeBackgroundColor({ color: value ? BADGE : '#b3261e' });
  chrome.action.setTitle({
    title: value
      ? 'Computer Artist: connected to ca'
      : `Computer Artist: not connected to ca${detail ? ` (${detail})` : ''}. ` +
        'Run `ca browser install`, then reload this extension.',
  });
}

// The popup reads the bridge state from session storage rather than asking the worker.
function saveBridge(status, detail = null) {
  chrome.storage.session.set({ bridge: { status, detail: detail || null, at: Date.now() } }).catch(() => {});
}

function scheduleReconnect() {
  if (retryTimer) return;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 30000);
}

async function connect() {
  if (port) return;
  let current;
  try {
    current = chrome.runtime.connectNative(HOST);
  } catch (error) {
    setConnected(false, String(error.message || error));
    scheduleReconnect();
    return;
  }
  port = current;
  if (!ready) saveBridge('connecting');
  current.onDisconnect.addListener(() => {
    const detail = chrome.runtime.lastError?.message;
    if (port === current) port = null;
    setConnected(false, detail);
    scheduleReconnect();
  });
  current.onMessage.addListener((message) => onHostMessage(current, message));
  current.postMessage(await hello());
}

function onHostMessage(current, message) {
  if (message?.type === 'ready') {
    retryDelay = 1000;
    setConnected(true);
  } else if (message?.type === 'refused') {
    setConnected(false, message.detail || message.error);
  } else if (message?.type === 'request') {
    inFlight++;
    const work = reloading
      ? Promise.reject(new Refusal('busy', 'The extension is reloading; try again in a moment'))
      : handle(message);
    work.finally(() => inFlight--).then(
      (result) => post(current, { type: 'reply', id: message.id, ok: true, ...result }),
      (error) =>
        post(current, {
          type: 'reply',
          id: message.id,
          ok: false,
          error: error instanceof Refusal ? error.code : 'failed',
          detail: String(error?.message || error),
          ...(error instanceof Refusal ? error.extra : {}),
        }),
    );
  }
}

function post(current, message) {
  try {
    current.postMessage(message);
  } catch (error) {
    // The host went away while this request ran; the CLI sees the disconnect.
  }
}

// ------------------------------------------------------------------ claims

// Claims live in local storage so an extension reload or update keeps them. Session
// storage marks a running browser: when it is empty, Chrome restarted or the
// extension reloaded, and only claims whose tab still shows the claimed URL survive.
function state() {
  statePromise ??= (async () => {
    const stored = await chrome.storage.local.get(['claims', 'revoked']);
    const current = { claims: stored.claims || {}, revoked: stored.revoked || {} };
    const { alive } = await chrome.storage.session.get('alive');
    if (!alive) {
      for (const [id, claim] of Object.entries(current.claims)) {
        const tab = await chrome.tabs.get(Number(id)).catch(() => null);
        if (!tab || (tab.url || tab.pendingUrl) !== claim.url) delete current.claims[id];
      }
      await chrome.storage.local.set({ claims: current.claims, revoked: current.revoked });
      // Window IDs are reused after a restart: keep the agents' window only while a tab
      // ca opened there is still claimed.
      const { agentWindow } = await chrome.storage.local.get('agentWindow');
      if (agentWindow != null) {
        const tabs = await Promise.all(
          Object.entries(current.claims)
            .filter(([, claim]) => claim.opened)
            .map(([id]) => chrome.tabs.get(Number(id)).catch(() => null)),
        );
        if (!tabs.some((tab) => tab?.windowId === agentWindow)) await chrome.storage.local.remove('agentWindow');
      }
      await chrome.storage.session.set({ alive: true });
      // Documents from before the reload hold an orphaned watcher; give them a live one.
      for (const [id, claim] of Object.entries(current.claims)) {
        watchTab(Number(id)).catch(() => {});
        mark(Number(id), claim);
      }
    }
    return current;
  })();
  return statePromise;
}

async function persist() {
  const current = await state();
  const revoked = Object.entries(current.revoked)
    .sort((a, b) => b[1].at - a[1].at)
    .slice(0, 50);
  current.revoked = Object.fromEntries(revoked);
  await chrome.storage.local.set({ claims: current.claims, revoked: current.revoked });
}

function token() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function mark(tabId, claim) {
  try {
    await chrome.action.setBadgeText({ tabId, text: claim ? 'CA' : null });
    if (claim) {
      await chrome.action.setBadgeBackgroundColor({ tabId, color: BADGE });
      await chrome.action.setTitle({
        tabId,
        title: `Computer Artist: ${claim.owner} controls this tab. Clicking or typing in it takes it back.`,
      });
    } else {
      await chrome.action.setTitle({ tabId, title: 'Computer Artist' });
    }
  } catch (error) {
    // The tab closed meanwhile.
  }
}

async function endClaim(tabId, reason) {
  const current = await state();
  const claim = current.claims[tabId];
  if (!claim) return null;
  delete current.claims[tabId];
  if (reason !== 'released') {
    const title = reason === 'target_closed' ? null : (await chrome.tabs.get(tabId).catch(() => null))?.title;
    current.revoked[tabId] = {
      reason,
      owner: claim.owner,
      lease: claim.lease,
      at: Date.now(),
      title: title || claim.title || '',
    };
  }
  await persist();
  if (reason !== 'target_closed') {
    liftPen(tabId, reason);
    await mark(tabId, null);
    if (claim.opened) {
      try {
        await chrome.tabs.ungroup(tabId);
      } catch (error) {
        // Not grouped any more, or tab groups are unavailable.
      }
    }
  }
  return claim;
}

async function requireLease(tabId, lease) {
  const current = await state();
  const claim = current.claims[tabId];
  if (!claim) {
    const revoked = current.revoked[tabId];
    if (revoked && revoked.lease === lease) {
      throw new Refusal(
        'ownership_revoked',
        `The lease on tab ${tabId} ended: ${revoked.reason}. Inspect the tab and claim it again only if the user agrees.`,
        { stop_reason: revoked.reason },
      );
    }
    throw new Refusal('not_claimed', `Tab ${tabId} is not claimed; use ca browser claim or open`);
  }
  if (!lease || claim.lease !== lease) {
    throw new Refusal('lease_mismatch', `Tab ${tabId} is claimed by ${claim.owner} with another lease`, {
      owner: claim.owner,
    });
  }
  return claim;
}

function liftPen(tabId, reason) {
  chrome.scripting
    .executeScript({
      target: { tabId },
      world: 'ISOLATED',
      func: (why) => globalThis.__caPen?.leave(why),
      args: [reason],
    })
    .catch(() => {}); // Replaced, closing or unscriptable: there is no pen to lift.
}

async function noteAction(tabId, lease, op) {
  // The popup shows each agent's last action; only the claim that acted is updated.
  const current = await state();
  const claim = current.claims[tabId];
  if (!claim || claim.lease !== lease) return;
  claim.last = { op, at: Date.now() };
  await persist();
}

async function watchTab(tabId) {
  // watch.js runs in new documents by itself; this covers documents that were
  // open before the extension loaded. It is idempotent per document.
  await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    world: 'ISOLATED',
    files: ['watch.js'],
  });
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === 'ca-human-input' && sender.id === chrome.runtime.id && sender.tab) {
    if (opening.has(sender.tab.id)) inputWhileOpening.add(sender.tab.id);
    state().then((current) => {
      if (current.claims[sender.tab.id]) endClaim(sender.tab.id, 'human_input');
    });
  }
});

chrome.tabs.onUpdated.addListener((tabId, change) => {
  // Track each claimed tab's URL so a reload can tell the same tab from a reused ID.
  if (!change.url) return;
  state().then((current) => {
    const claim = current.claims[tabId];
    if (claim && claim.url !== change.url) {
      claim.url = change.url;
      persist();
    }
  });
});

chrome.windows.onRemoved.addListener((windowId) => {
  chrome.storage.local.get('agentWindow').then(({ agentWindow }) => {
    if (agentWindow === windowId) chrome.storage.local.remove('agentWindow');
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  state().then((current) => {
    if (current.claims[tabId]) endClaim(tabId, 'target_closed');
  });
});

chrome.tabs.onReplaced.addListener((added, removed) => {
  state().then((current) => {
    if (current.claims[removed]) endClaim(removed, 'target_replaced');
  });
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  // Chrome activation (including grouping/window changes) has no input attribution.
  // Watching is allowed; trusted page input alone ends the relevant claim.
  if (opening.has(tabId)) activatedWhileOpening.add(tabId);
});

// ----------------------------------------------------------------- helpers

async function getTab(tabId) {
  if (!Number.isInteger(tabId)) throw new Refusal('invalid_tab', 'A numeric tab ID is required');
  try {
    return await chrome.tabs.get(tabId);
  } catch (error) {
    throw new Refusal('target_unavailable', `Tab ${tabId} does not exist in this browser`);
  }
}

// Chrome lets no extension script its own pages, other extensions or the Web Store;
// file URLs need "Allow access to file URLs" on the extension card.
async function restriction(url) {
  url ||= 'about:blank';
  let parsed;
  try {
    parsed = new URL(url);
  } catch (error) {
    return 'unsupported_url';
  }
  if (parsed.protocol === 'file:') {
    return (await chrome.extension.isAllowedFileSchemeAccess()) ? null : 'file_access_off';
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    if (url === 'about:blank') return null;
    const browser = ['chrome:', 'chrome-extension:', 'chrome-untrusted:', 'chrome-search:', 'devtools:',
      'about:', 'view-source:', 'edge:', 'brave:', 'vivaldi:', 'opera:'];
    return browser.includes(parsed.protocol) ? 'browser_page' : 'unsupported_url';
  }
  if (parsed.hostname === 'chromewebstore.google.com' ||
      (parsed.hostname === 'chrome.google.com' && parsed.pathname.startsWith('/webstore'))) {
    return 'web_store';
  }
  return null;
}

const RESTRICTIONS = {
  browser_page: 'Chrome does not let extensions script browser pages (chrome://, the new tab page, other extensions)',
  web_store: 'Chrome does not let extensions script the Chrome Web Store',
  file_access_off: 'File URLs need "Allow access to file URLs" on the Computer Artist extension card',
  unsupported_url: 'Chrome does not let extensions script data:, blob: or other non-web URLs',
};

function checkUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch (error) {
    throw new Refusal('invalid_url', `Invalid URL: ${url}`);
  }
  if (!ALLOWED_SCHEMES.includes(parsed.protocol) && url !== 'about:blank') {
    throw new Refusal('unsupported_url', `ca opens only http, https, file and about:blank URLs`);
  }
  return parsed.href;
}

async function describeTab(tab) {
  const current = await state();
  const claim = current.claims[tab.id];
  const revoked = current.revoked[tab.id];
  const restricted = await restriction(tab.url || tab.pendingUrl);
  return {
    tab: tab.id,
    window: tab.windowId,
    index: tab.index,
    active: tab.active,
    title: tab.title,
    url: tab.url ?? tab.pendingUrl,
    status: tab.status,
    group: tab.groupId >= 0 ? tab.groupId : null,
    claim: claim ? { owner: claim.owner, since: claim.since, opened: claim.opened } : null,
    scriptable: !restricted,
    ...(restricted ? { restriction: restricted } : {}),
    ...(revoked && !claim ? { last_stop_reason: revoked.reason } : {}),
  };
}

function waitForLoad(tabId, timeoutMs, start) {
  // start() may create the tab; its result then names the tab to follow.
  return new Promise((resolve, reject) => {
    let seenLoading = false;
    let done = false;
    const finish = async (loaded) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearInterval(poll);
      clearTimeout(timer);
      resolve(loaded);
    };
    const listener = (id, change) => {
      if (id !== tabId) return;
      if (change.status === 'loading') seenLoading = true;
      if (change.status === 'complete' && seenLoading) finish(true);
    };
    chrome.tabs.onUpdated.addListener(listener);
    const began = Date.now();
    const poll = setInterval(async () => {
      try {
        if (tabId == null) return;
        const tab = await chrome.tabs.get(tabId);
        if (tab.status === 'loading') seenLoading = true;
        if (tab.status === 'complete' && (seenLoading || Date.now() - began > 1500)) finish(true);
      } catch (error) {
        finish(false);
      }
    }, 250);
    const timer = setTimeout(() => finish(false), timeoutMs);
    start().then(
      (tab) => {
        if (tabId == null) tabId = tab?.id;
      },
      (error) => {
        finish(false);
        if (error instanceof Refusal) reject(error);
      },
    );
  });
}

async function inPage(tabId, request, claim = null) {
  let results;
  if (claim) {
    // The pen is cosmetic: a page that refuses it still gets the action.
    await chrome.scripting
      .executeScript({ target: { tabId }, world: 'ISOLATED', files: ['pen.js'] })
      .catch(() => {});
    request = { ...request, pen: { owner: claim.owner } };
  }
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'ISOLATED',
      func: pageAction,
      args: [request],
    });
  } catch (error) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    const restricted = await restriction(tab?.url);
    throw new Refusal(
      'page_unavailable',
      restricted
        ? `${RESTRICTIONS[restricted]} (tab ${tabId}).`
        : `Cannot script tab ${tabId}: ${error.message}`,
      restricted ? { restriction: restricted } : {},
    );
  }
  const result = results?.[0]?.result;
  if (!result) throw new Refusal('page_unavailable', 'The page returned no result');
  if (!result.ok) throw new Refusal(result.code, result.detail, result.extra || {});
  delete result.ok;
  return result;
}

function timeoutMs(request, fallback) {
  const seconds = Number(request.timeout ?? fallback);
  return Math.max(100, Math.min(Number.isFinite(seconds) ? seconds : fallback, 110) * 1000);
}

// The agents' own window: created unfocused on first use and reused, so agent tabs
// are visible (rendered at full speed, capturable) without touching the user's windows.
let agentWindowTurn = Promise.resolve();
function openInAgentWindow(url) {
  // One at a time, so simultaneous first opens share one new window.
  const run = async () => {
    const { agentWindow } = await chrome.storage.local.get('agentWindow');
    const existing = agentWindow != null ? await chrome.windows.get(agentWindow).catch(() => null) : null;
    if (existing) return chrome.tabs.create({ windowId: existing.id, url, active: true });
    const window = await chrome.windows.create({ url, focused: false, type: 'normal' });
    await chrome.storage.local.set({ agentWindow: window.id });
    return window.tabs[0];
  };
  const next = agentWindowTurn.then(run, run);
  agentWindowTurn = next.catch(() => {});
  return next;
}

// Screenshots leave the pen out. Overlapping captures of one tab share the hiding,
// and the pen returns only when the last of them is done.
const concealed = new Map();
function setPen(tabId, hidden) {
  return chrome.scripting
    .executeScript({ target: { tabId }, world: 'ISOLATED', func: (h) => globalThis.__caPen?.conceal(h), args: [hidden] })
    .catch(() => null);
}
async function concealPen(tabId) {
  const count = (concealed.get(tabId) || 0) + 1;
  concealed.set(tabId, count);
  await setPen(tabId, true); // also waits until a shown tab has painted without it
}
async function revealPen(tabId) {
  const count = (concealed.get(tabId) || 1) - 1;
  if (count > 0) return concealed.set(tabId, count);
  concealed.delete(tabId);
  await setPen(tabId, false);
}

// Background tabs are captured through the debugger protocol, one capture at a time
// per tab, attached only for the capture. Visible tabs never need it.
const capturing = new Map();
function debuggerCapture(tabId) {
  const run = async () => {
    const target = { tabId };
    try {
      await chrome.debugger.attach(target, '1.3');
    } catch (error) {
      throw new Refusal(
        'capture_unavailable',
        `Chrome refused to capture background tab ${tabId}: ${error.message}. Use focus to show it, or read and locate.`,
      );
    }
    try {
      const shot = await Promise.race([
        chrome.debugger.sendCommand(target, 'Page.captureScreenshot', { format: 'png' }),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timed out')), 15000)),
      ]);
      return `data:image/png;base64,${shot.data}`;
    } catch (error) {
      throw new Refusal('capture_unavailable', `Capturing background tab ${tabId} failed: ${error.message}`);
    } finally {
      await chrome.debugger.detach(target).catch(() => {});
    }
  };
  const next = (capturing.get(tabId) || Promise.resolve()).then(run, run);
  capturing.set(tabId, next.catch(() => {}));
  return next;
}

async function groupOpenedTab(tab) {
  try {
    const [group] = await chrome.tabGroups.query({ windowId: tab.windowId, title: GROUP_TITLE });
    const groupId = await chrome.tabs.group(
      group ? { tabIds: [tab.id], groupId: group.id } : { tabIds: [tab.id], createProperties: { windowId: tab.windowId } },
    );
    if (!group) await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: GROUP_COLOR });
  } catch (error) {
    // Tab groups are a visible marker only; some Chromium builds lack them.
  }
}

async function claimTab(tab, owner, opened) {
  const current = await state();
  const existing = current.claims[tab.id];
  if (existing) {
    throw new Refusal('tab_claimed', `Tab ${tab.id} is already claimed by ${existing.owner}`, {
      owner: existing.owner,
    });
  }
  const restricted = await restriction(tab.url || tab.pendingUrl);
  if (restricted) {
    throw new Refusal('page_unavailable', `${RESTRICTIONS[restricted]}; ca cannot claim tab ${tab.id}.`, {
      restriction: restricted,
    });
  }
  const claim = {
    lease: token(), owner, since: Date.now(), opened, title: tab.title || '', url: tab.url || tab.pendingUrl,
  }; // prettier-ignore
  current.claims[tab.id] = claim;
  delete current.revoked[tab.id];
  await persist();
  try {
    await watchTab(tab.id);
  } catch (error) {
    delete current.claims[tab.id];
    await persist();
    throw new Refusal(
      'page_unavailable',
      `Cannot watch tab ${tab.id} for human input: ${error.message}. ca claims only scriptable pages.`,
    );
  }
  await mark(tab.id, claim);
  return claim;
}

function ownerName(request) {
  const owner = String(request.owner || 'ca').slice(0, 64);
  if (!/^[\w .:@-]+$/.test(owner)) throw new Refusal('invalid_owner', 'Owner names use letters, digits and . : @ - _');
  return owner;
}

// -------------------------------------------------------------- operations

async function handle(request) {
  const tabId = request.tab;
  switch (request.op) {
    case 'info': {
      const id = await identity();
      const windows = await chrome.windows.getAll();
      const tabs = await chrome.tabs.query({});
      const current = await state();
      return {
        info: {
          instance: id.instance,
          label: id.label,
          browser: browserName(),
          extension_version: VERSION,
          build: BUILD,
          capabilities: CAPABILITIES,
          agent_window: (await chrome.storage.local.get('agentWindow')).agentWindow ?? null,
          windows: windows.map((w) => ({ window: w.id, focused: w.focused, state: w.state, type: w.type, incognito: w.incognito })),
          tabs: tabs.length,
          claims: Object.keys(current.claims).length,
        },
      };
    }
    case 'label': {
      const label = String(request.label || '').trim().slice(0, 64);
      await chrome.storage.local.set({ label });
      const id = await identity();
      id.label = label;
      if (port) port.postMessage(await hello());
      return { instance: id.instance, label };
    }
    case 'reload': {
      // Load updated files after ca browser install, like the card's reload button.
      // Claims are kept in local storage and survive it; requests in progress would not.
      if (inFlight > 1) {
        throw new Refusal('busy', `${inFlight - 1} other request(s) are in progress; try again when they finish`);
      }
      reloading = true;
      setTimeout(() => chrome.runtime.reload(), 250);
      return { reloading: true, previous_version: VERSION, previous_build: BUILD };
    }
    case 'tabs': {
      const tabs = await chrome.tabs.query({});
      return { tabs: await Promise.all(tabs.map(describeTab)) };
    }
    case 'open': {
      const url = checkUrl(request.url);
      const owner = ownerName(request);
      const properties = { url, active: false };
      if (request.window != null) properties.windowId = Number(request.window);
      let tab;
      const loaded = await waitForLoad(null, timeoutMs(request, 30), async () => {
        try {
          tab = request.agent_window ? await openInAgentWindow(url) : await chrome.tabs.create(properties);
          opening.add(tab.id);
        } catch (error) {
          throw new Refusal('window_unavailable', `Cannot open a background tab: ${error.message}`);
        }
        return tab;
      });
      if (!tab) throw new Refusal('window_unavailable', 'Chrome did not open the tab');
      let claim;
      let now;
      try {
        now = await getTab(tab.id); // after redirects, so the claim records the URL it shows
        claim = await claimTab(now, owner, true);
      } catch (error) {
        // Close the failed tab unless the human already switched to it.
        if (!activatedWhileOpening.delete(tab.id) && !inputWhileOpening.has(tab.id)) {
          await chrome.tabs.remove(tab.id).catch(() => {});
        }
        inputWhileOpening.delete(tab.id);
        throw error;
      } finally {
        opening.delete(tab.id);
      }
      activatedWhileOpening.delete(tab.id);
      if (inputWhileOpening.delete(tab.id)) {
        await endClaim(tab.id, 'human_input');
        throw new Refusal('ownership_revoked', 'Your input took the tab while it opened', {
          stop_reason: 'human_input', tab: tab.id,
        });
      }
      await groupOpenedTab(tab);
      return { lease: claim.lease, loaded, ...(await describeTab(now)) };
    }
    case 'claim': {
      const tab = await getTab(tabId);
      const claim = await claimTab(tab, ownerName(request), false);
      return { lease: claim.lease, ...(await describeTab(tab)) };
    }
    case 'release': {
      await requireLease(tabId, request.lease);
      await endClaim(tabId, 'released');
      return { released: true, tab: tabId };
    }
    case 'status': {
      const current = await state();
      const claim = current.claims[tabId];
      const revoked = current.revoked[tabId];
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      return {
        tab: tabId,
        exists: Boolean(tab),
        claimed: Boolean(claim),
        owner: claim?.owner ?? null,
        holds_lease: Boolean(claim && request.lease && claim.lease === request.lease),
        stop_reason: !claim && revoked ? revoked.reason : null,
        ...(tab ? { active: tab.active, url: tab.url, title: tab.title } : {}),
      };
    }
    case 'close': {
      const claim = await requireLease(tabId, request.lease);
      if (!claim.opened) {
        throw new Refusal('not_agent_tab', 'ca closes only tabs it opened; release this one instead');
      }
      await endClaim(tabId, 'released');
      await chrome.tabs.remove(tabId);
      return { closed: true, tab: tabId };
    }
    case 'navigate': {
      await requireLease(tabId, request.lease);
      const tab = await getTab(tabId);
      const start = request.reload
        ? () => chrome.tabs.reload(tabId)
        : () => chrome.tabs.update(tabId, { url: checkUrl(request.url) });
      if (!request.reload) checkUrl(request.url);
      const loaded = await waitForLoad(tab.id, timeoutMs(request, 30), start);
      await requireLease(tabId, request.lease);
      await noteAction(tabId, request.lease, request.reload ? 'reload' : 'navigate');
      return { loaded, ...(await describeTab(await getTab(tabId))) };
    }
    case 'read':
    case 'locate': {
      await getTab(tabId);
      return inPage(tabId, { ...request, max_chars: Math.min(Number(request.max_chars) || 20000, MAX_READ) });
    }
    case 'wait': {
      await getTab(tabId);
      const deadline = Date.now() + timeoutMs(request, 10);
      for (;;) {
        try {
          const found = await inPage(tabId, { ...request, op: 'locate', limit: 1 });
          if (found.count > 0) return found;
        } catch (error) {
          if (!(error instanceof Refusal) || !['page_unavailable', 'element_not_found'].includes(error.code)) throw error;
        }
        if (Date.now() > deadline) throw new Refusal('timeout', 'The target did not appear in time');
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
    case 'click':
    case 'move':
    case 'drag':
    case 'scroll':
    case 'type':
    case 'press':
    case 'select': {
      const claim = await requireLease(tabId, request.lease);
      await getTab(tabId);
      if (request.op === 'type' && String(request.input ?? '').length > MAX_TEXT) {
        throw new Refusal('payload_too_large', `Type at most ${MAX_TEXT} characters at once`);
      }
      const result = await inPage(tabId, request, claim);
      await noteAction(tabId, request.lease, request.op);
      return result;
    }
    case 'focus': {
      // The one deliberate exception to "never switch tabs or focus windows":
      // only on explicit request, to show the user a tab or to test visibly.
      await requireLease(tabId, request.lease);
      const tab = await getTab(tabId);
      await chrome.tabs.update(tabId, { active: true });
      const window = await chrome.windows.get(tab.windowId);
      await chrome.windows.update(tab.windowId, {
        focused: true,
        ...(window.state === 'minimized' ? { state: 'normal' } : {}),
      });
      await noteAction(tabId, request.lease, 'focus');
      return { focused: true, ...(await describeTab(await getTab(tabId))) };
    }
    case 'screenshot': {
      const tab = await getTab(tabId);
      const window = await chrome.windows.get(tab.windowId);
      const restricted = await restriction(tab.url || tab.pendingUrl);
      if (restricted) {
        throw new Refusal('page_unavailable', `${RESTRICTIONS[restricted]} (tab ${tabId}).`, { restriction: restricted });
      }
      // Chrome paints a page only while it is shown: a covered window, or one behind
      // another application on macOS, is hidden and captureVisibleTab then returns its
      // last painted frame. So the page's own visibility decides; any page that is not
      // being painted is rendered through the debugger protocol instead.
      const painted = async () =>
        tab.active && window.state !== 'minimized' &&
        (await chrome.scripting
          .executeScript({ target: { tabId }, world: 'ISOLATED', func: () => document.visibilityState })
          .then((results) => results?.[0]?.result === 'visible', () => false));
      await concealPen(tabId);
      let data;
      let method = 'debugger';
      try {
        if (await painted()) {
          try {
            data = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
            method = 'visible_tab';
          } catch (error) {
            // Chrome allows about two visible captures a second; render the rest instead.
            if (!/MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND/.test(String(error?.message))) throw error;
          }
          // Hidden or switched away while capturing: that frame may be stale.
          if (method === 'visible_tab' && !((await getTab(tabId)).active && (await painted()))) method = 'debugger';
        }
        if (method === 'debugger') data = await debuggerCapture(tabId);
      } finally {
        await revealPen(tabId);
      }
      // The viewport in CSS pixels maps screenshot pixels to pointer coordinates.
      const viewport = await chrome.scripting
        .executeScript({
          target: { tabId },
          world: 'ISOLATED',
          func: () => ({ width: innerWidth, height: innerHeight, scroll_x: scrollX, scroll_y: scrollY }),
        })
        .then((results) => results?.[0]?.result ?? null, () => null);
      return { tab: tabId, format: 'png', data_url: data, viewport, method };
    }
    default:
      throw new Refusal('unknown_operation', `Unknown operation ${request.op}`);
  }
}

// ---------------------------------------------------------- page functions

// Runs inside the page's isolated world. It must be self-contained: Chrome
// serializes this function's source into the page. pen.js and watch.js share
// this world, which is how it reaches the pen and the last human input time.
async function pageAction(request) {
  const ca = (globalThis.__ca ??= {
    doc: Math.random().toString(36).slice(2, 8),
    next: 1,
    refs: new Map(),
    ids: new WeakMap(),
  });
  const fail = (code, detail, extra) => ({ ok: false, code, detail, extra });
  const clip = (value, size = 160) => {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > size ? `${text.slice(0, size)}…` : text;
  };
  const roles = {
    A: (el) => (el.hasAttribute('href') ? 'link' : ''),
    BUTTON: () => 'button',
    SELECT: () => 'combobox',
    TEXTAREA: () => 'textbox',
    SUMMARY: () => 'button',
    IMG: () => 'img',
    H1: () => 'heading', H2: () => 'heading', H3: () => 'heading',
    H4: () => 'heading', H5: () => 'heading', H6: () => 'heading',
    INPUT: (el) => {
      const type = (el.type || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'hidden') return '';
      return 'textbox';
    },
  };
  const roleOf = (el) => el.getAttribute('role') || roles[el.tagName]?.(el) || (el.isContentEditable ? 'textbox' : '');
  const viewOf = (node) => (node.ownerDocument || node).defaultView || window;
  const nameOf = (el) => {
    const labelled = el.getAttribute('aria-labelledby');
    const fromIds = labelled
      ? labelled.split(/\s+/).map((id) => el.ownerDocument.getElementById(id)?.innerText || '').join(' ')
      : '';
    const labels = el.labels ? Array.from(el.labels, (l) => l.innerText).join(' ') : '';
    return clip(
      el.getAttribute('aria-label') || fromIds || labels || el.getAttribute('alt') ||
        el.getAttribute('title') ||
        (['submit', 'button', 'reset'].includes(el.type) ? el.value : '') ||
        el.innerText || el.getAttribute('placeholder') || '',
      120,
    );
  };
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = viewOf(el).getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const refOf = (el) => {
    let ref = ca.ids.get(el);
    if (!ref) {
      ref = `${ca.doc}-${ca.next++}`;
      ca.ids.set(el, ref);
      ca.refs.set(ref, new WeakRef(el));
    }
    return ref;
  };

  // Frames from the page's own origin are reachable from this world; others are not.
  // Coordinates are CSS pixels of the top viewport unless a name says local.
  const isFrame = (el) => el?.tagName === 'IFRAME' || el?.tagName === 'FRAME';
  const inner = (frame) => {
    try {
      return frame.contentDocument || null;
    } catch (error) {
      return null;
    }
  };
  const parentFrame = (doc) => {
    try {
      return doc.defaultView?.frameElement || null;
    } catch (error) {
      return null;
    }
  };
  const originOf = (frame) => {
    try {
      return new URL(frame.src, frame.baseURI).origin;
    } catch (error) {
      return 'another origin';
    }
  };
  const frameBox = (frame) => {
    const rect = frame.getBoundingClientRect();
    const style = viewOf(frame).getComputedStyle(frame);
    const sx = frame.offsetWidth ? rect.width / frame.offsetWidth : 1;
    const sy = frame.offsetHeight ? rect.height / frame.offsetHeight : 1;
    return {
      x: rect.left + (frame.clientLeft + parseFloat(style.paddingLeft)) * sx,
      y: rect.top + (frame.clientTop + parseFloat(style.paddingTop)) * sy,
      sx,
      sy,
    };
  };
  const framesOf = (doc) => {
    const frames = [];
    for (let frame = parentFrame(doc); frame; frame = parentFrame(frame.ownerDocument)) frames.unshift(frame);
    return frames;
  };
  const toTop = (doc, x, y) => {
    for (const frame of framesOf(doc).reverse()) {
      const box = frameBox(frame);
      x = box.x + x * box.sx;
      y = box.y + y * box.sy;
    }
    return { x, y };
  };
  const fromTop = (doc, x, y) => {
    for (const frame of framesOf(doc)) {
      const box = frameBox(frame);
      x = (x - box.x) / box.sx;
      y = (y - box.y) / box.sy;
    }
    return { x, y };
  };
  const topRect = (el) => {
    const rect = el.getBoundingClientRect();
    const a = toTop(el.ownerDocument, rect.left, rect.top);
    const b = toTop(el.ownerDocument, rect.right, rect.bottom);
    return { x: a.x, y: a.y, width: b.x - a.x, height: b.y - a.y };
  };
  const round = (value) => Math.round(value * 10) / 10;
  const describe = (el) => {
    const rect = topRect(el);
    const info = {
      ref: refOf(el),
      tag: el.tagName.toLowerCase(),
      role: roleOf(el) || null,
      name: nameOf(el),
      visible: isVisible(el),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
    };
    if (el.ownerDocument !== document) info.in_frame = true;
    if ('value' in el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
      info.value = el.type === 'password' ? '[hidden]' : clip(el.value, 200);
    }
    if (el.tagName === 'SELECT') {
      info.options = Array.from(el.options, (option) => ({
        value: option.value, label: option.label, selected: option.selected,
        disabled: option.disabled || Boolean(option.closest('optgroup')?.disabled),
      }));
      info.multiple = el.multiple;
    }
    if (el.type === 'checkbox' || el.type === 'radio') info.checked = el.checked;
    return info;
  };
  const interactive =
    'a[href],button,input,textarea,select,summary,label,[role],[tabindex],[contenteditable=""],[contenteditable=true],[onclick]';
  const find = (target, doc) => {
    let nodes;
    if (target.selector) {
      try {
        nodes = Array.from(doc.querySelectorAll(target.selector));
      } catch (error) {
        return fail('invalid_selector', `Invalid CSS selector: ${target.selector}`);
      }
    } else if (target.text || target.role) {
      const wanted = String(target.text || target.name || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const match = (selector, label) => {
        const candidates = Array.from(doc.querySelectorAll(selector))
          .slice(0, 5000)
          .filter((el) => isVisible(el) && (!target.role || roleOf(el) === target.role));
        const scored = candidates
          .map((el) => {
            const name = label(el).toLowerCase();
            return { el, exact: name === wanted, partial: wanted ? name.includes(wanted) : true };
          })
          .filter((m) => m.partial);
        const exact = scored.filter((m) => m.exact);
        const found = (exact.length ? exact : scored).map((m) => m.el);
        // Prefer the innermost match when a container repeats its child's text.
        return found.filter((el) => !found.some((other) => other !== el && el.contains(other)));
      };
      nodes = match(target.role ? '*' : interactive, nameOf);
      if (!nodes.length && !target.role) {
        // Fall back to any visible text, such as list items and paragraphs.
        nodes = match('body *', (el) => clip(el.innerText, 400));
      }
      if (!nodes.length && !target.role) {
        // Then to tooltips and labels, which name icon-only toolbar buttons.
        nodes = match('body [title], body [aria-label]', nameOf);
      }
    } else {
      return fail('invalid_target', 'Pass a ref, selector, text or role');
    }
    return nodes;
  };
  // --frame selectors, outermost first, choose the document that targets are found in.
  const scopeOf = (frames) => {
    let doc = document;
    for (const selector of frames || []) {
      let matches;
      try {
        matches = Array.from(doc.querySelectorAll(selector)).filter(isFrame);
      } catch (error) {
        return fail('invalid_selector', `Invalid CSS selector: ${selector}`);
      }
      if (!matches.length) return fail('frame_not_found', `No iframe matches ${selector}`);
      if (matches.length > 1) {
        return fail('ambiguous_target', `${matches.length} iframes match ${selector}`, {
          candidates: matches.slice(0, 5).map(describe),
        });
      }
      const next = inner(matches[0]);
      if (!next) {
        return fail('cross_origin_frame', `The iframe ${selector} shows ${originOf(matches[0])}; ca acts only in frames from the page's own origin`);
      }
      doc = next;
    }
    return doc;
  };
  const frameSelector = (frame) => {
    if (frame.id) return `#${CSS.escape(frame.id)}`;
    const quote = (value) => JSON.stringify(value);
    if (frame.name) return `${frame.tagName.toLowerCase()}[name=${quote(frame.name)}]`;
    return `${frame.tagName.toLowerCase()}[src=${quote(frame.getAttribute('src') || '')}]`;
  };
  // Where a target is missing from the page itself, say which same-origin frames hold it.
  const frameHint = (target) => {
    const found = [];
    const visit = (doc, path) => {
      for (const frame of doc.querySelectorAll('iframe,frame')) {
        const sub = inner(frame);
        if (!sub || found.length >= 5) continue;
        const route = [...path, frameSelector(frame)];
        const nodes = find(target, sub);
        if (Array.isArray(nodes) && nodes.length) found.push({ frame: route, matches: nodes.length });
        if (route.length < 4) visit(sub, route);
      }
    };
    visit(document, []);
    return found;
  };
  const resolve = (target, doc) => {
    if (target.ref) {
      const ref = String(target.ref);
      const el = ref.split('-')[0] === ca.doc ? ca.refs.get(ref)?.deref() : null;
      if (!el || !el.isConnected) {
        return fail('stale_element', `Element ${ref} is from an earlier page or was removed; locate it again`);
      }
      return el;
    }
    const nodes = find(target, doc);
    if (!Array.isArray(nodes)) return nodes;
    if (!nodes.length) {
      const frames = target.frame?.length ? [] : frameHint(target);
      if (frames.length) {
        const flags = frames[0].frame.map((selector) => `--frame '${selector}'`).join(' ');
        return fail('element_not_found', `No element in the page matches, but ${frames.length} frame(s) do; pass ${flags}`, { frames });
      }
      return fail('element_not_found', 'No element matches the target');
    }
    if (nodes.length > 1 && !target.first) {
      return fail('ambiguous_target', `${nodes.length} elements match; pass a ref or --first`, {
        candidates: nodes.slice(0, 5).map(describe),
      });
    }
    return nodes[0];
  };
  const deepActive = () => {
    let el = document.activeElement;
    while (isFrame(el) && inner(el)?.activeElement) el = inner(el).activeElement;
    return el;
  };
  const editable = (el) =>
    el.isContentEditable ||
    el.tagName === 'TEXTAREA' ||
    (el.tagName === 'INPUT' && !['button', 'submit', 'reset', 'checkbox', 'radio', 'range', 'file', 'image', 'hidden', 'color'].includes(el.type));
  const setValue = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  };
  const insert = (el, text) => {
    if (el.isContentEditable) {
      if (!el.ownerDocument.execCommand('insertText', false, text)) {
        const selection = viewOf(el).getSelection();
        const range = selection.rangeCount ? selection.getRangeAt(0) : el.ownerDocument.createRange();
        range.deleteContents();
        range.insertNode(el.ownerDocument.createTextNode(text));
        range.collapse(false);
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
      }
      return;
    }
    try {
      el.setRangeText(text, el.selectionStart, el.selectionEnd, 'end');
    } catch (error) {
      setValue(el, el.value + text); // email/number inputs have no selection API
    }
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
  };
  // Glide the pen to the target before acting, like the native pen. Human input
  // or a claim that ends while it moves refuses the action instead of performing it.
  const started = performance.now();
  const pen = request.pen ? globalThis.__caPen : null;
  const interrupted = () => {
    if ((globalThis.__caHumanAt ?? -Infinity) > started) {
      return fail('ownership_revoked', 'Your input took the tab back before ca acted', {
        stop_reason: 'human_input',
      });
    }
    const ended = globalThis.__caClaimEnded;
    if (ended && ended.at > started) {
      if (ended.reason === 'released') return fail('not_claimed', 'The claim was released before ca acted');
      return fail('ownership_revoked', `The claim ended before ca acted: ${ended.reason}`, {
        stop_reason: ended.reason,
      });
    }
    return null;
  };
  const arriveAt = async (x, y) => {
    if (!pen) return null;
    await pen.to(x, y, request.pen);
    return interrupted();
  };
  const arrive = async (el, spot) => {
    if (!pen || el === el.ownerDocument.body || el === el.ownerDocument.documentElement) return null;
    const { x, y } = spot(topRect(el));
    return arriveAt(x, y);
  };
  const centre = (rect) => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
  const key = (el, type, name, modifiers) =>
    el.dispatchEvent(
      new KeyboardEvent(type, {
        key: name,
        code: name.length === 1 ? `Key${name.toUpperCase()}` : name,
        bubbles: true,
        cancelable: true,
        composed: true,
        ...modifiers,
      }),
    );

  // ------------------------------------------------------------- pointer
  // A mouse as pages see it: hover transitions, move before press, pointer then
  // compatibility mouse events, focus on press and click on the common ancestor.
  const MASK = { left: 1, right: 2, middle: 4 };
  const CODE = { left: 0, middle: 1, right: 2 };
  const FOCUSABLE =
    'a[href],area[href],button,input,select,textarea,summary,iframe,[tabindex],[contenteditable]:not([contenteditable="false"])';
  const modifiers = request.modifiers || [];
  const held = {
    ctrlKey: modifiers.includes('Control'),
    shiftKey: modifiers.includes('Shift'),
    altKey: modifiers.includes('Alt'),
    metaKey: modifiers.includes('Meta'),
  };
  const mouse = (ca.mouse ??= { buttons: 0, suppress: false, down: null, hover: null });
  const hitTest = (x, y) => {
    let doc = document;
    let lx = x;
    let ly = y;
    for (let depth = 0; depth < 10; depth++) {
      let el = doc.elementFromPoint(lx, ly);
      while (el?.shadowRoot) {
        const deeper = el.shadowRoot.elementFromPoint(lx, ly);
        if (!deeper || deeper === el) break;
        el = deeper;
      }
      if (!el) return null;
      if (!isFrame(el)) return { el, x, y };
      const sub = inner(el);
      if (!sub) return { el, x, y, foreign: originOf(el) };
      const box = frameBox(el);
      lx = (lx - box.x) / box.sx;
      ly = (ly - box.y) / box.sy;
      doc = sub;
    }
    return null;
  };
  const up = (node) =>
    node.parentElement || node.parentNode?.host || (node.parentNode?.nodeType === 9 ? parentFrame(node.parentNode) : null);
  const chainOf = (el) => {
    const chain = [];
    for (let node = el; node; node = up(node)) chain.push(node);
    return chain;
  };
  const fire = (el, type, at, extra = {}) => {
    const view = viewOf(el);
    const local = el.ownerDocument === document ? at : fromTop(el.ownerDocument, at.x, at.y);
    const init = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view,
      clientX: local.x,
      clientY: local.y,
      screenX: window.screenX + at.x,
      screenY: window.screenY + (window.outerHeight - window.innerHeight) + at.y,
      button: 0,
      buttons: mouse.buttons,
      ...held,
      ...extra,
    };
    let event;
    if (type.startsWith('pointer')) {
      event = new PointerEvent(type, {
        pointerId: 1, pointerType: 'mouse', isPrimary: true, width: 1, height: 1,
        pressure: mouse.buttons ? 0.5 : 0, ...init,
      }); // prettier-ignore
    } else if (type === 'wheel') {
      event = new WheelEvent(type, init);
    } else {
      event = new MouseEvent(type, init);
    }
    return el.dispatchEvent(event);
  };
  const hoverTo = (at) => {
    const previous = mouse.hover?.deref();
    const next = at.el;
    if (previous === next) return;
    const was = previous?.isConnected ? chainOf(previous) : [];
    const now = chainOf(next);
    const left = was.filter((node) => !now.includes(node));
    const entered = now.filter((node) => !was.includes(node)).reverse();
    const related = (a, b) => (b && a.ownerDocument === b.ownerDocument ? b : null);
    const quiet = { bubbles: false, cancelable: false };
    if (was.length) {
      fire(previous, 'pointerout', at, { relatedTarget: related(previous, next), button: -1 });
      for (const node of left) fire(node, 'pointerleave', at, { ...quiet, relatedTarget: related(node, next), button: -1 });
      fire(previous, 'mouseout', at, { relatedTarget: related(previous, next) });
      for (const node of left) fire(node, 'mouseleave', at, { ...quiet, relatedTarget: related(node, next) });
    }
    const from = was.length ? previous : null;
    fire(next, 'pointerover', at, { relatedTarget: from && related(next, from), button: -1 });
    for (const node of entered) fire(node, 'pointerenter', at, { ...quiet, relatedTarget: from && related(node, from), button: -1 });
    fire(next, 'mouseover', at, { relatedTarget: from && related(next, from) });
    for (const node of entered) fire(node, 'mouseenter', at, { ...quiet, relatedTarget: from && related(node, from) });
    mouse.hover = new WeakRef(next);
  };
  const moveTo = (at) => {
    hoverTo(at);
    fire(at.el, 'pointermove', at, { button: -1 });
    if (!mouse.suppress) fire(at.el, 'mousemove', at);
  };
  const focusAt = (el) => {
    const doc = el.ownerDocument;
    const target = chainOf(el).find((node) => node.ownerDocument === doc && node.matches?.(FOCUSABLE) && !node.disabled);
    if (target) {
      target.focus({ preventScroll: true });
    } else if (doc.activeElement && doc.activeElement !== doc.body) {
      doc.activeElement.blur?.();
    }
  };
  const press = (at, button, count) => {
    mouse.buttons |= MASK[button];
    mouse.down = at.el;
    // A cancelled pointerdown suppresses the compatibility mouse events and, like a
    // cancelled mousedown, keeps focus where it is (toolbars rely on this), as in Chrome.
    mouse.suppress = !fire(at.el, 'pointerdown', at, { button: CODE[button] });
    if (!mouse.suppress && fire(at.el, 'mousedown', at, { button: CODE[button], detail: count })) focusAt(at.el);
    if (button === 'right') fire(at.el, 'contextmenu', at, { button: 2 });
  };
  const release = (at, button, count) => {
    mouse.buttons &= ~MASK[button];
    fire(at.el, 'pointerup', at, { button: CODE[button] });
    if (!mouse.suppress) fire(at.el, 'mouseup', at, { button: CODE[button], detail: count });
    mouse.suppress = false;
    const down = mouse.down;
    mouse.down = null;
    if (!down?.isConnected) return;
    const pressed = chainOf(down);
    const target = chainOf(at.el).find((node) => pressed.includes(node) && node.ownerDocument === at.el.ownerDocument);
    if (!target) return;
    if (button === 'left') {
      fire(target, 'click', at, { detail: count });
      if (count === 2) fire(target, 'dblclick', at, { detail: 2 });
    } else {
      fire(target, 'auxclick', at, { button: CODE[button], detail: count });
    }
  };
  const cancel = (at) => {
    if (!mouse.buttons) return;
    fire(at.el, 'pointercancel', at, { button: -1, cancelable: false });
    mouse.buttons = 0;
    mouse.suppress = false;
    mouse.down = null;
  };
  // Visible tabs move at frame rate so the page and the watcher see the stroke;
  // background tabs yield through a message, which Chrome does not throttle.
  const yieldTask = () =>
    new Promise((done) => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => done();
      channel.port2.postMessage(0);
    });
  const pace = async () => {
    const interval = Number(request.interval) || 0;
    if (interval > 0) {
      // Chrome throttles timers in hidden tabs to once a second; messages are not.
      if (document.visibilityState === 'visible') return new Promise((done) => setTimeout(done, interval * 1000));
      const until = performance.now() + interval * 1000;
      while (performance.now() < until) await yieldTask();
      return undefined;
    }
    if (document.visibilityState === 'visible') {
      return new Promise((done) => {
        const timer = setTimeout(done, 50);
        requestAnimationFrame(() => {
          clearTimeout(timer);
          done();
        });
      });
    }
    return yieldTask();
  };
  const inView = (doc, x, y) => {
    const view = viewOf(doc);
    return x >= 0 && y >= 0 && x < view.innerWidth && y < view.innerHeight;
  };
  // Scroll each document so a point offset from an element's corner is in view.
  const reveal = (el, offset) => {
    if (!offset) {
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      return;
    }
    el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    const rect = el.getBoundingClientRect();
    let point = { x: rect.left + offset[0], y: rect.top + offset[1] };
    let doc = el.ownerDocument;
    for (;;) {
      const view = viewOf(doc);
      if (!inView(doc, point.x, point.y)) {
        const dx = point.x < 0 || point.x >= view.innerWidth ? point.x - view.innerWidth / 2 : 0;
        const dy = point.y < 0 || point.y >= view.innerHeight ? point.y - view.innerHeight / 2 : 0;
        const [sx, sy] = [view.scrollX, view.scrollY];
        view.scrollBy({ left: dx, top: dy, behavior: 'instant' });
        point = { x: point.x - (view.scrollX - sx), y: point.y - (view.scrollY - sy) };
      }
      const frame = parentFrame(doc);
      if (!frame) return;
      const box = frameBox(frame);
      point = { x: box.x + point.x * box.sx, y: box.y + point.y * box.sy };
      doc = frame.ownerDocument;
    }
  };
  const pointerOp = async (targeted, scope) => {
    const op = request.op;
    const button = request.button || 'left';
    if (!(button in MASK)) return fail('invalid_button', 'Use the left, middle or right button');
    const count = Number(request.count ?? 1);
    if (![1, 2, 3].includes(count)) return fail('invalid_count', 'Click count is 1, 2 or 3');
    if (modifiers.some((name) => !['Shift', 'Control', 'Alt', 'Meta'].includes(name))) {
      return fail('invalid_modifier', 'Modifiers are Shift, Control, Alt and Meta');
    }
    let offsets = null;
    if (op === 'drag') offsets = request.path;
    else if (request.x != null || request.y != null) offsets = [[request.x, request.y]];
    if (offsets) {
      const valid = Array.isArray(offsets) && offsets.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite));
      if (!valid) return fail('invalid_point', 'Points are pairs of finite numbers');
      if (op === 'drag' && (offsets.length < 2 || offsets.length > 1000)) {
        return fail('invalid_point', 'A drag needs 2 to 1000 points');
      }
    }
    let el = null;
    let points;
    if (targeted) {
      el = resolve(request, scope);
      if (el.ok === false) return el;
      if (op !== 'scroll' && (el.disabled || el.matches(':disabled'))) {
        return fail('element_not_interactable', 'The element is disabled');
      }
      if (!isVisible(el)) return fail('element_not_interactable', 'The element is not visible');
      reveal(el, offsets?.[0]);
      const rect = el.getBoundingClientRect();
      points = (offsets || [[rect.width / 2, rect.height / 2]]).map(([x, y]) =>
        toTop(el.ownerDocument, rect.left + x, rect.top + y),
      );
    } else if (offsets) {
      points = offsets.map(([x, y]) => ({ x, y }));
    } else if (op === 'scroll') {
      points = [{ x: innerWidth / 2, y: innerHeight / 2 }];
    } else {
      return fail('invalid_target', 'Pass an element (--ref, --selector, --text or --role) or --x and --y');
    }
    const outside = points.find((p) => !inView(document, p.x, p.y));
    if (outside) {
      return fail('point_outside_viewport', `Point ${round(outside.x)},${round(outside.y)} is outside the ${innerWidth}x${innerHeight} viewport`, {
        viewport: { width: innerWidth, height: innerHeight },
      });
    }
    const stopped = await arriveAt(points[0].x, points[0].y);
    if (stopped) return stopped;
    if (el && !el.isConnected) return fail('stale_element', 'The element was removed while the pen moved');
    const first = hitTest(points[0].x, points[0].y);
    if (!first) return fail('element_not_found', 'Nothing is under that point');
    if (first.foreign) {
      return fail('cross_origin_frame', `The point is inside a frame from ${first.foreign}; ca acts only in frames from the page's own origin`, {
        frame: describe(first.el),
      });
    }
    if (el) {
      const rect = topRect(el);
      const within = points[0].x >= rect.x && points[0].x <= rect.x + rect.width &&
        points[0].y >= rect.y && points[0].y <= rect.y + rect.height; // prettier-ignore
      if (within && !chainOf(first.el).includes(el) && !first.el.contains(el)) {
        return fail('element_obscured', `Another element covers the target: ${first.el.tagName.toLowerCase()} ${clip(first.el.innerText, 60)}`, {
          covering: describe(first.el),
        });
      }
    }
    // Chrome never activates a disabled control; :disabled covers disabled fieldsets.
    const control = first.el.closest?.('button,input,select,textarea,option,optgroup');
    if ((op === 'click' || op === 'drag') && control?.matches(':disabled')) {
      return fail('element_not_interactable', 'The control under that point is disabled', {
        target: describe(control),
      });
    }
    const point = { x: round(first.x), y: round(first.y) };
    const url = location.href;
    if (op === 'move') {
      moveTo(first);
      return { ok: true, point, target: describe(first.el), url };
    }
    if (op === 'click') {
      moveTo(first);
      for (let n = 1; n <= count; n++) {
        press(first, button, n);
        release(first, button, n);
      }
      pen?.tap('click');
      return { ok: true, clicked: describe(el || first.el), target: describe(first.el), point, url };
    }
    if (op === 'scroll') {
      const delta = Number(request.delta);
      if (!Number.isFinite(delta)) return fail('invalid_delta', 'Pass a finite --delta');
      const horizontal = request.axis === 'horizontal';
      moveTo(first);
      const allowed = fire(first.el, 'wheel', first, {
        deltaX: horizontal ? delta : 0, deltaY: horizontal ? 0 : delta, deltaMode: 0,
      }); // prettier-ignore
      pen?.tap('press');
      if (!allowed) return { ok: true, point, target: describe(first.el), handled_by_page: true, url };
      // Untrusted wheel events have no default action, so scroll what a wheel would:
      // the nearest box that can still move that way, else the page, unless a box
      // contains its overscroll.
      const doc = first.el.ownerDocument;
      const canMove = (node) => {
        const position = horizontal ? node.scrollLeft : node.scrollTop;
        const limit = horizontal ? node.scrollWidth - node.clientWidth : node.scrollHeight - node.clientHeight;
        return delta > 0 ? position < limit - 0.5 : position > 0.5;
      };
      let box = null;
      for (let node = first.el; node && node.ownerDocument === doc; node = node.parentElement) {
        if (node === doc.body || node === doc.documentElement) break;
        const style = viewOf(node).getComputedStyle(node);
        if (!/auto|scroll|overlay/.test(horizontal ? style.overflowX : style.overflowY)) continue;
        const contained = /contain|none/.test(horizontal ? style.overscrollBehaviorX : style.overscrollBehaviorY);
        if (canMove(node) || contained) {
          box = node;
          break;
        }
      }
      const scroller = box || doc.scrollingElement || doc.documentElement;
      const before = horizontal ? scroller.scrollLeft : scroller.scrollTop;
      scroller.scrollBy({ left: horizontal ? delta : 0, top: horizontal ? 0 : delta, behavior: 'instant' });
      const after = horizontal ? scroller.scrollLeft : scroller.scrollTop;
      return {
        ok: true, point, target: describe(first.el), handled_by_page: false,
        scrolled: { element: box ? describe(box) : 'page', from: round(before), to: round(after) }, url,
      }; // prettier-ignore
    }
    // drag: press at the first point, move through every point in small steps, release.
    const path = [points[0]];
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1];
      const b = points[i];
      const steps = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 8));
      for (let s = 1; s <= steps; s++) path.push({ x: a.x + ((b.x - a.x) * s) / steps, y: a.y + ((b.y - a.y) * s) / steps });
    }
    moveTo(first);
    press(first, button, 1);
    pen?.tap('press');
    let last = first;
    for (let i = 1; i < path.length; i++) {
      await pace();
      const stop = interrupted();
      if (stop) {
        cancel(last);
        stop.extra = { ...stop.extra, gesture: 'cancelled', moved: i - 1 };
        return stop;
      }
      last = hitTest(path[i].x, path[i].y) || { ...last, x: path[i].x, y: path[i].y };
      if (!last.el.isConnected) last = { ...last, el: last.el.ownerDocument?.body || document.body };
      moveTo(last);
      pen?.trace(path[i].x, path[i].y);
    }
    release(last, button, 1);
    if (pen) await pen.to(last.x, last.y, request.pen);
    return {
      ok: true, from: point, to: { x: round(last.x), y: round(last.y) }, moves: path.length - 1,
      target: describe(first.el), end_target: describe(last.el), url,
    }; // prettier-ignore
  };

  try {
    const scope = scopeOf(request.frame);
    if (scope.ok === false) return scope;
    if (request.op === 'read') {
      let root = scope.body || scope.documentElement;
      if (request.selector || request.ref || request.text || request.role) {
        root = resolve(request, scope);
        if (root.ok === false) return root;
      }
      const text = root.innerText ?? root.textContent ?? '';
      const max = request.max_chars;
      return {
        ok: true,
        title: scope.title,
        url: scope.location?.href ?? location.href,
        text: text.slice(0, max),
        length: text.length,
        truncated: text.length > max,
      };
    }
    if (request.op === 'locate') {
      const nodes = find(request, scope);
      if (!Array.isArray(nodes)) return nodes;
      const limit = Math.min(Math.max(Number(request.limit) || 20, 1), 100);
      const reply = { ok: true, url: location.href, count: nodes.length, elements: nodes.slice(0, limit).map(describe) };
      if (!nodes.length && !request.frame?.length) {
        const frames = frameHint(request);
        if (frames.length) reply.frames = frames;
      }
      return reply;
    }
    const targeted = request.ref || request.selector || request.text || request.role;
    if (['click', 'move', 'drag', 'scroll'].includes(request.op)) return await pointerOp(targeted, scope);
    const el = targeted ? resolve(request, scope) : deepActive();
    if (!el || el.ok === false) return el || fail('element_not_found', 'No focused element');
    if (request.op === 'select') {
      if (el.tagName !== 'SELECT' || el.multiple) {
        return fail('unsupported_control', 'Select requires a native single-select control');
      }
      if (el.matches(':disabled') || !isVisible(el)) {
        return fail('element_not_interactable', 'The select is disabled or not visible');
      }
      const options = Array.from(el.options).filter((option) => option.value === request.value);
      if (options.length !== 1) {
        return fail(options.length ? 'ambiguous_target' : 'option_not_found', 'Choose one exact option value');
      }
      const option = options[0];
      if (option.disabled || option.closest('optgroup')?.disabled) {
        return fail('element_not_interactable', 'The option is disabled');
      }
      const stopped = await arrive(el, centre);
      if (stopped) return stopped;
      if (!el.isConnected || !el.contains(option)) return fail('stale_element', 'The select changed while the pen moved');
      if (el.matches(':disabled') || option.disabled || option.closest('optgroup')?.disabled) {
        return fail('element_not_interactable', 'The select or option became disabled');
      }
      // Like type, this is an explicit DOM action, not an OS popup or trusted key.
      const changed = el.selectedIndex !== option.index;
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, request.value);
      if (changed) {
        el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      pen?.tap('select');
      return { ok: true, changed, element: describe(el) };
    }
    if (request.op === 'type') {
      if (!editable(el)) return fail('element_not_editable', `A ${el.tagName.toLowerCase()} does not accept text`);
      if (el.disabled || el.readOnly) return fail('element_not_interactable', 'The field is disabled or read-only');
      const field = topRect(el);
      const stopped = await arrive(el, (rect) => ({ x: rect.x + Math.min(14, rect.width / 2), y: rect.y + rect.height / 2 }));
      if (stopped) return stopped;
      if (!el.isConnected) return fail('stale_element', 'The field was removed while the pen moved');
      el.focus({ preventScroll: true });
      if (el.isContentEditable) {
        const range = el.ownerDocument.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        const selection = viewOf(el).getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      if (request.clear) {
        if (el.isContentEditable) {
          el.textContent = '';
        } else {
          setValue(el, '');
        }
        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
      } else if (!el.isContentEditable && el.selectionStart != null) {
        try {
          el.setSelectionRange(el.value.length, el.value.length);
        } catch (error) {
          // Inputs without a selection API append through the value setter.
        }
      }
      for (const ch of Array.from(String(request.input ?? ''))) {
        key(el, 'keydown', ch, {});
        const before = new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertText', data: ch });
        if (el.dispatchEvent(before)) insert(el, ch);
        key(el, 'keyup', ch, {});
      }
      el.dispatchEvent(new Event('change', { bubbles: true }));
      const typed = Array.from(String(request.input ?? '')).length;
      pen?.write(Math.min(Math.max(field.width - 28, 0), typed * 7), typed);
      return { ok: true, typed: Array.from(String(request.input ?? '')).length, element: describe(el) };
    }
    if (request.op === 'press') {
      const parts = String(request.key || '').split('+');
      const name = parts.pop();
      const modifiers = {
        ctrlKey: parts.includes('Control') || parts.includes('Ctrl'),
        shiftKey: parts.includes('Shift'),
        altKey: parts.includes('Alt'),
        metaKey: parts.includes('Meta'),
      };
      if (!name) return fail('invalid_key', 'Pass a key such as Enter, Escape or Backspace');
      const stopped = await arrive(el, (rect) => ({ x: rect.x + Math.min(14, rect.width / 2), y: rect.y + rect.height / 2 }));
      if (stopped) return stopped;
      pen?.tap('press');
      const proceed = key(el, 'keydown', name, modifiers);
      let effect = 'dispatched';
      if (proceed && !Object.values(modifiers).some(Boolean)) {
        // Untrusted key events have no default action, so emulate the common ones.
        if (name === 'Enter' && el.form && el.tagName === 'INPUT') {
          el.form.requestSubmit();
          effect = 'submitted_form';
        } else if (name === 'Enter' && (roleOf(el) === 'button' || roleOf(el) === 'link')) {
          el.click();
          effect = 'activated';
        } else if (
          (name === 'Backspace' || name === 'Delete') &&
          editable(el) &&
          !el.isContentEditable &&
          !el.disabled &&
          !el.readOnly
        ) {
          const start = el.selectionStart ?? el.value.length;
          const end = el.selectionEnd ?? start;
          const from = start === end ? (name === 'Backspace' ? Math.max(0, start - 1) : start) : start;
          const to = start === end && name === 'Delete' ? end + 1 : end;
          setValue(el, el.value.slice(0, from) + el.value.slice(to));
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: name === 'Backspace' ? 'deleteContentBackward' : 'deleteContentForward' }));
          effect = 'edited';
        }
      }
      key(el, 'keyup', name, modifiers);
      return { ok: true, key: request.key, effect, element: describe(el) };
    }
    return fail('unknown_operation', `Unknown page operation ${request.op}`);
  } catch (error) {
    return fail('page_error', String(error?.message || error));
  }
}

// ----------------------------------------------------------------- startup

chrome.alarms.create('ca-reconnect', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'ca-reconnect' && !port) connect();
});
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
connect();
