// Clicks, forms and buttons wired to the functions above, plus the theme toggle.

// A file picked with an upload button in a reply.
const MAX_UPLOAD_BYTES = 5_000_000;
document.addEventListener("change", async (e) => {
  const input = e.target.closest && e.target.closest("[data-upload]");
  if (!input || !input.files.length || !state.chatId) return;
  const file = input.files[0];
  const button = input.closest(".upload-button");
  if (file.size > MAX_UPLOAD_BYTES) { fail(new Error("That file is too large (5 MB at most).")); return; }
  button.classList.add("busy");
  button.lastChild.textContent = "Uploading…";
  try {
    await api(`/chat-sessions/${state.chatId}/messages/${input.dataset.message}/uploads/${input.dataset.upload}`, {
      method: "POST", body: JSON.stringify({ filename: file.name, content: await file.text() }),
    });
    clearError();
  } catch (error) { fail(error); }
  await refreshTranscript();
});

document.addEventListener("click", (e) => {
  const menu = e.target.closest("[data-chat-menu]");
  if (menu) {
    const id = menu.dataset.chatMenu;
    state.chatMenu = state.chatMenu === id ? null : id;
    state.chatDeleteArmed = null;
    return renderChats();
  }
  const chatAct = e.target.closest("[data-chat-action]");
  if (chatAct) return chatAction(chatAct.dataset.chatAction, chatAct.dataset.id).catch(fail);
  if (e.target.closest("[data-chat-archived]")) {
    state.showArchived = !state.showArchived;
    return loadChats();
  }
  const chat = e.target.closest("[data-chat]");
  if (chat) return goChat(chat.dataset.chat);
  const taskNav = e.target.closest("[data-task-nav]");
  if (taskNav) return goTask(taskNav.dataset.taskNav);
  const task = e.target.closest("[data-task]");
  if (task) return goTask(task.dataset.task);
  const item = e.target.closest("[data-item]");
  if (item) return goItem(item.dataset.item);
  const unfocus = e.target.closest("[data-unfocus]");
  if (unfocus) {
    return api(`/chat-sessions/${state.chatId}/focus/${unfocus.dataset.unfocus}`, { method: "DELETE" })
      .then((body) => { state.focus = body.work_items; renderFocusBar(); }).catch(fail);
  }
  const discuss = e.target.closest("[data-discuss]");
  if (discuss && state.itemId) {
    return api(`/work-items/${state.itemId}/discuss`, { method: "POST",
      body: JSON.stringify({ about: discuss.dataset.discuss || null }) })
      .then((chat) => goChat(chat.id)).catch(fail);
  }
  const cancelSchedule = e.target.closest("[data-cancel-schedule]");
  if (cancelSchedule && state.itemId) {
    return api(`/schedules/${cancelSchedule.dataset.cancelSchedule}`, { method: "DELETE" })
      .then(() => renderWorkItem(state.itemId)).catch(fail);
  }
  const messageAction = e.target.closest("[data-message-action]");
  if (messageAction) return runMessageAction(messageAction.dataset.messageAction, messageAction.dataset.message);
  const messageCancel = e.target.closest("[data-message-cancel]");
  if (messageCancel) return cancelMessageAction(messageCancel.dataset.messageCancel, messageCancel.dataset.message);
  const removeEntry = e.target.closest("[data-remove-entry]");
  if (removeEntry && state.itemId) {
    return api(`/work-items/${state.itemId}/checklist/${removeEntry.dataset.removeEntry}`, { method: "DELETE" })
      .then(() => renderWorkItem(state.itemId)).catch(fail);
  }
  const workflow = e.target.closest("[data-workflow]");
  if (workflow) return goWorkflow(workflow.dataset.workflow);
  const choice = e.target.closest("[data-choice]");
  if (choice && !choice.disabled && !state.busy) {
    // Picking a choice is just saying it: the label goes in as your next message.
    clearError();
    return send(choice.dataset.choice);
  }

});
$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.busy) return;
  const voice = phone.dictated;
  phone.dictated = false;
  $("input").value = ""; clearError();
  // A message that could not be sent goes back in the box, unless something new was typed.
  if (await send(text, { voice }) === false && !$("input").value) {
    $("input").value = text;
    $("input").focus();
  }
};
// Clearing the box drops dictation (replies are still spoken in a conversation you've spoken in).
$("input").addEventListener("input", () => { if (!$("input").value.trim()) phone.dictated = false; });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("new-chat").onclick = async () => {
  try { const c = await api("/chat-sessions", { method: "POST", body: "{}" }); goChat(c.id); }
  catch (e) { fail(e); }
};

