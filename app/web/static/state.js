// The console's state, its addresses (#/chats, #/items, ?task=, ?workflow=), API calls
// and small formatting helpers.

const $ = (id) => document.getElementById(id);
const state = { chats: [], repositories: [], deploymentTargets: [], repositoryId: null, chatId: null, messages: [], tasks: [],
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
    $("chat-list").innerHTML = state.chats.map((c) => `
      <div class="chat-row">
        <button class="chat-item ${c.id === state.chatId ? "active" : ""}" data-chat="${c.id}" type="button">
          <div>${esc(c.title)}</div>
          <div class="when">${new Date(c.updated_at).toLocaleString()}</div>
        </button>
        <button class="ghost icon-btn chat-delete" data-chat-delete="${c.id}" type="button"
          title="Delete conversation" aria-label="Delete conversation ${esc(c.title)}">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>
            <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            <line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line>
          </svg>
        </button>
      </div>`).join("") || `<div class="sub">No conversations yet.</div>`;
    clearError();
  } catch (e) { fail(e); }
}

async function loadRepositories() {
  try {
    state.repositories = (await api("/repositories")).repositories;
    $("repository").innerHTML = `<option value="">Pick for me</option>`
      + state.repositories.map((repository) => `<option value="${esc(repository.id)}"
        ${repository.id === state.repositoryId ? "selected" : ""}>
        ${esc(repository.name)}</option>`).join("");
  } catch (e) { fail(e); }
}

async function loadDeploymentTargets() {
  try {
    state.deploymentTargets = (await api("/deployment-targets")).targets;
    if (state.taskId) await renderTask(state.taskId);
  }
  catch (e) { fail(e); }
}
