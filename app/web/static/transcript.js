// A conversation: loading it, rendering messages with their blocks and task cards,
// and sending what the user says.

async function openChat(id) {
  state.chatId = id;
  if (!id) {
    clearInterval(state.timer);
    state.messages = []; state.tasks = []; state.focus = [];
    $("chat-title").textContent = "No conversation open";
    $("transcript").innerHTML = `<div class="empty">Start or pick a conversation.</div>`;
    $("status").textContent = "";
    renderFocusBar();
  }
  await Promise.all([loadChats(), refreshTranscript()]);
}

async function refreshTranscript() {
  if (!state.chatId) return;
  try {
    const [msgs, tasks, runs, focus] = await Promise.all([
      api(`/chat-sessions/${state.chatId}/messages`),
      api(`/chat-sessions/${state.chatId}/tasks`),
      api(`/workflow-runs?chat_session_id=${encodeURIComponent(state.chatId)}&limit=50`),
      api(`/chat-sessions/${state.chatId}/focus`),
    ]);
    state.focus = focus.work_items;
    renderFocusBar();
    state.messages = msgs.messages; state.tasks = tasks.tasks;
    state.workflowRuns = runs.runs.filter((run) => run.workflow_id === "coding-change");
    speakReplyIfWaiting();
    const chat = state.chats.find((c) => c.id === state.chatId);
    $("chat-title").textContent = chat ? chat.title : "Conversation";
    renderTranscript();
    // Keep polling only while something is actually running.
    const running = state.tasks.some((task) =>
      ["planning", "executing", "validating"].includes(task.status));
    const queued = state.tasks.some((task) => task.status === "created");
    $("status").textContent = running ? "working…" : queued ? "queued…" : "";
    clearInterval(state.timer);
    if (running || queued) state.timer = setInterval(refreshTranscript, 2000);
    if (state.taskId) renderTask(state.taskId);
    clearError();
  } catch (e) { fail(e); }
}

const CODING_CAPABILITIES = ["coding", "pull_request_creation", "coding-pull-request"];

// Say what the work is doing rather than naming the task machinery.
// The supervisor answering a message is a chat reply, not separate work.
const workCapabilities = (task) => (task.required_capabilities || []).filter((c) => c !== "supervision");

function taskCardLabel(task) {
  const caps = workCapabilities(task);
  const research = caps.includes("web_research");
  const kind = research ? "Research" : caps.some((c) => CODING_CAPABILITIES.includes(c)) ? "Coding run"
    : task.work_item_id ? "Task" : "Reply";
  if (task.status === "failed") return `${kind} failed`;
  if (task.status === "cancelled") return `${kind} cancelled`;
  if (task.status === "completed") return `${kind} done`;
  if (task.status === "waiting_for_approval") return `${kind} · waiting for your review`;
  if (task.status === "created") return `${kind} queued…`;
  if (!active(task)) return `${kind} · ${label(task.status)}`;
  // What the agent itself last reported, e.g. "Writing app/web/index.html".
  if (task.progress) return `${kind} · ${task.progress}`;
  return research ? "Researching…" : kind === "Reply" ? "Thinking…" : `${kind} working…`;
}

const WORKFLOW_STATUS = { proposed: "waiting for your approval", approved: "approved · ready to start",
  running: "running", completed: "done", failed: "failed", rejected: "declined", cancelled: "cancelled" };
const workflowStatus = (status) => WORKFLOW_STATUS[status] || label(status);

// Pre-built pieces of UI a reply can carry. The server only sends data (which block,
// with what values); nothing here renders markup from a message.
const BLOCKS = {
  choices: (block) => `<div class="choices">${block.options.map((option) => {
    const picked = block.state === "chosen" && option.label === block.chosen;
    return `<button type="button" class="${option.primary ? "" : "quiet"} ${picked ? "picked" : ""}"`
      + ` data-choice="${esc(option.label)}" ${block.state === "open" ? "" : "disabled"}>${esc(option.label)}</button>`;
  }).join("")}</div>`,
  link: (block) => block.href.startsWith("#/")
    ? `<a class="block-link" href="${esc(block.href)}">${esc(block.label)} ›</a>`
    : `<a class="block-link" href="${esc(block.href)}" target="_blank" rel="noreferrer">${esc(block.label)} ↗</a>`,
  workflow: (block) => {
    const run = state.workflowRuns.find((candidate) => candidate.id === block.workflow_run_id);
    const status = run ? run.status : "proposed";
    const dot = ["proposed", "approved", "running"].includes(status) ? "run" : status === "failed" ? "fail" : "";
    return `<button class="taskcard" data-workflow="${esc(block.workflow_run_id)}" type="button">`
      + `<span class="dot ${dot}"></span><span class="grow">Coding workflow · ${esc(workflowStatus(status))}</span>`
      + `<span>Review ›</span></button>`;
  },
  item: (block) => `<button type="button" class="chip link-button" data-item="${esc(block.slug)}">#${esc(block.slug)}</button>`,
};
const renderBlocks = (m) => (m.blocks || []).map((block) => (BLOCKS[block.type] || (() => ""))(block)).join("");

