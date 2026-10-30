// A conversation: loading it, rendering messages with their blocks and task cards,
// and sending what the user says.

async function openChat(id) {
  state.chatId = id;
  state.transcriptSignature = null;  // a different chat: draw it even if it looks the same
  state.messages = [];
  state.nextBefore = null;
  state.transcriptLoaded = false;
  state.loadingEarlier = false;
  if (!id) {
    stopPolling();
    state.tasks = []; state.focus = [];
    $("chat-title").textContent = "No conversation open";
    $("transcript").innerHTML = `<div class="empty">Start or pick a conversation.</div>`;
    $("status").textContent = "";
    renderFocusBar();
  }
  await Promise.all([loadChats(), refreshTranscript()]);
  window.applyAvatarForChat?.();
}

// While something runs the page looks again quickly at first, then gently; it doesn't look
// at all while the tab is hidden, and catches up when it comes back.
function pollDelay() {
  const age = Date.now() - (state.pollStarted || Date.now());
  return age < 10_000 ? 1000 : age < 60_000 ? 2000 : 5000;
}
function stopPolling() { clearTimeout(state.timer); state.timer = null; }
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { stopPolling(); return; }
  if (state.chatId) { state.pollStarted = Date.now(); refreshTranscript(); }
});

function mergeMessages(...pages) {
  const byId = new Map();
  for (const page of pages) for (const message of page) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) =>
    new Date(a.created_at) - new Date(b.created_at) || a.id.localeCompare(b.id));
}

async function refreshTranscript() {
  if (!state.chatId) return;
  const chatId = state.chatId;
  try {
    const [msgs, tasks, runs, focus] = await Promise.all([
      api(`/chat-sessions/${chatId}/messages`),
      api(`/chat-sessions/${chatId}/tasks`),
      api(`/workflow-runs?chat_session_id=${encodeURIComponent(chatId)}&limit=50`),
      api(`/chat-sessions/${chatId}/focus`),
    ]);
    if (state.chatId !== chatId) return;
    const messages = mergeMessages(state.messages, msgs.messages);
    if (!state.transcriptLoaded) state.nextBefore = msgs.next_before;
    state.transcriptLoaded = true;
    // Draw only when something is different, so text you selected stays selected.
    const signature = JSON.stringify([messages, tasks, runs, focus,
      state.pending.filter((entry) => entry.chatId === chatId).map((entry) => entry.messageId)]);
    const changed = signature !== state.transcriptSignature;
    state.transcriptSignature = signature;
    state.messages = messages;
    state.tasks = tasks.tasks;
    if (changed) {
      state.focus = focus.work_items;
      renderFocusBar();
      state.workflowRuns = runs.runs.filter((run) => run.workflow_id === "coding-change");
      speakDraftIfWaiting();
      speakReplyIfWaiting();
      syncPhoneAlarmsAfterReply();
      const chat = state.chats.find((c) => c.id === chatId);
      $("chat-title").textContent = chat ? chat.title : "Conversation";
      renderTranscript();
    }
    // Keep polling only while something is actually running.
    const running = state.tasks.some((task) =>
      ["planning", "executing", "validating"].includes(task.status));
    const queued = state.tasks.some((task) => task.status === "created");
    $("status").textContent = running ? "working…" : queued ? "queued…" : "";
    stopPolling();
    if (running || queued) {
      if (!state.pollStarted) state.pollStarted = Date.now();
      if (!document.hidden) state.timer = setTimeout(refreshTranscript, pollDelay());
    } else {
      state.pollStarted = null;
    }
    if (changed && state.taskId) renderTask(state.taskId);
    if (!state.stickyError) clearError();
  } catch (e) { fail(e); }
}

async function loadEarlierMessages() {
  if (!state.chatId || !state.nextBefore || state.loadingEarlier) return;
  const chatId = state.chatId;
  const before = state.nextBefore;
  const transcript = $("transcript");
  const previousHeight = transcript.scrollHeight;
  const previousTop = transcript.scrollTop;
  state.loadingEarlier = true;
  let button = transcript.querySelector("[data-load-earlier]");
  if (button) { button.disabled = true; button.textContent = "Loading…"; }
  try {
    const page = await api(`/chat-sessions/${chatId}/messages?before=${encodeURIComponent(before)}`);
    if (state.chatId !== chatId) return;
    state.messages = mergeMessages(state.messages, page.messages);
    state.nextBefore = page.next_before;
    state.transcriptSignature = null;
    renderTranscript();
    transcript.scrollTop = previousTop + transcript.scrollHeight - previousHeight;
  } catch (e) { fail(e); }
  finally {
    state.loadingEarlier = false;
    button = transcript.querySelector("[data-load-earlier]");
    if (button) { button.disabled = false; button.textContent = "Load earlier"; }
  }
}

const CODING_CAPABILITIES = ["coding", "pull_request_creation", "coding-pull-request"];

// Say what the work is doing rather than naming the task machinery.
// The supervisor answering a message is a chat reply, not separate work.
// So is the grocery agent looking something up in the user's stores.
const CHAT_CAPABILITIES = ["supervision", "groceries"];
const workCapabilities = (task) => (task.required_capabilities || []).filter((c) => !CHAT_CAPABILITIES.includes(c));

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
  if ((task.required_capabilities || []).includes("groceries")) return "Checking your stores…";
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
  // Context attached on the phone (the screen the user was on); the text itself stays folded.
  attachment: (block) => `<div class="attachment-chip">📱 Screen${block.app ? " from " + esc(block.app) : ""}`
    + ` · ${esc(block.text.length.toLocaleString())} characters</div>`,
  // A file handed over right here; the server sends it to the upload's handler.
  upload: (block, m) => block.state === "done"
    ? `<div class="upload-done">✓ ${esc(block.result || "Uploaded")}</div>`
    : `<label class="upload-button"><input type="file" hidden data-upload="${esc(block.id)}"`
      + ` data-message="${esc(m.id)}" accept="${esc(block.accept)}">${esc(block.label)}</label>`,
};
const renderBlocks = (m) => (m.blocks || []).map((block) => (BLOCKS[block.type] || (() => ""))(block, m)).join("");

