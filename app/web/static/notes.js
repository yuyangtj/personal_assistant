// Notes: a Keep-like pile of notes with tags and checklists, plus an import from Google Keep.

state.notes = [];
state.noteFilter = "";          // "" for all, a tag, or ARCHIVED
state.noteEditing = null;
state.noteDeleteArmed = null;
state.keepFile = null;
state.noteTagging = null;       // the note whose tags are being edited on its card
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

// Small line icons for the card's action bar (24×24 viewBox, drawn with currentColor).
const NOTE_ICONS = {
  pin: '<path d="M12 17v5"/><path d="M9 10.76V6h6v4.76l2 3.24H7z"/><path d="M8 2h8"/>',
  tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  unarchive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="m9 15 3-3 3 3"/><path d="M12 12v6"/>',
  delete: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/>',
};
const noteIcon = (name) => `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NOTE_ICONS[name]}</svg>`;
const noteTool = (action, label, icon, extra = "") =>
  `<button type="button" class="note-tool ${extra}" data-note-action="${action}" aria-label="${label}" title="${label}">${icon}</button>`;

function noteTagger(note) {
  return `<div class="note-tagger">${note.tags.map((tag) =>
    `<span class="note-tag">${esc(tag)}<button type="button" class="note-untag" data-note-untag="${esc(tag)}"
      aria-label="Remove tag ${esc(tag)}" title="Remove">×</button></span>`).join("")}
    <input class="note-tag-input" placeholder="Add a tag, then Enter" aria-label="Add a tag" maxlength="50"></div>`;
}

function noteCard(note) {
  const items = [...note.checklist.map((item, index) => ({ ...item, index }))]
    .sort((a, b) => Number(a.done) - Number(b.done));
  const shown = items.slice(0, 10);
  const list = items.length ? `<ul class="note-items">${shown.map((item) =>
    `<li class="${item.done ? "done" : ""}"><input type="checkbox" data-note-check="${item.index}"
      ${item.done ? "checked" : ""} aria-label="${esc(item.text)}"><span>${esc(item.text)}</span></li>`).join("")}
    ${items.length > shown.length ? `<li class="sub">+ ${items.length - shown.length} more</li>` : ""}</ul>` : "";
  const tagging = state.noteTagging === note.id;
  const armed = state.noteDeleteArmed === note.id;
  const tags = tagging ? noteTagger(note) : note.tags.length ? `<div class="note-meta">${note.tags.map((tag) =>
    `<button type="button" class="note-tag" data-note-filter="${esc(tag)}">${esc(tag)}</button>`).join("")}</div>` : "";
  return `<div class="note ${tagging ? "tagging" : ""}" data-note="${esc(note.id)}" tabindex="0"
      aria-label="Note ${esc(note.title || note.body.slice(0, 40))}" title="Changed ${esc(String(note.updated_at).slice(0, 10))}">
    ${noteTool("pin", note.pinned ? "Unpin" : "Pin", noteIcon("pin"), "note-pin" + (note.pinned ? " on" : ""))}
    ${note.title ? `<div class="note-title">${esc(note.title)}</div>` : ""}
    ${note.body ? `<div class="note-body">${esc(note.body)}</div>` : ""}
    ${list}
    ${tags}
    <div class="note-tools">
      ${noteTool("tag", tagging ? "Done with tags" : "Tags", noteIcon("tag"), tagging ? "on" : "")}
      ${noteTool("archive", note.archived ? "Unarchive" : "Archive", noteIcon(note.archived ? "unarchive" : "archive"))}
      ${armed ? `<button type="button" class="note-tool danger-armed" data-note-action="delete" aria-label="Tap again to delete">Delete?</button>`
        : noteTool("delete", "Delete", noteIcon("delete"))}
    </div></div>`;
}

function renderNotes() {
  if (state.noteFilter === ARCHIVED && !state.notes.some((note) => note.archived)) state.noteFilter = "";
  renderNoteTags();
  const notes = shownNotes();
  const empty = state.notes.length
    ? (state.noteFilter === ARCHIVED ? "No archived notes match." : "No note matches.")
    : "No notes yet. Add one above, or import your Google Keep notes.";
  const list = $("note-list");
  if (!notes.length) {
    list.innerHTML = `<div class="empty">${empty}</div>`;
  } else {
    // Keep-style masonry: cards go left to right, each into the shortest column.
    const count = noteColumnCount();
    list.innerHTML = Array.from({ length: count }, () => `<div class="note-col"></div>`).join("");
    const columns = [...list.children];
    for (const note of notes) {
      const shortest = columns.reduce((best, column) => (column.offsetHeight < best.offsetHeight ? column : best));
      shortest.insertAdjacentHTML("beforeend", noteCard(note));
    }
    state.noteColumns = count;
  }
  // A note being edited opens over the page, so it has room even on a phone.
  const editing = state.notes.find((note) => note.id === state.noteEditing);
  if (editing) list.insertAdjacentHTML("beforeend", `<div class="note-modal" data-note-backdrop>${noteEditor(editing)}</div>`);
}

