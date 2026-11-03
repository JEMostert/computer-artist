// Computer Artist browser bridge: a service worker that lets the local `ca`
// CLI list, claim and operate tabs through the native host
// `com.computer_artist.browser`. Actions run as DOM operations inside the page
// (untrusted events); nothing here moves the pointer, activates a tab or
// focuses a window. Claimed tabs carry a lease, a "CA" badge and, for tabs ca
// opened, a "CA" tab group. Human input in a claimed tab revokes the lease.

const HOST = 'com.computer_artist.browser';
const PROTOCOL = 1;
const VERSION = chrome.runtime.getManifest().version;
const BADGE = '#4058ff';
const GROUP_TITLE = 'CA';
const MAX_TEXT = 100000;
const MAX_READ = 200000;
const ALLOWED_SCHEMES = ['http:', 'https:', 'file:'];

let port = null;
let ready = false;
let retryDelay = 1000;
let retryTimer = null;
let identityPromise = null;
let statePromise = null;

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
  };
}

// --------------------------------------------------------------- transport

function setConnected(value, detail) {
  ready = value;
  chrome.action.setBadgeText({ text: value ? '' : '!' });
  chrome.action.setBadgeBackgroundColor({ color: value ? BADGE : '#b3261e' });
  chrome.action.setTitle({
    title: value
      ? 'Computer Artist: connected to ca'
      : `Computer Artist: not connected to ca${detail ? ` (${detail})` : ''}. ` +
        'Run `ca browser install`, then reload this extension.',
  });
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
    handle(message).then(
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

function state() {
  statePromise ??= chrome.storage.session
    .get(['claims', 'revoked'])
    .then((stored) => ({ claims: stored.claims || {}, revoked: stored.revoked || {} }));
  return statePromise;
}

async function persist() {
  const current = await state();
  const revoked = Object.entries(current.revoked)
    .sort((a, b) => b[1].at - a[1].at)
    .slice(0, 50);
  current.revoked = Object.fromEntries(revoked);
  await chrome.storage.session.set({ claims: current.claims, revoked: current.revoked });
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
    current.revoked[tabId] = { reason, owner: claim.owner, lease: claim.lease, at: Date.now() };
  }
  await persist();
  if (reason !== 'target_closed') {
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

function installHumanWatch() {
  // Runs in the extension's isolated world of the claimed page.
  if (globalThis.__caHumanWatch) return true;
  globalThis.__caHumanWatch = true;
  const report = (event) => {
    if (!event.isTrusted) return;
    try {
      chrome.runtime.sendMessage({ type: 'ca-human-input', kind: event.type }).catch(() => {});
    } catch (error) {
      // The extension was reloaded; this page's watcher is orphaned.
    }
  };
  for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    addEventListener(type, report, { capture: true, passive: true });
  }
  return true;
}

async function watchTab(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    world: 'ISOLATED',
    func: installHumanWatch,
  });
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === 'ca-human-input' && sender.id === chrome.runtime.id && sender.tab) {
    state().then((current) => {
      if (current.claims[sender.tab.id]) endClaim(sender.tab.id, 'human_input');
    });
  }
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
  // ca never activates tabs, so an activation hands the tab to the human.
  state().then((current) => {
    if (current.claims[tabId]) endClaim(tabId, 'human_activated_tab');
  });
});

chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status !== 'complete') return;
  state().then((current) => {
    if (current.claims[tabId]) watchTab(tabId).catch(() => {});
  });
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

async function inPage(tabId, request) {
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'ISOLATED',
      func: pageAction,
      args: [request],
    });
  } catch (error) {
    throw new Refusal(
      'page_unavailable',
      `Cannot script tab ${tabId}: ${error.message}. Browser pages, the web store and other ` +
        'extensions cannot be scripted; file URLs need "Allow access to file URLs".',
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

async function groupOpenedTab(tab) {
  try {
    const [group] = await chrome.tabGroups.query({ windowId: tab.windowId, title: GROUP_TITLE });
    const groupId = await chrome.tabs.group(
      group ? { tabIds: [tab.id], groupId: group.id } : { tabIds: [tab.id], createProperties: { windowId: tab.windowId } },
    );
    if (!group) await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: 'blue' });
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
  const claim = { lease: token(), owner, since: Date.now(), opened };
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
          tab = await chrome.tabs.create(properties);
        } catch (error) {
          throw new Refusal('window_unavailable', `Cannot open a background tab: ${error.message}`);
        }
        return tab;
      });
      if (!tab) throw new Refusal('window_unavailable', 'Chrome did not open the tab');
      let claim;
      try {
        claim = await claimTab(tab, owner, true);
      } catch (error) {
        await chrome.tabs.remove(tab.id).catch(() => {});
        throw error;
      }
      await groupOpenedTab(tab);
      return { lease: claim.lease, loaded, ...(await describeTab(await getTab(tab.id))) };
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
    case 'type':
    case 'press': {
      await requireLease(tabId, request.lease);
      await getTab(tabId);
      if (request.op === 'type' && String(request.input ?? '').length > MAX_TEXT) {
        throw new Refusal('payload_too_large', `Type at most ${MAX_TEXT} characters at once`);
      }
      return inPage(tabId, request);
    }
    case 'screenshot': {
      const tab = await getTab(tabId);
      const window = await chrome.windows.get(tab.windowId);
      if (!tab.active || window.state === 'minimized') {
        throw new Refusal(
          'tab_not_visible',
          'Chrome can capture only the active tab of a visible window, and ca never switches ' +
            'tabs. Read or locate this tab instead, or ask the user to show it.',
          { active: tab.active, window_state: window.state },
        );
      }
      const data = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
      const after = await getTab(tabId);
      if (!after.active) throw new Refusal('tab_not_visible', 'The tab was switched during capture');
      return { tab: tabId, format: 'png', data_url: data };
    }
    default:
      throw new Refusal('unknown_operation', `Unknown operation ${request.op}`);
  }
}