function renderTranscript() {
  const byId = Object.fromEntries(state.tasks.map((t) => [t.id, t]));
  const atBottom = $("transcript").scrollHeight - $("transcript").scrollTop
    - $("transcript").clientHeight < 80;
  $("transcript").innerHTML = state.messages.map((m) => {
    const linked = m.linked_task_id ? byId[m.linked_task_id] : null;
    // Every chat reply runs as a small task; only real work, running, or failed tasks
    // earn a card, so ordinary conversation reads like a chat.
    const task = linked && (active(linked) || linked.status !== "completed"
      || workCapabilities(linked).length || linked.work_item_id) ? linked : null;
    const who = m.role === "user" ? "YOU" : m.role === "system" ? "TASK REFERENCE" : "ASSISTANT";
    // The bubble keeps the message's own line breaks (pre-wrap), so no template
    // whitespace may sit inside it.
    const extras = renderBlocks(m)
      + (task ? `<button class="taskcard" data-task="${task.id}" type="button" aria-label="View: ${esc(taskCardLabel(task))}">`
        + `<span class="dot ${active(task) ? "run" : task.status === "failed" ? "fail" : ""}"></span>`
        + `<span class="grow">${esc(taskCardLabel(task))}</span><span>View ›</span></button>` : "");
    return `<div class="msg ${m.role}"><div class="who">${who}</div>`
      + `<div class="bubble">${esc(m.content)}${extras}</div></div>`;
  }).map((html, index) => html + phoneTurnsAfter(state.messages[index].id)).join("")
    // Phone turns from before the first saved message (or a chat not saved yet).
    .replace(/^/, phoneTurnsAfter(null)) || `<div class="empty">No messages yet.</div>`;
  if (atBottom) $("transcript").scrollTop = $("transcript").scrollHeight;
}

async function send(text, { local = true, voice = false } = {}) {
  state.busy = true; $("send").disabled = true;
  try {
    if (local && phone.bridge && !awaitingChoice()) {
      // Simple things (the time, a timer) can be answered on the phone itself.
      const route = await phoneRoute(text);
      if (route && route.route !== "cloud") return answerOnPhone(text, route, voice);
    }
    if (!state.chatId) {
      state.chatId = (await api("/chat-sessions", { method: "POST", body: "{}" })).id;
      goChat(state.chatId);
      loadChats();
    }
    const posted = await api(`/chat-sessions/${state.chatId}/messages`, {
      method: "POST", body: JSON.stringify({ content: text, repository_id: state.repositoryId,
        local_time: localIsoTime(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }),
    });
    // A spoken question gets its answer spoken too, whenever it arrives.
    if (voice) phone.speakFor = posted.message.id;
    // The server decides what the message needs; the repository picker is only an override.
    // When it already took care of it (an action, an offer with choices, or a choice
    // picked), its reply is in the transcript; otherwise the message gets a chat reply.
    const decision = posted.decision || { intent: "answer" };
    if (decision.handled || decision.intent === "direct_action") {
      await refreshTranscript();
      if (state.itemId) await renderWorkItem(state.itemId);
      await loadItems();
      return;
    }
    await createTask(posted.message.id);
  } catch (e) { fail(e); }
  finally { state.busy = false; $("send").disabled = false; }
}

function localIsoTime() {
  // The user's wall-clock time, so "tomorrow at 9" resolves in their timezone.
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

async function createTask(messageId) {
  try {
    const body = {
      source_context: { client: "web-console",
        local_time: new Date().toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    };
    await api(`/chat-sessions/${state.chatId}/messages/${messageId}/task`,
      { method: "POST", body: JSON.stringify(body) });
    await refreshTranscript();
  } catch (e) { fail(e); }
}

async function startCodingWorkflow(runId) {
  try {
    const run = await api(`/workflow-runs/${runId}/start`, { method: "POST", body: "{}" });
    await refreshTranscript();
    if (run.task_id) goTask(run.task_id); else await renderWorkflow(runId);
    return run;
  } catch (error) {
    fail(error);
    throw error;
  }
}
