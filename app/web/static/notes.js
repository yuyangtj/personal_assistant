// Notes: a Keep-like pile of notes with tags and checklists, plus an import from Google Keep.

state.notes = [];
state.noteFilter = "";          // "" for all, a tag, or ARCHIVED
state.noteEditing = null;
state.noteDeleteArmed = null;
state.keepFile = null;
// No tag can start with "#" (the server strips it), so this never clashes with one.
const ARCHIVED = "#archived";

async function loadNotes() {
  try {
    state.notes = (await api("/notes?include_archived=true")).notes;
    $("note-error").textContent = "";
  } catch (error) { $("note-error").textContent = error.message || String(error); }
  renderNotes();
}

const noteError = (error) => { $("note-error").textContent = error.message || String(error); };
const splitTags = (text) => text.split(",").map((tag) => tag.trim()).filter(Boolean);
// The checklist as editable text: one item per line, "[x] " marks a done one.
const checklistText = (items) => items.map((item) => (item.done ? "[x] " : "[ ] ") + item.text).join("\n");
const parseChecklist = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean)
  .map((line) => {
    const match = line.match(/^\[( |x|X)?\]\s*(.*)$/);
    return match ? { text: match[2], done: (match[1] || "").toLowerCase() === "x" } : { text: line, done: false };
  }).filter((item) => item.text);

function noteMatches(note, query) {
  if (!query) return true;
  const text = [note.title, note.body, ...note.checklist.map((item) => item.text), ...note.tags].join(" ");
  return text.toLowerCase().includes(query);
}

function shownNotes() {
  const query = $("note-search").value.trim().toLowerCase();
  const archived = state.noteFilter === ARCHIVED;
  return state.notes.filter((note) => note.archived === archived
    && (archived || !state.noteFilter || note.tags.some((tag) => tag.toLowerCase() === state.noteFilter.toLowerCase()))
    && noteMatches(note, query))
    // Pinned first, then the most recently changed, as the server lists them.
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || String(b.updated_at).localeCompare(String(a.updated_at)));
}

function renderNoteTags() {
  const counts = new Map();
  for (const note of state.notes) {
    if (note.archived) continue;
    for (const tag of note.tags) {
      const key = tag.toLowerCase();
      const entry = counts.get(key) || { tag, count: 0 };
      entry.count += 1;
      counts.set(key, entry);
    }
  }
  const tags = [...counts.values()].sort((a, b) => a.tag.localeCompare(b.tag));
  const archivedCount = state.notes.filter((note) => note.archived).length;
  const chip = (value, label) => `<button type="button" data-note-filter="${esc(value)}"
    class="${state.noteFilter.toLowerCase() === value.toLowerCase() ? "active" : ""}">${label}</button>`;
  $("note-tags-bar").innerHTML = chip("", "All")
    + tags.map(({ tag, count }) => chip(tag, `${esc(tag)} <span class="sub">${count}</span>`)).join("")
    + (archivedCount ? chip(ARCHIVED, `Archived <span class="sub">${archivedCount}</span>`) : "");
}

function noteEditor(note) {
  const armed = state.noteDeleteArmed === note.id;
  return `<div class="note editing" data-note="${esc(note.id)}">
    <input class="note-edit-title" maxlength="300" placeholder="Title" value="${esc(note.title)}" aria-label="Title">
    <label class="sub">Text</label>
    <textarea class="note-edit-body" rows="6" maxlength="20000" aria-label="Text">${esc(note.body)}</textarea>
    <label class="sub">Checklist: one item per line, “[x] ” marks it done</label>
    <textarea class="note-edit-list" rows="${Math.min(12, note.checklist.length + 2)}" aria-label="Checklist">${esc(checklistText(note.checklist))}</textarea>
    <label class="sub">Tags</label>
    <input class="note-edit-tags" value="${esc(note.tags.join(", "))}" placeholder="Tags, separated by commas" aria-label="Tags">
    <div class="row wrap">
      <button type="button" class="quiet" data-note-action="pin">${note.pinned ? "Unpin" : "Pin"}</button>
      <button type="button" class="quiet" data-note-action="archive">${note.archived ? "Unarchive" : "Archive"}</button>
      <button type="button" class="quiet ${armed ? "danger-armed" : ""}" data-note-action="delete">${armed ? "Tap again to delete" : "Delete"}</button>
      <span class="grow"></span>
      <button type="button" class="quiet" data-note-action="cancel">Cancel</button>
      <button type="button" data-note-action="save">Save</button></div></div>`;
}

