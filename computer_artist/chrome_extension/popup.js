// The toolbar card. Everything shown comes from state the worker already keeps:
// the bridge status and claims in session storage, the instance in local storage
// and the browser's own tab list. It never acts on tabs.

const $ = (id) => document.getElementById(id);
const SCRIPTABLE = ['http:', 'https:', 'file:'];
const ACTIONS = {
  click: 'clicked',
  type: 'typed',
  press: 'pressed a key',
  navigate: 'navigated',
  reload: 'reloaded',
};
const STOPS = {
  human_input: (title) => `You clicked or typed in ${title}`,
  human_activated_tab: (title) => `You switched to ${title}`,
  target_closed: (title) => `${title} closed`,
  target_replaced: (title) => `Chrome replaced ${title}`,
};
const HISTORY_MS = 60 * 60 * 1000;

function ago(at) {
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

function since(at) {
  const minutes = Math.round((Date.now() - at) / 60000);
  return minutes < 1 ? 'under a minute' : minutes < 60 ? `${minutes} min` : `${Math.round(minutes / 60)} h`;
}

function host(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'file:' ? 'local file' : parsed.host || parsed.protocol;
  } catch (error) {
    return '';
  }
}

function scheme(url) {
  try {
    return new URL(url).protocol;
  } catch (error) {
    return '';
  }
}

function browserName() {
  const brands = navigator.userAgentData?.brands || [];
  const named = brands.find((b) => !/not.?a.?brand|chromium/i.test(b.brand));
  const version = (navigator.userAgent.match(/Chrome\/(\d+)/) || [])[1] || '';
  return `${named?.brand || 'Chromium'} ${version}`.trim();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

// The latest thing that happened in a claimed tab.
function activity(claim) {
  if (claim.last) return `${ACTIONS[claim.last.op] || claim.last.op} ${ago(claim.last.at)}`;
  return `${claim.opened ? 'opened' : 'claimed'} ${ago(claim.since)}`;
}

async function render() {
  const [session, local, tabs, [current]] = await Promise.all([
    chrome.storage.session.get(['bridge', 'claims', 'revoked']),
    chrome.storage.local.get(['instance', 'label']),
    chrome.tabs.query({}),
    chrome.tabs.query({ active: true, currentWindow: true }),
  ]);
  const byId = new Map(tabs.map((t) => [t.id, t]));
  const claims = Object.entries(session.claims || {}).map(([id, claim]) => ({ id: Number(id), ...claim }));
  const status = session.bridge?.status || 'connecting';

  document.body.dataset.bridge = status;
  $('bridge-text').textContent =
    status === 'connected' ? 'Connected' : status === 'disconnected' ? 'Not connected' : 'Connecting';
  $('browser').textContent = local.label ? `${local.label}, ${browserName()}` : browserName();
  $('instance').textContent = local.instance || 'not set yet';
  $('copy').hidden = !local.instance;
  $('version').textContent = `v${chrome.runtime.getManifest().version}`;
  $('fix').hidden = status !== 'disconnected';
  $('fix-detail').textContent = session.bridge?.detail || '';
  $('fix-detail').hidden = !session.bridge?.detail;

  // This tab.
  const here = $('here');
  here.replaceChildren();
  here.className = 'here';
  const mine = current && claims.find((c) => c.id === current.id);
  const stopped = current && !mine && session.revoked?.[current.id];
  if (mine) {
    here.classList.add('agent');
    const lead = el('p', 'lead');
    lead.append(el('b', '', mine.owner), ' is working in this tab');
    here.append(
      lead,
      el('p', 'meta', `For ${since(mine.since)}, ${activity(mine)}`),
      el('p', 'hint', 'Click, type or scroll in the page to take it back.'),
    );
  } else if (stopped && Date.now() - stopped.at < HISTORY_MS) {
    here.append(
      el('p', 'lead', 'This tab is yours again'),
      el('p', 'meta', `${(STOPS[stopped.reason] || (() => stopped.reason))('it')}, ending ${stopped.owner}'s claim ${ago(stopped.at)}`),
    );
  } else if (current && !SCRIPTABLE.includes(scheme(current.url))) {
    here.append(el('p', 'lead', 'ca cannot work in this page'), el('p', 'meta', 'Browser pages, the Web Store and other extensions are off limits.'));
  } else {
    here.append(el('p', 'lead', 'You have this tab'), el('p', 'meta', 'ca acts only in tabs it opens or that you ask it to claim.'));
  }

  // Agent tabs.
  const list = $('claims');
  list.replaceChildren();
  claims.sort((a, b) => b.since - a.since);
  for (const claim of claims) {
    const tab = byId.get(claim.id);
    const item = el('li', claim.id === current?.id ? 'current' : '');
    item.append(
      el('span', 'title', tab?.title || claim.title || `Tab ${claim.id}`),
      el('span', 'owner', claim.owner),
      el('span', 'meta', [host(tab?.url || ''), activity(claim)].filter(Boolean).join(', ')),
    );
    item.title = tab?.url || '';
    list.append(item);
  }
  $('claims-count').textContent = claims.length ? String(claims.length) : '';
  $('claims-empty').hidden = claims.length > 0;

  // Recent handbacks, newest first.
  const history = Object.values(session.revoked || {})
    .filter((r) => Date.now() - r.at < HISTORY_MS)
    .sort((a, b) => b.at - a.at)
    .slice(0, 3);
  $('history').replaceChildren(
    ...history.map((r) => {
      const item = el('li');
      const title = r.title ? `“${r.title}”` : 'a tab';
      const what = el('span', 'what', (STOPS[r.reason] || (() => r.reason))(title));
      what.title = `${r.owner}'s claim ended: ${r.reason}`;
      const time = el('time', '', ago(r.at));
      time.dateTime = new Date(r.at).toISOString();
      item.append(what, time);
      return item;
    }),
  );
  $('history-block').hidden = history.length === 0;
}

$('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('instance').textContent);
    $('copy').textContent = 'Copied';
  } catch (error) {
    $('copy').textContent = 'Copy failed';
  }
  setTimeout(() => ($('copy').textContent = 'Copy ID'), 1500);
});

let queued = false;
const refresh = () => {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued = false;
    render();
  });
};
chrome.storage.onChanged.addListener(refresh);
chrome.tabs.onUpdated.addListener(refresh);
chrome.tabs.onRemoved.addListener(refresh);
setInterval(refresh, 5000);
// Opening the card wakes a sleeping worker, which reconnects if it can.
chrome.runtime.sendMessage({ type: 'ca-popup' }).catch(() => {});
render();