// A message just sent shows at once (the server is still routing it), with a thinking
// indicator until the reply's own card takes over.
function pendingTurns() {
  const html = [];
  for (const entry of state.pending) {
    if (entry.chatId !== state.chatId) continue;
    const saved = entry.messageId && state.messages.find((m) => m.id === entry.messageId);
    if (!saved) {
      html.push(`<div class="msg user pending"><div class="who">YOU</div><div class="bubble">${esc(entry.text)}</div></div>`);
    }
    if (!saved || !saved.linked_task_id) {
      html.push(`<div class="msg assistant"><div class="who">ASSISTANT</div><div class="bubble">`
        + `<span class="typing" role="status" aria-label="Thinking"><i></i><i></i><i></i></span></div></div>`);
    }
  }
  return html.join("");
}

function renderTranscript() {
  const byId = Object.fromEntries(state.tasks.map((t) => [t.id, t]));
  const atBottom = $("transcript").scrollHeight - $("transcript").scrollTop
    - $("transcript").clientHeight < 80;
  const messages = state.messages.map((m) => {
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
    // The reply as far as it has been written, while it is still being written.
    const draft = m.role === "user" && linked && active(linked) && linked.draft
      ? `<div class="msg assistant draft"><div class="who">ASSISTANT</div><div class="bubble">`
        + `${esc(linked.draft)} <span class="typing" role="status" aria-label="Still writing">`
        + `<i></i><i></i><i></i></span></div></div>` : "";
    return `<div class="msg ${m.role}"><div class="who">${who}</div>`
      + `<div class="bubble">${esc(m.content)}${extras}</div></div>${draft}`;
  }).join("") + pendingTurns() || `<div class="empty">No messages yet.</div>`;
  const earlier = state.nextBefore ? `<button type="button" class="quiet" data-load-earlier`
    + ` ${state.loadingEarlier ? "disabled" : ""}>${state.loadingEarlier ? "Loading…" : "Load earlier"}</button>` : "";
  $("transcript").innerHTML = earlier + messages;
  if (atBottom || state.pending.length) $("transcript").scrollTop = $("transcript").scrollHeight;
  window.avatarEvent?.("transcript", avatarTranscriptState());
}

// What the avatar shows from the chat: whether a reply is on its way, and the last reply.
function avatarTranscriptState() {
  const waiting = state.pending.some((entry) => entry.chatId === state.chatId)
    || state.tasks.some((task) => ["created", "planning", "executing", "validating"].includes(task.status)
      && !workCapabilities(task).length);
  const last = [...state.messages].reverse().find((m) => m.role === "assistant");
  return { waiting, last: last ? { id: last.id, emotion: last.emotion } : null };
}

// Sends what the user said. Returns false when it could not be sent, so the caller can put
// the text back in the box.
async function send(text, { voice = false } = {}) {
  state.busy = true; $("send").disabled = true;
  let entry = null;
  try {
    const attachments = phone.screen ? [{ kind: "screen", ...phone.screen }] : [];
    // Once you speak in a conversation, its replies are spoken from then on, also to typed
    // messages and tapped suggestions, until you mute or switch conversations.
    if (voice) phone.voiceChat = state.chatId;
    const spoken = voice || Boolean(phone.bridge && state.chatId && phone.voiceChat === state.chatId);
    // Every message goes to the server, which sees the whole conversation. The phone only
    // listens, speaks and runs the actions (timers, alarms, events) a reply proposes.
    if (!state.chatId) {
      state.chatId = (await api("/chat-sessions", { method: "POST", body: "{}" })).id;
      goChat(state.chatId);
      loadChats();
    }
    if (voice) phone.voiceChat = state.chatId;
    entry = { chatId: state.chatId, text, messageId: null };
    state.pending.push(entry);
    state.pollStarted = Date.now();
    renderTranscript();
    const posted = await api(`/chat-sessions/${state.chatId}/messages`, {
      method: "POST", body: JSON.stringify({ content: text, repository_id: state.repositoryId,
        local_time: localIsoTime(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        attachments, reply: true }),
    });
    entry.messageId = posted.message.id;
    if (attachments.length) setScreen(null);
    // A spoken question gets its answer spoken too, whenever it arrives.
    if (spoken) { phone.speakFor = posted.message.id; phone.spokenSentences = 0; }
    // The server decides what the message needs, and queues the chat reply in the same
    // request; the repository picker is only a hint. When it took care of the message at
    // once (a note, a choice picked), its reply is already in the transcript.
    const decision = posted.decision || { intent: "answer" };
    await refreshTranscript();
    if (decision.handled || decision.intent === "direct_action") {
      if (state.itemId) await renderWorkItem(state.itemId);
      await loadItems();
    }
    return true;
  } catch (e) { fail(e, { sticky: true }); return false; }
  finally {
    state.pending = state.pending.filter((item) => item !== entry);
    state.busy = false; $("send").disabled = false;
    if (entry) { state.transcriptSignature = null; renderTranscript(); }
  }
}

function localIsoTime() {
  // The user's wall-clock time, so "tomorrow at 9" resolves in their timezone.
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
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