function noteCard(note) {
  if (state.noteEditing === note.id) return noteEditor(note);
  const items = [...note.checklist.map((item, index) => ({ ...item, index }))]
    .sort((a, b) => Number(a.done) - Number(b.done));
  const shown = items.slice(0, 10);
  const list = items.length ? `<ul class="note-items">${shown.map((item) =>
    `<li class="${item.done ? "done" : ""}"><input type="checkbox" data-note-check="${item.index}"
      ${item.done ? "checked" : ""} aria-label="${esc(item.text)}"><span>${esc(item.text)}</span></li>`).join("")}
    ${items.length > shown.length ? `<li class="sub">+ ${items.length - shown.length} more</li>` : ""}</ul>` : "";
  return `<div class="note" data-note="${esc(note.id)}" tabindex="0" role="button" aria-label="Edit note ${esc(note.title)}">
    ${note.title ? `<div class="note-title">${note.pinned ? "📌 " : ""}${esc(note.title)}</div>` : ""}
    ${note.body ? `<div class="note-body">${!note.title && note.pinned ? "📌 " : ""}${esc(note.body)}</div>` : ""}
    ${list}
    <div class="note-meta">${note.tags.map((tag) =>
      `<button type="button" class="note-tag" data-note-filter="${esc(tag)}">${esc(tag)}</button>`).join("")}
      <span class="sub">${esc(String(note.updated_at).slice(0, 10))}</span></div></div>`;
}

function renderNotes() {
  if (state.noteFilter === ARCHIVED && !state.notes.some((note) => note.archived)) state.noteFilter = "";
  renderNoteTags();
  const notes = shownNotes();
  const empty = state.notes.length
    ? (state.noteFilter === ARCHIVED ? "No archived notes match." : "No note matches.")
    : "No notes yet. Add one above, or import your Google Keep notes.";
  $("note-list").innerHTML = notes.length ? notes.map(noteCard).join("") : `<div class="empty">${empty}</div>`;
}

