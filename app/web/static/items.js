// Work items: the list, a work item's page, and the chat's focus bar.

const ITEM_STATUSES = ["open", "active", "blocked", "done", "archived"];

async function loadSpaces() {
  try {
    const spaces = (await api("/spaces")).spaces;
    $("new-item-space").innerHTML = spaces.map((space) =>
      `<option value="${esc(space.slug)}" ${space.slug === "general" ? "selected" : ""}>${esc(space.name)}</option>`).join("");
  } catch (e) { fail(e); }
}

async function loadItems() {
  try {
    state.items = (await api("/work-items")).work_items;
    const spaces = [...new Set(state.items.map((item) => item.space))].sort();
    $("item-list").innerHTML = spaces.map((space) => `<div class="space-head">${esc(space)}</div>`
      + state.items.filter((item) => item.space === space).map((item) => `
        <button class="chat-item ${[item.id, item.slug].includes(state.itemRef) ? "active" : ""}" data-item="${esc(item.slug)}" type="button">
          <div>${esc(item.title)}</div>
          <div class="when">#${esc(item.slug)} · ${esc(label(item.status))}</div>
        </button>`).join("")).join("") || `<div class="sub" style="margin-top:12px">No work items yet.</div>`;
    renderFocusBar();
  } catch (e) { fail(e); }
}

