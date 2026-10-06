// The console's state, its addresses (#/chats, #/items, ?task=, ?workflow=), API calls
// and small formatting helpers.

const $ = (id) => document.getElementById(id);
const state = { chats: [], archivedChats: [], showArchived: false, chatMenu: null, chatDeleteArmed: null, repositories: [], deploymentTargets: [], repositoryId: null, chatId: null, messages: [], tasks: [],
                workflowId: null, taskId: null, busy: false, timer: null,
                taskTimer: null, prPollAttempts: 0, pendingAction: null,
                workflowRuns: [], items: [], focus: [], itemId: null,
                itemRef: null };

// Every view has its own address: #/chats/<id>, #/items/<slug>; ?task=<id> or
// ?workflow=<id> opens a drawer over either, so back/forward, reload and push links work.
// Each section has a view (#<section>-view, chats: #chat-view) and a nav link (#nav-<section>).
const SECTIONS = ["chats", "items", "memories"];
const sectionView = (section) => $(section === "chats" ? "chat-view" : `${section}-view`);

function parseRoute() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [section, id = null] = path.split("/").map(decodeURIComponent);
  const params = new URLSearchParams(query);
  return { section: SECTIONS.includes(section) ? section : "chats", id: id || null,
    task: params.get("task"), workflow: params.get("workflow") };
}
function routeHash({ section, id = null, task = null, workflow = null }) {
  const drawer = task ? "?task=" + encodeURIComponent(task)
    : workflow ? "?workflow=" + encodeURIComponent(workflow) : "";
  return `#/${section}${id ? "/" + encodeURIComponent(id) : ""}${drawer}`;
}
function go(route) {
  const hash = routeHash(route);
  if (location.hash !== hash) location.hash = hash; else render();
}
const goTask = (task) => go({ ...parseRoute(), workflow: null, task });
const goWorkflow = (workflow) => go({ ...parseRoute(), task: null, workflow });
const closeDrawer = () => go({ ...parseRoute(), task: null, workflow: null });
const goChat = (id) => go({ section: "chats", id });
const goItem = (ref) => go({ section: "items", id: ref });