// ---------------------------------------------------------- page functions

// Runs inside the page's isolated world. It must be self-contained: Chrome
// serializes this function's source into the page.
function pageAction(request) {
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
  const nameOf = (el) => {
    const labelled = el.getAttribute('aria-labelledby');
    const fromIds = labelled
      ? labelled.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' ')
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
    const style = getComputedStyle(el);
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
  const describe = (el) => {
    const rect = el.getBoundingClientRect();
    const info = {
      ref: refOf(el),
      tag: el.tagName.toLowerCase(),
      role: roleOf(el) || null,
      name: nameOf(el),
      visible: isVisible(el),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
    };
    if ('value' in el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
      info.value = el.type === 'password' ? '[hidden]' : clip(el.value, 200);
    }
    if (el.type === 'checkbox' || el.type === 'radio') info.checked = el.checked;
    return info;
  };
  const interactive =
    'a[href],button,input,textarea,select,summary,label,[role],[tabindex],[contenteditable=""],[contenteditable=true],[onclick]';
  const find = (target) => {
    let nodes;
    if (target.selector) {
      try {
        nodes = Array.from(document.querySelectorAll(target.selector));
      } catch (error) {
        return fail('invalid_selector', `Invalid CSS selector: ${target.selector}`);
      }
    } else if (target.text || target.role) {
      const wanted = String(target.text || target.name || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const match = (selector, label) => {
        const candidates = Array.from(document.querySelectorAll(selector))
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
    } else {
      return fail('invalid_target', 'Pass a ref, selector, text or role');
    }
    return nodes;
  };
  const resolve = (target) => {
    if (target.ref) {
      const ref = String(target.ref);
      const el = ref.split('-')[0] === ca.doc ? ca.refs.get(ref)?.deref() : null;
      if (!el || !el.isConnected) {
        return fail('stale_element', `Element ${ref} is from an earlier page or was removed; locate it again`);
      }
      return el;
    }
    const nodes = find(target);
    if (!Array.isArray(nodes)) return nodes;
    if (!nodes.length) return fail('element_not_found', 'No element matches the target');
    if (nodes.length > 1 && !target.first) {
      return fail('ambiguous_target', `${nodes.length} elements match; pass a ref or --first`, {
        candidates: nodes.slice(0, 5).map(describe),
      });
    }
    return nodes[0];
  };
  const eventInit = (el, extra = {}) => {
    const rect = el.getBoundingClientRect();
    return {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: rect.x + rect.width / 2,
      clientY: rect.y + rect.height / 2,
      ...extra,
    };
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
      if (!document.execCommand('insertText', false, text)) {
        const selection = getSelection();
        const range = selection.rangeCount ? selection.getRangeAt(0) : document.createRange();
        range.deleteContents();
        range.insertNode(document.createTextNode(text));
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

  try {
    if (request.op === 'read') {
      let root = document.body || document.documentElement;
      if (request.selector || request.ref || request.text) {
        root = resolve(request);
        if (root.ok === false) return root;
      }
      const text = root.innerText ?? root.textContent ?? '';
      const max = request.max_chars;
      return {
        ok: true,
        title: document.title,
        url: location.href,
        text: text.slice(0, max),
        length: text.length,
        truncated: text.length > max,
      };
    }
    if (request.op === 'locate') {
      const nodes = find(request);
      if (!Array.isArray(nodes)) return nodes;
      const limit = Math.min(Math.max(Number(request.limit) || 20, 1), 100);
      return { ok: true, url: location.href, count: nodes.length, elements: nodes.slice(0, limit).map(describe) };
    }
    const el = request.ref || request.selector || request.text || request.role ? resolve(request) : document.activeElement;
    if (!el || el.ok === false) return el || fail('element_not_found', 'No focused element');
    if (request.op === 'click') {
      if (el.disabled) return fail('element_not_interactable', 'The element is disabled');
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      if (!isVisible(el)) return fail('element_not_interactable', 'The element is not visible');
      const init = eventInit(el);
      const hit = document.elementFromPoint(init.clientX, init.clientY);
      if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) {
        return fail('element_obscured', `Another element covers the target: ${hit.tagName.toLowerCase()} ${clip(hit.innerText, 60)}`, {
          covering: describe(hit),
        });
      }
      el.dispatchEvent(new PointerEvent('pointerdown', { ...init, pointerType: 'mouse', isPrimary: true }));
      el.dispatchEvent(new MouseEvent('mousedown', { ...init, button: 0, buttons: 1 }));
      if (typeof el.focus === 'function') el.focus({ preventScroll: true });
      el.dispatchEvent(new PointerEvent('pointerup', { ...init, pointerType: 'mouse', isPrimary: true }));
      el.dispatchEvent(new MouseEvent('mouseup', { ...init, button: 0 }));
      el.click();
      return { ok: true, clicked: describe(el), url: location.href };
    }
    if (request.op === 'type') {
      if (!editable(el)) return fail('element_not_editable', `A ${el.tagName.toLowerCase()} does not accept text`);
      if (el.disabled || el.readOnly) return fail('element_not_interactable', 'The field is disabled or read-only');
      el.focus({ preventScroll: true });
      if (el.isContentEditable) {
        const range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
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
        } else if ((name === 'Backspace' || name === 'Delete') && editable(el) && !el.isContentEditable) {
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