function noteColumnCount() {
  const width = $("note-list").clientWidth || window.innerWidth;
  if (width < 300) return 1;
  if (window.matchMedia("(max-width: 720px)").matches) return 2;
  return Math.max(1, Math.min(8, Math.floor((width + 12) / (240 + 12))));
}

// Escape closes an open note without saving, wherever the focus is.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.noteEditing && !$("notes-view").hidden) {
    state.noteEditing = null;
    renderNotes();
  }
});

// Re-flow the columns when the window gets wider or narrower.
window.addEventListener("resize", () => {
  if (!$("notes-view").hidden && state.noteColumns && noteColumnCount() !== state.noteColumns) renderNotes();
});

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
    growNoteBody();
    renderNotes();
    $("note-body").focus();
  } catch (error) { noteError(error); }
};

// The composer is one slim line ("Take a note…") until it's used, as in Keep.
const composerEmpty = () => !["note-title", "note-body", "note-tags"].some((id) => $(id).value.trim());
function growNoteBody() {
  const body = $("note-body");
  body.style.height = "auto";
  body.style.height = Math.min(body.scrollHeight, 360) + "px";
}
$("note-form").addEventListener("focusin", () => $("note-form").classList.add("expanded"));
$("note-body").addEventListener("input", growNoteBody);
document.addEventListener("click", (event) => {
  if (!event.target.closest("#note-form") && composerEmpty()) $("note-form").classList.remove("expanded");
});
$("note-close").onclick = () => {
  for (const id of ["note-title", "note-body", "note-tags"]) $(id).value = "";
  $("note-as-list").checked = false;
  growNoteBody();
  $("note-form").classList.remove("expanded");
  document.activeElement.blur();
};

$("note-search").oninput = renderNotes;

$("note-tags-bar").onclick = (event) => {
  const chip = event.target.closest("[data-note-filter]");
  if (!chip) return;
  state.noteFilter = chip.dataset.noteFilter;
  state.noteEditing = null;
  renderNotes();
};

async function addTag(id, input) {
  const note = state.notes.find((item) => item.id === id);
  const added = splitTags(input.value);
  if (!added.length) return;
  await saveNote(id, { tags: [...note.tags, ...added] });
  renderNotes();
  const again = document.querySelector(`[data-note="${CSS.escape(id)}"] .note-tag-input`);
  if (again) again.focus();
}

$("note-list").onclick = async (event) => {
  if (event.target.dataset && "noteBackdrop" in event.target.dataset) {
    const editor = event.target.querySelector(".note.editing [data-note-action=save]");
    if (editor) editor.click();
    return;
  }
  const card = event.target.closest("[data-note]");
  if (!card) return;
  const id = card.dataset.note;
  const note = state.notes.find((item) => item.id === id);
  const tag = event.target.closest("[data-note-filter]");
  const untag = event.target.closest("[data-note-untag]");
  const check = event.target.dataset && event.target.dataset.noteCheck;
  const button = event.target.closest("[data-note-action]");
  const action = button && button.dataset.noteAction;
  try {
    if (untag) {
      const gone = untag.dataset.noteUntag.toLowerCase();
      await saveNote(id, { tags: note.tags.filter((item) => item.toLowerCase() !== gone) });
    } else if (event.target.closest(".note-tagger")) {
      return;  // typing a tag: don't open the note
    } else if (tag) {
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
    } else if (action === "tag") {
      const input = card.querySelector(".note-tag-input");
      if (input && input.value.trim()) await saveNote(id, { tags: [...note.tags, ...splitTags(input.value)] });
      state.noteTagging = state.noteTagging === id ? null : id;
      renderNotes();
      const opened = document.querySelector(`[data-note="${CSS.escape(id)}"] .note-tag-input`);
      if (opened) opened.focus();
      return;
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
      state.noteTagging = null;
      renderNotes();
      const title = document.querySelector(".note-modal .note-edit-title");
      if (title) title.focus();
      return;
    } else {
      return;
    }
    state.noteDeleteArmed = null;
    $("note-error").textContent = "";
    renderNotes();
  } catch (error) { noteError(error); }
};

$("note-list").onkeydown = (event) => {
  const tagInput = event.target.closest(".note-tag-input");
  if (tagInput) {
    const id = tagInput.closest("[data-note]").dataset.note;
    if (event.key === "Enter") { event.preventDefault(); addTag(id, tagInput).catch(noteError); }
    else if (event.key === "Escape") { state.noteTagging = null; renderNotes(); }
    return;
  }
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
    result.empty_skipped ? plural(result.empty_skipped, "empty note") : "",
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
