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
  const chatDelete = e.target.closest("[data-chat-delete]");
  if (chatDelete) {
    const id = chatDelete.dataset.chatDelete;
    const chat = state.chats.find((c) => c.id === id);
    const title = chat ? `"${chat.title}"` : "this conversation";
    if (!window.confirm(`Delete ${title}? The tasks it started are kept.`)) return;
    return api(`/chat-sessions/${id}`, { method: "DELETE" })
      .then(async () => {
        if (state.chatId === id) {
          // The open conversation is gone: pick another one, or show the empty state.
          const next = state.chats.find((c) => c.id !== id);
          if (next) goChat(next.id);
          else go({ section: "chats" });
        }
        await loadChats();
      })
      .catch(fail);
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
  const phoneAction = e.target.closest("[data-phone-action]");
  if (phoneAction) return runPhoneAction(phoneAction.dataset.phoneAction);
  const phoneCancel = e.target.closest("[data-phone-cancel]");
  if (phoneCancel) return cancelPhoneAction(phoneCancel.dataset.phoneCancel);
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
    return send(choice.dataset.choice, { local: false });
  }

});
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.busy) return;
  const voice = phone.dictated;
  phone.dictated = false;
  $("input").value = ""; clearError(); send(text, { voice });
};
// Clearing the box drops dictation: a typed message gets a written reply only.
$("input").addEventListener("input", () => { if (!$("input").value.trim()) phone.dictated = false; });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("new-chat").onclick = async () => {
  try { const c = await api("/chat-sessions", { method: "POST", body: "{}" }); goChat(c.id); }
  catch (e) { fail(e); }
};
$("reload-chats").onclick = loadChats;
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