function renderFocusBar() {
  if (!state.chatId) { $("focus-bar").innerHTML = ""; return; }
  const focused = new Set(state.focus.map((item) => item.id));
  const candidates = state.items.filter((item) => !focused.has(item.id) && item.status !== "done");
  $("focus-bar").innerHTML = state.focus.map((item) => `
    <span class="chip"><button type="button" class="link-button" data-item="${esc(item.slug)}"
      title="${esc(item.title)}">#${esc(item.slug)}</button><button type="button" class="chip-x"
      data-unfocus="${esc(item.id)}" aria-label="Stop focusing on #${esc(item.slug)}">×</button></span>`).join("")
    + (candidates.length ? `<select id="focus-add" aria-label="Focus this chat on a work item">
        <option value="">+ Work item</option>${candidates.map((item) =>
          `<option value="${esc(item.id)}">#${esc(item.slug)}</option>`).join("")}</select>` : "");
  const add = $("focus-add");
  if (add) add.onchange = async () => {
    if (!add.value) return;
    try {
      state.focus = (await api(`/chat-sessions/${state.chatId}/focus/${add.value}`, { method: "PUT" })).work_items;
      renderFocusBar();
    } catch (e) { fail(e); }
  };
}

function briefSection(title, value) {
  if (Array.isArray(value)) {
    return `<div class="brief-label">${title}</div>${value.length
      ? `<ul class="brief-list">${value.map((entry) => `<li>${esc(entry)}</li>`).join("")}</ul>`
      : `<div class="brief-text sub">Nothing yet</div>`}`;
  }
  return `<div class="brief-label">${title}</div><div class="brief-text">${esc(value || "—")}</div>`;
}

async function renderWorkItem(id) {
  state.itemId = id;
  try {
    const [item, runs, timeline, schedules] = await Promise.all([
      api(`/work-items/${id}`), api(`/work-items/${id}/runs`), api(`/work-items/${id}/timeline?limit=50`),
      api(`/schedules?work_item_id=${encodeURIComponent(id)}`),
    ]);
    if (state.itemId !== id) return;
    const brief = item.brief || {};
    state.itemId = item.id;
    $("item-page").innerHTML = `
      <div class="row wrap"><a class="back mobile-only" href="#/items">‹ Work items</a>${statusPill(item.status)}<span class="badge">#${esc(item.slug)}</span>
        <span class="sub" style="margin:0">${esc(item.space)} · ${esc(item.kind)}</span></div>
      <h1>${esc(item.title)}</h1>
      <div class="row wrap"><label class="sub" for="item-status" style="margin:0">Status</label>
        <select id="item-status">${ITEM_STATUSES.map((value) =>
          `<option value="${value}" ${value === item.status ? "selected" : ""}>${label(value)}</option>`).join("")}</select>
        <button type="button" class="ghost" data-discuss="">Discuss in a new chat</button></div>
      <div class="panel-card"><h3>Brief</h3>
        ${briefSection("Goal", brief.goal)}${briefSection("Where it stands", brief.status_summary)}
        ${briefSection("Decisions", brief.decisions || [])}${briefSection("Next steps", brief.next_steps || [])}
        ${briefSection("Open questions", brief.open_questions || [])}
        <div class="sub">Updated automatically after a chat goes quiet or a run finishes.</div></div>
      <div class="panel-card"><h3>Checklist</h3>
        ${(item.checklist || []).map((entry) => `<div class="item-check ${entry.done ? "done" : ""}">
          <input type="checkbox" data-check="${esc(entry.id)}" ${entry.done ? "checked" : ""} aria-label="Done: ${esc(entry.text)}">
          <span>${esc(entry.text)}</span>
          <button type="button" class="chip-x ghost" data-remove-entry="${esc(entry.id)}" aria-label="Remove ${esc(entry.text)}">×</button></div>`).join("")
          || `<div class="sub">No entries.</div>`}
        <form id="checklist-form" style="margin-top:6px"><textarea id="checklist-text" rows="1" maxlength="300"
          placeholder="Add an entry…" aria-label="New checklist entry"></textarea><button type="submit">Add</button></form></div>
      ${(item.links || []).length ? `<div class="panel-card"><h3>Links</h3>${item.links.map((link) => link.url
        ? `<div><a href="${esc(link.url)}" target="_blank" rel="noreferrer">${esc(link.label)}</a></div>`
        : `<div class="sub" style="margin:0">${esc(link.kind)}: ${esc(link.label)}</div>`).join("")}</div>` : ""}
      <div class="panel-card"><h3>Schedules</h3>${schedules.schedules.map((schedule) => `
        <div class="item-check"><span>⏰</span><span>${esc(schedule.message)}
          <div class="sub" style="margin:0">${esc(schedule.kind)} · next ${esc(new Date(schedule.next_run_at).toLocaleString())}${schedule.recurrence !== "none" ? ` · ${esc(schedule.recurrence)}` : ""}</div></span>
          <button type="button" class="chip-x ghost" data-cancel-schedule="${esc(schedule.id)}" aria-label="Cancel ${esc(schedule.message)}">×</button></div>`).join("")
        || `<div class="sub">None. Say “remind me tomorrow at 9 to …” in a chat about this item.</div>`}</div>
      <div class="panel-card"><h3>Runs</h3>${runs.tasks.map((task) => `
        <button class="taskcard" data-task="${esc(task.id)}" type="button">
          <span class="dot ${active(task) ? "run" : task.status === "failed" ? "fail" : ""}"></span>
          <span class="grow">${esc(task.original_request.slice(0, 90))}</span><span>${esc(label(task.status))}</span></button>`).join("")
        || `<div class="sub">No runs yet.</div>`}</div>
      <div class="panel-card"><h3>Timeline</h3>${timeline.entries.map((entry) => `
        <div class="ev timeline-entry"><div>${esc(entry.summary)}<div class="sub" style="margin:0">${esc(new Date(entry.at).toLocaleString())}</div></div>
          <button type="button" class="ghost" data-discuss="${esc(entry.summary)}" aria-label="Discuss: ${esc(entry.summary)}">Discuss</button></div>`).join("")
        || `<div class="sub">Nothing yet.</div>`}</div>`;
    $("item-status").onchange = async (event) => {
      try {
        await api(`/work-items/${item.id}`, { method: "PATCH",
          body: JSON.stringify({ version: item.version, status: event.target.value }) });
        await Promise.all([renderWorkItem(item.id), loadItems()]);
      } catch (e) { fail(e); await renderWorkItem(item.id); }
    };
    $("checklist-form").onsubmit = async (event) => {
      event.preventDefault();
      const text = $("checklist-text").value.trim();
      if (!text) return;
      try {
        await api(`/work-items/${item.id}/checklist`, { method: "POST", body: JSON.stringify({ text }) });
        await renderWorkItem(item.id);
      } catch (e) { fail(e); }
    };
    clearError();
  } catch (e) { fail(e); }
}