async function render() {
  const route = parseRoute();
  const items = route.section === "items";
  // A view coming back into sight is refreshed even when its route is unchanged.
  const returning = sectionView(route.section).hidden;
  for (const section of SECTIONS) {
    const shown = section === route.section;
    sectionView(section).hidden = !shown;
    sectionView(section).classList.toggle("has-detail", shown && Boolean(route.id));
    $(`nav-${section}`).classList.toggle("active", shown);
    $(`nav-${section}`).toggleAttribute("aria-current", shown);
  }
  if (route.section === "memories") {
    loadMemories();
  } else if (items) {
    if (route.id !== state.itemRef || returning) {
      state.itemRef = route.id;
      if (route.id) await renderWorkItem(route.id);
      else { state.itemId = null; $("item-page").innerHTML = `<div class="empty">Pick a work item.</div>`; }
    }
    loadItems();
  } else if (route.id !== state.chatId) {
    await openChat(route.id);
  } else if (returning) {
    await Promise.all([loadChats(), refreshTranscript()]);
  }
  if (route.task) { if (route.task !== state.taskId) await renderTask(route.task); }
  else if (route.workflow) { if (route.workflow !== state.workflowId) await renderWorkflow(route.workflow); }
  else if (state.taskId || state.workflowId) hideTask();
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" }, ...options,
  });
  if (!res.ok) {
    let detail = res.status + " " + res.statusText;
    try { const body = await res.json(); if (body.detail) detail = body.detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}
function fail(error) { $("error").textContent = error.message || String(error); }
function clearError() { $("error").textContent = ""; }
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const label = (s) => String(s || "").replace(/_/g, " ");
const active = (t) => !["completed", "failed", "cancelled", "superseded"].includes(t.status);
const shortSha = (sha) => sha ? String(sha).slice(0, 8) : "—";
const tone = (value) => {
  const normalized = String(value || "").toLowerCase();
  if (["passed", "pass", "success", "succeeded", "completed", "mergeable"].includes(normalized)) return "pass";
  if (["failed", "failure", "error", "cancelled", "missing", "stale"].includes(normalized)) return "fail";
  if (["pending", "in_progress", "queued", "waiting", "waiting_for_approval", "draft"].includes(normalized)) return "warn";
  return "";
};
const statusPill = (value, text = label(value)) => `<span class="status-pill ${tone(value)}"><span class="dot ${tone(value) || "muted"}"></span>${esc(text)}</span>`;

async function pendingApproval(taskId) {
  const res = await fetch(`/tasks/${taskId}/pending-approval`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function pullRequestStatus(taskId) {
  const res = await fetch(`/tasks/${taskId}/pull-request-status`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function taskDeployment(taskId) {
  const res = await fetch(`/tasks/${taskId}/deployment-workflow`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function loadChats() {
  try {
    state.chats = (await api("/chat-sessions")).sessions;
    state.archivedChats = state.showArchived
      ? (await api("/chat-sessions?archived=true")).sessions : [];
    renderChats();
    clearError();
  } catch (e) { fail(e); }
}

// The chat list: Pinned, then one section per repository a chat's coding work was in,
// then Other; archived chats fold away at the bottom. Each chat has a ⋯ menu.
const repositoryName = (id) => (state.repositories.find((r) => r.id === id) || {}).name || id;

function chatRow(c) {
  const open = state.chatMenu === c.id;
  const armed = state.chatDeleteArmed === c.id;
  const actions = open ? `<div class="chat-actions">
      ${c.archived ? "" : `<button type="button" class="quiet" data-chat-action="${c.pinned ? "unpin" : "pin"}" data-id="${c.id}">${c.pinned ? "Unpin" : "Pin"}</button>`}
      <button type="button" class="quiet" data-chat-action="${c.archived ? "unarchive" : "archive"}" data-id="${c.id}">${c.archived ? "Unarchive" : "Archive"}</button>
      <button type="button" class="${armed ? "danger" : "quiet"}" data-chat-action="delete" data-id="${c.id}">${armed ? "Delete for good?" : "Delete"}</button>
    </div>` : "";
  return `<div class="chat-row">
      <button class="chat-item ${c.id === state.chatId ? "active" : ""}" data-chat="${c.id}" type="button">
        <div>${c.pinned ? "📌 " : ""}${esc(c.title)}</div>
        <div class="when">${new Date(c.updated_at).toLocaleString()}</div>
      </button>
      <button type="button" class="ghost icon-btn chat-more" data-chat-menu="${c.id}"
        aria-expanded="${open}" aria-label="More for ${esc(c.title)}" title="Pin, archive or delete">⋯</button>
    </div>${actions}`;
}

function renderChats() {
  const pinned = state.chats.filter((c) => c.pinned);
  const rest = state.chats.filter((c) => !c.pinned);
  const repos = [...new Set(rest.map((c) => c.repository_id).filter(Boolean))];
  const sections = [["Pinned", pinned],
    ...repos.map((repo) => [repositoryName(repo), rest.filter((c) => c.repository_id === repo)]),
    [repos.length || pinned.length ? "Other" : "", rest.filter((c) => !c.repository_id)]];
  const list = sections.filter(([, chats]) => chats.length).map(([name, chats]) =>
    (name ? `<div class="space-head">${esc(name)}</div>` : "") + chats.map(chatRow).join("")).join("");
  const archived = `<button type="button" class="ghost archived-toggle" data-chat-archived
      aria-expanded="${Boolean(state.showArchived)}">${state.showArchived ? "▾" : "▸"} Archived</button>`
    + (state.showArchived ? (state.archivedChats.map(chatRow).join("")
      || `<div class="sub">Nothing archived.</div>`) : "");
  $("chat-list").innerHTML = (list || `<div class="sub">No conversations yet.</div>`) + archived;
}

async function chatAction(action, id) {
  if (action === "delete" && state.chatDeleteArmed !== id) {
    state.chatDeleteArmed = id;  // a second tap confirms; no browser dialog (the app has none)
    return renderChats();
  }
  state.chatMenu = null;
  state.chatDeleteArmed = null;
  if (action === "delete") {
    await api(`/chat-sessions/${id}`, { method: "DELETE" });
    if (state.chatId === id) { state.chatId = null; go({ section: "chats" }); }
  } else {
    const change = { pin: { pinned: true }, unpin: { pinned: false },
      archive: { archived: true }, unarchive: { archived: false } }[action];
    await api(`/chat-sessions/${id}`, { method: "PATCH", body: JSON.stringify(change) });
  }
  await loadChats();
}

async function loadRepositories() {
  try {
    state.repositories = (await api("/repositories")).repositories;
    $("repository").innerHTML = `<option value="">Pick for me</option>`
      + state.repositories.map((repository) => `<option value="${esc(repository.id)}"
        ${repository.id === state.repositoryId ? "selected" : ""}>
        ${esc(repository.name)}</option>`).join("");
    if (state.chats.length) renderChats();  // section names use repository names
  } catch (e) { fail(e); }
}

async function loadDeploymentTargets() {
  try {
    state.deploymentTargets = (await api("/deployment-targets")).targets;
    if (state.taskId) await renderTask(state.taskId);
  }
  catch (e) { fail(e); }
}