async function saveNote(id, fields) {
  const note = await api(`/notes/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
  state.notes = state.notes.map((item) => (item.id === id ? note : item));
}

$("note-form").onsubmit = async (event) => {
  event.preventDefault();
  const text = $("note-body").value;
  const asList = $("note-as-list").checked;
  try {
    const note = await api("/notes", { method: "POST", body: JSON.stringify({
      title: $("note-title").value,
      body: asList ? "" : text,
      checklist: asList ? parseChecklist(text) : [],
      tags: [...splitTags($("note-tags").value),
        ...(state.noteFilter && state.noteFilter !== ARCHIVED ? [state.noteFilter] : [])],
    }) });
    state.notes = [note, ...state.notes];
    for (const id of ["note-title", "note-body", "note-tags"]) $(id).value = "";
    $("note-as-list").checked = false;
    $("note-error").textContent = "";
    renderNotes();
  } catch (error) { noteError(error); }
};

$("note-search").oninput = renderNotes;

$("note-tags-bar").onclick = (event) => {
  const chip = event.target.closest("[data-note-filter]");
  if (!chip) return;
  state.noteFilter = chip.dataset.noteFilter;
  state.noteEditing = null;
  renderNotes();
};

$("note-list").onclick = async (event) => {
  const card = event.target.closest("[data-note]");
  if (!card) return;
  const id = card.dataset.note;
  const note = state.notes.find((item) => item.id === id);
  const tag = event.target.closest("[data-note-filter]");
  const check = event.target.dataset && event.target.dataset.noteCheck;
  const action = event.target.dataset && event.target.dataset.noteAction;
  try {
    if (tag) {
      state.noteFilter = tag.dataset.noteFilter;
    } else if (check !== undefined) {
      const checklist = note.checklist.map((item, index) =>
        (index === Number(check) ? { ...item, done: event.target.checked } : item));
      await saveNote(id, { checklist });
    } else if (action === "save") {
      await saveNote(id, {
        title: card.querySelector(".note-edit-title").value,
        body: card.querySelector(".note-edit-body").value,
        checklist: parseChecklist(card.querySelector(".note-edit-list").value),
        tags: splitTags(card.querySelector(".note-edit-tags").value),
      });
      state.noteEditing = null;
    } else if (action === "cancel") {
      state.noteEditing = null;
    } else if (action === "pin") {
      await saveNote(id, { pinned: !note.pinned });
    } else if (action === "archive") {
      await saveNote(id, { archived: !note.archived });
      state.noteEditing = null;
    } else if (action === "delete") {
      if (state.noteDeleteArmed !== id) { state.noteDeleteArmed = id; renderNotes(); return; }
      await api(`/notes/${id}`, { method: "DELETE" });
      state.notes = state.notes.filter((item) => item.id !== id);
      state.noteEditing = null;
    } else if (!card.classList.contains("editing")) {
      state.noteEditing = id;
    } else {
      return;
    }
    state.noteDeleteArmed = null;
    $("note-error").textContent = "";
    renderNotes();
  } catch (error) { noteError(error); }
};

$("note-list").onkeydown = (event) => {
  const card = event.target.closest(".note:not(.editing)");
  if (card && event.target === card && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    state.noteEditing = card.dataset.note;
    renderNotes();
  }
};

// Import from Google Keep: the Takeout .zip goes to the server as is. A dry run shows
// what it found first; images and trashed notes are skipped.
async function sendKeepExport(dryRun) {
  const res = await fetch(`/notes/import/google-keep?dry_run=${dryRun}`, {
    method: "POST", headers: { "content-type": "application/zip" }, body: state.keepFile,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

const plural = (count, noun) => `${count} ${noun}${count === 1 ? "" : "s"}`;
function keepSummary(result) {
  const skipped = [
    result.images_skipped ? plural(result.images_skipped, "image") : "",
    result.trashed_skipped ? plural(result.trashed_skipped, "trashed note") : "",
    result.unreadable ? plural(result.unreadable, "unreadable file") : "",
  ].filter(Boolean);
  const here = result.already_here ? `, ${result.already_here} already here` : "";
  return `${plural(result.added, result.dry_run ? "new note" : "note")}${here}`
    + (skipped.length ? ` (skipping ${skipped.join(", ")})` : "");
}

function showKeepImport(html) {
  $("keep-import").innerHTML = html;
  $("keep-import").hidden = !html;
}

$("keep-file").onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  state.keepFile = file;
  showKeepImport(`<div class="sub" style="margin:0">Reading ${esc(file.name)}…</div>`);
  try {
    const result = await sendKeepExport(true);
    showKeepImport(`<div>Found ${esc(keepSummary(result))}.</div>
      <div class="row"><button type="button" data-keep="cancel" class="quiet">Cancel</button>
      <button type="button" data-keep="import" ${result.added ? "" : "disabled"}>Import ${esc(plural(result.added, "note"))}</button></div>`);
  } catch (error) { state.keepFile = null; showKeepImport(`<div class="err">${esc(error.message)}</div>`); }
};

$("keep-import").onclick = async (event) => {
  const choice = event.target.dataset && event.target.dataset.keep;
  if (!choice) return;
  if (choice === "cancel" || !state.keepFile) { state.keepFile = null; showKeepImport(""); return; }
  event.target.disabled = true;
  event.target.textContent = "Importing…";
  try {
    const result = await sendKeepExport(false);
    state.keepFile = null;
    showKeepImport(`<div>Imported ${esc(keepSummary(result))}.</div>
      <div class="row"><button type="button" data-keep="cancel" class="quiet">Close</button></div>`);
    await loadNotes();
  } catch (error) { showKeepImport(`<div class="err">${esc(error.message)}</div>`); }
};