// The message box starts one line tall and grows with what's in it, up to about six lines,
// also when dictation or a share fills it.
const INPUT_MAX_PX = 160;
function growInput() {
  const box = $("input");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, INPUT_MAX_PX)}px`;
  box.style.overflowY = box.scrollHeight > INPUT_MAX_PX ? "auto" : "hidden";
}
{
  const box = $("input");
  const native = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
  Object.defineProperty(box, "value", {
    get() { return native.get.call(this); },
    set(value) { native.set.call(this, value); growInput(); },
  });
  box.addEventListener("input", growInput);
}

// More: Stats and the settings, in a small sheet above the tab bar (or beside the rail).
function setMore(open) {
  $("more-sheet").hidden = !open;
  $("more-open").setAttribute("aria-expanded", String(open));
}
$("more-open").onclick = (e) => { e.stopPropagation(); setMore($("more-sheet").hidden); };
$("nav-stats").addEventListener("click", () => setMore(false));
$("passkeys-open").addEventListener("click", () => setMore(false));
document.addEventListener("click", (e) => {
  if (!$("more-sheet").hidden && !e.target.closest("#more-sheet, #more-open")) setMore(false);
  // The conversation menu closes like a popover too.
  for (const menu of document.querySelectorAll("details.chat-menu[open]")) {
    if (!menu.contains(e.target)) menu.open = false;
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMore(false); });
document.addEventListener("change", (e) => {
  const check = e.target.closest("[data-check]");
  if (!check || !state.itemId) return;
  api(`/work-items/${state.itemId}/checklist/${check.dataset.check}`, { method: "PATCH",
    body: JSON.stringify({ done: check.checked }) }).then(() => renderWorkItem(state.itemId)).catch(fail);
});
$("new-item-form").onsubmit = async (event) => {
  event.preventDefault();
  const title = $("new-item-title").value.trim();
  if (!title) return;
  try {
    // Created while a chat is open, the item is focused on that chat.
    const item = await api("/work-items", { method: "POST", body: JSON.stringify({
      title, space: $("new-item-space").value, chat_session_id: state.chatId || null }) });
    $("new-item-title").value = "";
    await loadItems();
    if (state.chatId) await refreshTranscript();
    goItem(item.slug);
  } catch (e) { fail(e); }
};
$("repository").onchange = (event) => {
  state.repositoryId = event.target.value || null;
};
$("close-task").onclick = closeDrawer;
$("detail-scrim").onclick = closeDrawer;
$("cancel-action").onclick = closeActionDialog;
$("action-dialog").addEventListener("close", () => { state.pendingAction = null; });
// The one request each approval makes; it alone carries the token or passkey signature.
function approvalRequest(pending, instructions) {
  const sha = pending.approval.expected_head_sha;
  switch (pending.mode) {
    case "workflow":
      return { path: `/workflow-runs/${pending.taskId}/decision`, body: { decision: "approve" } };
    case "deployment":
      return { path: `/workflow-runs/${pending.approval.id}/decision`, body: { decision: "approve" } };
    case "ready":
      return { path: `/tasks/${pending.taskId}/pull-request-ready`, body: { expected_head_sha: sha } };
    case "revision":
      return { path: `/tasks/${pending.taskId}/pull-request-revision`, body: { instructions, expected_head_sha: sha } };
    default:
      return { path: `/tasks/${pending.taskId}/pull-request-approval`,
        body: { decision: "approve", expected_head_sha: sha, merge_method: "squash" } };
  }
}
$("action-form").onsubmit = async (event) => {
  event.preventDefault();
  const pending = state.pendingAction;
  if (!pending) return;
  const instructions = $("revision-instructions").value.trim();
  const button = $("confirm-action");
  button.disabled = true;
  $("action-error").textContent = "";
  try {
    const request = approvalRequest(pending, instructions);
    const headers = { "content-type": "application/json", ...(await approvalAuth("POST", request.path)) };
    const result = await api(request.path, { method: "POST", headers, body: JSON.stringify(request.body) });
    if (pending.mode === "deployment") {
      await api(`/workflow-runs/${pending.approval.id}/start`, { method: "POST", body: "{}" });
    }
    closeActionDialog();
    if (pending.mode === "workflow") {
      await startCodingWorkflow(pending.taskId);
    } else if (pending.mode === "deployment") {
      await renderTask(pending.taskId);
    } else if (pending.mode === "revision") {
      await refreshTranscript();
      goTask(result.id);
    } else {
      if (pending.mode === "merge") await refreshTranscript();
      await renderTask(pending.taskId);
    }
  } catch (error) {
    $("action-error").textContent = error.message || String(error);
  } finally {
    button.disabled = false;
  }
};
closeTaskPanel();

const THEME_STORAGE_KEY = "assistant.theme";
const theme = {
  stored: null,
  current() {
    if (this.stored === "light" || this.stored === "dark") return this.stored;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light" : "dark";
  },
  apply(value) {
    document.documentElement.setAttribute("data-theme", value);
    const btn = $("theme-toggle");
    if (btn) btn.setAttribute("aria-pressed", value === "light" ? "true" : "false");
  },
  toggle() {
    const next = this.current() === "light" ? "dark" : "light";
    this.stored = next;
    try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch (_) {}
    this.apply(next);
  },
  init() {
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY);
      if (saved === "light" || saved === "dark") this.stored = saved;
    } catch (_) {}
    this.apply(this.current());
  },
};
theme.init();
$("theme-toggle").onclick = () => theme.toggle();

// The avatar: on by default in Assistant, off elsewhere unless switched on; each choice is
// remembered on this device. Its script loads only when it is first shown.
const AVATAR_STORAGE_KEY = "assistantAvatar";
const AVATAR_HOME_KEY = "assistantAvatarHome";
const inHome = () => Boolean(state.chatId && state.chatId === state.homeId);
function avatarWanted() {
  try {
    if (inHome()) return localStorage.getItem(AVATAR_HOME_KEY) !== "0";
    return localStorage.getItem(AVATAR_STORAGE_KEY) === "1";
  } catch (_) { return inHome(); }
}
// Called whenever another chat opens: the face follows that chat's setting.
function applyAvatarForChat() {
  const shown = $("avatar-toggle").getAttribute("aria-pressed") === "true";
  if (avatarWanted() !== shown) setAvatar(!shown, { remember: false });
}
function loadAvatar() {
  if (window.avatar) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/ui/static/avatar.js";
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}
async function setAvatar(on, { remember = true } = {}) {
  if (remember) {
    try { localStorage.setItem(inHome() ? AVATAR_HOME_KEY : AVATAR_STORAGE_KEY, on ? "1" : "0"); } catch (_) {}
  }
  const button = $("avatar-toggle");
  button.setAttribute("aria-pressed", String(on));
  button.setAttribute("aria-label", on ? "Hide the avatar" : "Show the avatar");
  const stage = $("avatar-stage");
  if (!on) {
    if (window.avatar) window.avatar.unmount();
    stage.hidden = true;
    return;
  }
  stage.hidden = false;
  try { await loadAvatar(); } catch (_) {
    stage.hidden = true;
    fail(new Error("The avatar couldn't load. Try again."));
    return;
  }
  window.avatar.mount(stage);
  window.avatarEvent("transcript", avatarTranscriptState());
  if (phone.listening) window.avatarEvent("listening", { on: true });
}
// The switch is in a conversation's ⋯ menu, so the face appears right where it was turned on.
$("avatar-toggle").onclick = () => setAvatar($("avatar-toggle").getAttribute("aria-pressed") !== "true");
applyAvatarForChat();
