// Notes: a Keep-like pile of notes with tags and checklists, plus an import from Google Keep.

state.notes = [];
state.noteFilter = "";          // "" for all, a tag, or ARCHIVED
// A big collection is drawn a screenful at a time: this many cards, then more on scroll.
const NOTE_BATCH = 48;
state.noteLimit = NOTE_BATCH;
state.noteRendered = 0;
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
  state.noteLimit = NOTE_BATCH;  // coming back to the tab starts at the top
  renderNotes();
}

const noteError = (error) => { $("note-error").textContent = error.message || String(error); };
const splitTags = (text) => text.split(",").map((tag) => tag.trim()).filter(Boolean);
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

// A slim row, not every tag: All, the most-used tags, "All tags" (a searchable list) and Archived.
const TOP_TAGS = 5;
state.tagPanelOpen = false;

function noteTagCounts(includeArchived = false) {
  const counts = new Map();
  for (const note of state.notes) {
    if (note.archived && !includeArchived) continue;
    for (const tag of note.tags) {
      const key = tag.toLowerCase();
      const entry = counts.get(key) || { tag, count: 0 };
      entry.count += 1;
      counts.set(key, entry);
    }
  }
  return [...counts.values()];
}

const tagChip = (value, label, extra = "") => `<button type="button" data-note-filter="${esc(value)}" title="${esc(value)}"
  class="${extra} ${state.noteFilter.toLowerCase() === value.toLowerCase() ? "active" : ""}">${label}</button>`;
// A long tag name is cut with "…" (the full name is the tooltip); the count always shows.
const tagLabel = ({ tag, count }) => `<span class="tag-name">${esc(tag)}</span> <span class="sub">${count}</span>`;

function renderNoteTags() {
  const all = noteTagCounts();
  const byCount = [...all].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
  const top = byCount.slice(0, TOP_TAGS);
  const active = state.noteFilter && state.noteFilter !== ARCHIVED
    ? all.find((entry) => entry.tag.toLowerCase() === state.noteFilter.toLowerCase()) : null;
  const shown = active && !top.includes(active) ? [...top, active] : top;
  const archivedCount = state.notes.filter((note) => note.archived).length;
  $("note-tags-bar").innerHTML = tagChip("", "All")
    + shown.map((entry) => tagChip(entry.tag, tagLabel(entry))).join("")
    + (all.length > shown.length
      ? `<button type="button" class="note-tags-more" data-note-tags-open aria-expanded="${state.tagPanelOpen}">All tags (${all.length}) ▾</button>` : "")
    + (archivedCount ? tagChip(ARCHIVED, `Archived <span class="sub">${archivedCount}</span>`) : "");
  if (state.tagPanelOpen) renderTagPanel(all);
}

function renderTagPanel(all = noteTagCounts()) {
  const query = $("note-tag-filter").value.trim().toLowerCase();
  const list = all.filter(({ tag }) => !query || tag.toLowerCase().includes(query))
    .sort((a, b) => a.tag.localeCompare(b.tag, undefined, { sensitivity: "base" }));
  $("note-tag-list").innerHTML = list.length ? list.map((entry) => tagChip(entry.tag, tagLabel(entry))).join("")
    : `<div class="sub">No tag matches.</div>`;
  $("note-tag-panel").hidden = false;
}

function setTagPanel(open) {
  state.tagPanelOpen = open;
  if (open) {
    $("note-tag-filter").value = "";
    renderNoteTags();
    $("note-tag-filter").focus();
  } else {
    $("note-tag-panel").hidden = true;
    for (const button of document.querySelectorAll("[data-note-tags-open]")) button.setAttribute("aria-expanded", "false");
  }
}

// --- The editor: one simple pop-up. Title, text or checklist rows, tags, a few icons and
// Done. Everything is saved when it closes (Done, Escape or a click outside), and only if
// something changed, so just looking at a note leaves its edited date alone.
function openEditor(note) {
  state.noteEditing = note.id;
  state.noteTagging = null;
  state.noteDeleteArmed = null;
  state.noteDraft = {
    title: note.title, body: note.body, items: note.checklist.map((item) => ({ ...item })),
    tags: [...note.tags], pinned: note.pinned, listMode: note.checklist.length > 0, error: "",
  };
}

// Copy what has been typed into the draft (the inputs are the source until then).
function syncDraft(card) {
  const draft = state.noteDraft;
  if (!draft || !card) return draft;
  draft.title = card.querySelector(".note-edit-title").value;
  const body = card.querySelector(".note-edit-body");
  if (body) draft.body = body.value;
  if (card.querySelector(".note-edit-items")) {
    draft.items = [...card.querySelectorAll(".note-edit-item")].map((row) => ({
      text: row.querySelector(".note-edit-text").value, done: row.querySelector(".note-edit-check").checked }));
  }
  return draft;
}

function draftTagger(draft) {
  return `<div class="note-tagger">${draft.tags.map((tag) =>
    `<span class="note-tag">${esc(tag)}<button type="button" class="note-untag" data-draft-untag="${esc(tag)}"
      aria-label="Remove tag ${esc(tag)}" title="Remove">×</button></span>`).join("")}
    <input class="note-tag-input" placeholder="Add a tag" aria-label="Add a tag" maxlength="50" autocomplete="off"></div>`;
}

function noteEditor(note) {
  const draft = state.noteDraft;
  const armed = state.noteDeleteArmed === note.id;
  const showBody = !draft.listMode || draft.body.trim();
  const items = draft.listMode ? `<div class="note-edit-items">${draft.items.map((item, index) =>
    `<div class="note-edit-item"><input type="checkbox" class="note-edit-check" ${item.done ? "checked" : ""} aria-label="Done">
      <input class="note-edit-text ${item.done ? "done" : ""}" maxlength="500" value="${esc(item.text)}" placeholder="List item" aria-label="List item">
      <button type="button" class="note-untag" data-note-remove-item="${index}" aria-label="Remove item" title="Remove">×</button></div>`).join("")}
    <button type="button" class="note-add-item" data-note-action="add-item">+ List item</button></div>` : "";
  return `<div class="note editing" data-note="${esc(note.id)}">
    <input class="note-edit-title" maxlength="300" placeholder="Title" value="${esc(draft.title)}" aria-label="Title">
    ${showBody ? `<textarea class="note-edit-body" maxlength="20000" placeholder="Take a note…" aria-label="Note">${esc(draft.body)}</textarea>` : ""}
    ${items}
    ${draftTagger(draft)}
    <div class="tag-suggest"></div>
    ${draft.error ? `<div class="err note-edit-error" role="alert">${esc(draft.error)}</div>` : ""}
    <div class="note-foot">
      <div class="note-tools">
        ${noteTool("pin", draft.pinned ? "Unpin" : "Pin", noteIcon("pin"), draft.pinned ? "on pinned" : "")}
        ${noteTool("list", draft.listMode ? "Turn into text" : "Turn into a checklist", noteIcon("list"), draft.listMode ? "on" : "")}
        ${noteTool("archive", note.archived ? "Unarchive" : "Archive", noteIcon(note.archived ? "unarchive" : "archive"))}
        ${armed ? `<button type="button" class="note-tool danger-armed" data-note-action="delete" aria-label="Tap again to delete">Delete?</button>`
          : noteTool("delete", "Delete", noteIcon("delete"))}
      </div>
      <span class="note-date" title="Edited ${esc(noteTime(note.updated_at))}&#10;Created ${esc(noteTime(note.created_at))}">Edited ${esc(noteDate(note.updated_at))}</span>
      <button type="button" class="note-done" data-note-action="done">Done</button>
    </div></div>`;
}

const cleanTags = (tags) => {
  const seen = new Map();
  for (const tag of tags) {
    const clean = tag.replace(/^#+/, "").trim().replace(/\s+/g, " ");
    if (clean && !seen.has(clean.toLowerCase())) seen.set(clean.toLowerCase(), clean);
  }
  return [...seen.values()];
};
const sameText = (a, b) => a.replace(/\r\n/g, "\n") === b.replace(/\r\n/g, "\n");

// Redraw the open editor from the draft, e.g. after adding a tag or a list item.
function refreshEditor(focus) {
  const modal = document.querySelector(".note-modal");
  const note = state.notes.find((item) => item.id === state.noteEditing);
  if (!modal || !note) return;
  modal.innerHTML = noteEditor(note);
  growEditorBody();
  const target = focus && modal.querySelector(focus);
  if (target) { target.focus(); if (target.setSelectionRange && target.value) target.setSelectionRange(target.value.length, target.value.length); }
}

function growEditorBody() {
  const body = document.querySelector(".note-modal .note-edit-body");
  if (!body) return;
  body.style.height = "auto";
  body.style.height = Math.min(Math.max(body.scrollHeight, 96), window.innerHeight * 0.5) + "px";
}

function toggleListMode(draft) {
  if (!draft.listMode) {
    // Each line of the text becomes an item.
    const lines = draft.body.split("\n").map((line) => line.trim()).filter(Boolean);
    draft.items = [...lines.map((text) => ({ text, done: false })), ...draft.items];
    draft.body = "";
    draft.listMode = true;
    if (!draft.items.length) draft.items.push({ text: "", done: false });
  } else {
    // Each item becomes a line of text (what was ticked off is not kept).
    const lines = draft.items.map((item) => item.text.trim()).filter(Boolean);
    draft.body = [draft.body.trim(), ...lines].filter(Boolean).join("\n");
    draft.items = [];
    draft.listMode = false;
  }
}

// Save what changed and close. With an error (say, an empty note) it stays open and says so.
async function finishEditing(card, extra = {}) {
  const note = state.notes.find((item) => item.id === state.noteEditing);
  if (!card || !note) { state.noteEditing = null; state.noteDraft = null; renderNotes(); return; }
  const draft = syncDraft(card);
  const pending = card.querySelector(".note-tag-input");
  if (pending && pending.value.trim()) draft.tags.push(...splitTags(pending.value).map(canonicalTag));
  const items = draft.items.filter((item) => item.text.trim()).map((item) => ({ text: item.text.trim(), done: item.done }));
  const fields = { ...extra };
  if (draft.title !== note.title) fields.title = draft.title;
  if (!sameText(draft.body, note.body)) fields.body = draft.body;
  if (JSON.stringify(items) !== JSON.stringify(note.checklist.map(({ text, done }) => ({ text, done })))) fields.checklist = items;
  const tags = cleanTags(draft.tags);
  if (JSON.stringify(tags) !== JSON.stringify(note.tags)) fields.tags = tags;
  if (draft.pinned !== note.pinned) fields.pinned = draft.pinned;
  if (Object.keys(fields).length) {
    try { await saveNote(note.id, fields); }
    catch (error) { draft.error = error.message || String(error); refreshEditor(); return; }
  }
  state.noteEditing = null;
  state.noteDraft = null;
  state.noteDeleteArmed = null;
  $("note-error").textContent = "";
  renderNotes();
}

async function editorClick(event, card, note) {
  const draft = syncDraft(card);
  const button = event.target.closest("[data-note-action]");
  const action = button && button.dataset.noteAction;
  const removeItem = event.target.closest("[data-note-remove-item]");
  const removeTag = event.target.closest("[data-draft-untag]");
  let focus = null;
  if (removeItem) {
    draft.items.splice(Number(removeItem.dataset.noteRemoveItem), 1);
  } else if (removeTag) {
    const gone = removeTag.dataset.draftUntag.toLowerCase();
    draft.tags = draft.tags.filter((tag) => tag.toLowerCase() !== gone);
  } else if (action === "add-item") {
    draft.items.push({ text: "", done: false });
    focus = ".note-edit-item:last-of-type .note-edit-text";
  } else if (action === "pin") {
    draft.pinned = !draft.pinned;
  } else if (action === "list") {
    toggleListMode(draft);
    focus = draft.listMode ? ".note-edit-item:last-of-type .note-edit-text" : ".note-edit-body";
  } else if (action === "archive") {
    await finishEditing(card, { archived: !note.archived });
    return;
  } else if (action === "delete") {
    if (state.noteDeleteArmed !== note.id) { state.noteDeleteArmed = note.id; refreshEditor(); return; }
    await api(`/notes/${note.id}`, { method: "DELETE" });
    state.notes = state.notes.filter((item) => item.id !== note.id);
    state.noteEditing = null;
    state.noteDraft = null;
    state.noteDeleteArmed = null;
    renderNotes();
    return;
  } else if (action === "done") {
    await finishEditing(card);
    return;
  } else {
    return;
  }
  state.noteDeleteArmed = null;
  refreshEditor(focus);
}

function editorKey(event, card) {
  const draft = syncDraft(card);
  const tagInput = event.target.closest(".note-tag-input");
  const itemText = event.target.closest(".note-edit-text");
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    finishEditing(card).catch(noteError);
  } else if (tagInput && handleTagKeys(event, tagInput)) {
    return;
  } else if (tagInput && event.key === "Enter") {
    event.preventDefault();
    draft.tags.push(...splitTags(tagInput.value).map(canonicalTag));
    draft.tags = cleanTags(draft.tags);
    refreshEditor(".note-tag-input");
  } else if (itemText && event.key === "Enter") {
    event.preventDefault();
    const index = [...card.querySelectorAll(".note-edit-text")].indexOf(itemText);
    draft.items.splice(index + 1, 0, { text: "", done: false });
    refreshEditor(`.note-edit-item:nth-child(${index + 2}) .note-edit-text`);
  } else if (itemText && event.key === "Backspace" && !itemText.value && draft.items.length > 1) {
    event.preventDefault();
    const index = [...card.querySelectorAll(".note-edit-text")].indexOf(itemText);
    draft.items.splice(index, 1);
    refreshEditor(`.note-edit-item:nth-child(${Math.max(index, 1)}) .note-edit-text`);
  }
}

// Small line icons for the card's action bar (24×24 viewBox, drawn with currentColor).
const NOTE_ICONS = {
  pin: '<path d="M12 17v5"/><path d="M9 10.76V6h6v4.76l2 3.24H7z"/><path d="M8 2h8"/>',
  tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  unarchive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="m9 15 3-3 3 3"/><path d="M12 12v6"/>',
  list: '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="m3 6 1 1 2-2"/><path d="m3 12 1 1 2-2"/><path d="m3 18 1 1 2-2"/>',
  delete: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/>',
};
const noteIcon = (name) => `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NOTE_ICONS[name]}</svg>`;
// "Oct 24" this year, "Oct 24, 2018" before; hovering gives the exact time.
function noteDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const options = { month: "short", day: "numeric" };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return date.toLocaleDateString(undefined, options);
}
const noteTime = (iso) => { const date = new Date(iso); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(); };
const noteTool = (action, label, icon, extra = "") =>
  `<button type="button" class="note-tool ${extra}" data-note-action="${action}" aria-label="${label}" title="${label}">${icon}</button>`;

// --- Picking a tag: focus the field to see existing tags, type to narrow them or create a new
// one. The same field is in the editor, on a card's tag button and in the new-note box.
const TAG_SUGGESTIONS = 8;

// The spelling already in use, so typing "airflow" doesn't make a second "Airflow".
function canonicalTag(text) {
  const clean = text.replace(/^#+/, "").trim().replace(/\s+/g, " ");
  const known = noteTagCounts(true).find((entry) => entry.tag.toLowerCase() === clean.toLowerCase());
  return known ? known.tag : clean;
}

const tagBoxOf = (input) => (input.id === "note-tags" ? $("note-tag-suggest")
  : input.closest(".note") && input.closest(".note").querySelector(".tag-suggest"));

// What the field is for: the tags the note has, what is being typed, and how to add one.
function tagContext(input) {
  if (input.id === "note-tags") {
    const parts = input.value.split(",");
    const query = parts.pop();
    const done = parts.map((part) => part.trim()).filter(Boolean);
    return { query, have: done, add(name) { input.value = [...done, name].join(", ") + ", "; input.focus(); showTagSuggestions(input); } };
  }
  const editor = input.closest(".note.editing");
  if (editor) {
    const draft = syncDraft(editor);
    return { query: input.value, have: draft.tags, add(name) { draft.tags = cleanTags([...draft.tags, name]); refreshEditor(".note-tag-input"); } };
  }
  const card = input.closest("[data-note]");
  const note = card && state.notes.find((item) => item.id === card.dataset.note);
  if (note) return { query: input.value, have: note.tags, add: (name) => addTags(note.id, [name]) };
  return null;
}

function tagOptions(query, have) {
  const q = query.trim().replace(/^#+/, "").toLowerCase();
  const haveSet = new Set(have.map((tag) => tag.toLowerCase()));
  const starts = (tag) => Number(tag.toLowerCase().startsWith(q));
  return noteTagCounts(true)
    .filter(({ tag }) => !haveSet.has(tag.toLowerCase()) && (!q || tag.toLowerCase().includes(q)))
    .sort((a, b) => starts(b.tag) - starts(a.tag) || b.count - a.count || a.tag.localeCompare(b.tag))
    .slice(0, TAG_SUGGESTIONS);
}

function showTagSuggestions(input) {
  const box = tagBoxOf(input);
  const context = tagContext(input);
  if (!box || !context) return;
  const query = context.query.trim().replace(/^#+/, "").replace(/\s+/g, " ");
  const known = noteTagCounts(true).some(({ tag }) => tag.toLowerCase() === query.toLowerCase());
  const has = context.have.some((tag) => tag.toLowerCase() === query.toLowerCase());
  const create = query && !known && !has
    ? `<button type="button" class="tag-option create" data-tag-name="${esc(query)}">+ New tag “${esc(query)}”</button>` : "";
  // Existing tags first (so the arrow keys reach them), "New tag" last.
  box.innerHTML = tagOptions(query, context.have).map(({ tag, count }) =>
    `<button type="button" class="tag-option" data-tag-name="${esc(tag)}">${esc(tag)} <span class="sub">${count}</span></button>`).join("") + create;
}

function hideTagSuggestions(input) {
  const box = tagBoxOf(input);
  if (box) box.innerHTML = "";
}

function pickSuggestedTag(option) {
  const box = option.closest(".tag-suggest");
  const input = box.id === "note-tag-suggest" ? $("note-tags") : box.closest(".note").querySelector(".note-tag-input");
  return tagContext(input).add(option.dataset.tagName);
}

// Arrow keys move through the suggestions and Enter takes the highlighted one; with none
// highlighted Enter adds what was typed (the caller's job). True when the key was used.
function handleTagKeys(event, input) {
  const box = tagBoxOf(input);
  const options = box ? [...box.querySelectorAll(".tag-option")] : [];
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && options.length) {
    const current = options.findIndex((option) => option.classList.contains("active"));
    options.forEach((option) => option.classList.remove("active"));
    const next = event.key === "ArrowDown" ? (current + 1) % options.length : (current <= 0 ? options.length - 1 : current - 1);
    options[next].classList.add("active");
    event.preventDefault();
    return true;
  }
  const active = event.key === "Enter" && options.find((option) => option.classList.contains("active"));
  if (active) {
    event.preventDefault();
    Promise.resolve(pickSuggestedTag(active)).catch(noteError);
    return true;
  }
  return false;
}

function noteTagger(note) {
  return `<div class="note-tagger">${note.tags.map((tag) =>
    `<span class="note-tag">${esc(tag)}<button type="button" class="note-untag" data-note-untag="${esc(tag)}"
      aria-label="Remove tag ${esc(tag)}" title="Remove">×</button></span>`).join("")}
    <input class="note-tag-input" placeholder="Add a tag, then Enter" aria-label="Add a tag" maxlength="50" autocomplete="off"></div>
    <div class="tag-suggest"></div>`;
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
      aria-label="Note ${esc(note.title || note.body.slice(0, 40))}">
    ${noteTool("pin", note.pinned ? "Unpin" : "Pin", noteIcon("pin"), "note-pin" + (note.pinned ? " on" : ""))}
    ${note.title ? `<div class="note-title">${esc(note.title)}</div>` : ""}
    ${note.body ? `<div class="note-body">${esc(note.body)}</div>` : ""}
    ${list}
    ${tags}
    <div class="note-foot">
      <span class="note-date" title="Edited ${esc(noteTime(note.updated_at))}&#10;Created ${esc(noteTime(note.created_at))}">Edited ${esc(noteDate(note.updated_at))}</span>
      <div class="note-tools">
      ${noteTool("tag", tagging ? "Done with tags" : "Tags", noteIcon("tag"), tagging ? "on" : "")}
      ${noteTool("archive", note.archived ? "Unarchive" : "Archive", noteIcon(note.archived ? "unarchive" : "archive"))}
      ${armed ? `<button type="button" class="note-tool danger-armed" data-note-action="delete" aria-label="Tap again to delete">Delete?</button>`
        : noteTool("delete", "Delete", noteIcon("delete"))}
      </div></div></div>`;
}

// Keep-style masonry: each card goes into the shortest column, left to right.
function placeNotes(notes) {
  const columns = [...$("note-list").querySelectorAll(":scope > .note-col")];
  for (const note of notes) {
    const shortest = columns.reduce((best, column) => (column.offsetHeight < best.offsetHeight ? column : best));
    shortest.insertAdjacentHTML("beforeend", noteCard(note));
  }
}

function updateMoreNotes(total) {
  const more = $("note-more");
  const left = total - state.noteRendered;
  more.hidden = left <= 0;
  if (left > 0) {
    more.firstElementChild.textContent = `Show more notes (${left.toLocaleString()} more)`;
    // Look again: if the button is still near the screen after a batch, load the next one.
    noteMoreObserver.unobserve(more);
    noteMoreObserver.observe(more);
  }
}

function showMoreNotes() {
  const notes = shownNotes();
  if (state.noteRendered >= notes.length) return;
  const batch = notes.slice(state.noteRendered, state.noteRendered + NOTE_BATCH);
  placeNotes(batch);
  state.noteRendered += batch.length;
  state.noteLimit = state.noteRendered;  // a redraw keeps showing what is on screen
  updateMoreNotes(notes.length);
}

const noteMoreObserver = new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting)) showMoreNotes();
}, { rootMargin: "900px" });
$("note-more").onclick = showMoreNotes;

function renderNotes() {
  const open = document.querySelector(".note-modal .note.editing");
  if (open) syncDraft(open);
  if (state.noteFilter === ARCHIVED && !state.notes.some((note) => note.archived)) state.noteFilter = "";
  renderNoteTags();
  const notes = shownNotes();
  const empty = state.notes.length
    ? (state.noteFilter === ARCHIVED ? "No archived notes match." : "No note matches.")
    : "No notes yet. Add one above, or import your Google Keep notes.";
  const list = $("note-list");
  if (!notes.length) {
    list.innerHTML = `<div class="empty">${empty}</div>`;
    state.noteRendered = 0;
  } else {
    const count = noteColumnCount();
    list.innerHTML = Array.from({ length: count }, () => `<div class="note-col"></div>`).join("");
    const visible = notes.slice(0, Math.max(state.noteLimit, NOTE_BATCH));
    placeNotes(visible);
    state.noteRendered = visible.length;
    state.noteColumns = count;
  }
  updateMoreNotes(notes.length);
  // A note being edited opens over the page, so it has room even on a phone.
  const editing = state.notes.find((note) => note.id === state.noteEditing);
  if (editing && state.noteDraft) {
    list.insertAdjacentHTML("beforeend", `<div class="note-modal" data-note-backdrop>${noteEditor(editing)}</div>`);
    growEditorBody();
  }
}

// Searching or filtering starts again from the first screenful, over all notes.
function showFirstNotes() {
  state.noteLimit = NOTE_BATCH;
  renderNotes();
}

function noteColumnCount() {
  const width = $("note-list").clientWidth || window.innerWidth;
  if (width < 300) return 1;
  if (window.matchMedia("(max-width: 720px)").matches) return 2;
  return Math.max(1, Math.min(8, Math.floor((width + 12) / (240 + 12))));
}

// Escape closes an open note (saving any change), wherever the focus is.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.tagPanelOpen) { setTagPanel(false); return; }
  if (event.key === "Escape" && state.noteEditing && !$("notes-view").hidden) {
    finishEditing(document.querySelector(".note-modal .note.editing")).catch(noteError);
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
      tags: [...splitTags($("note-tags").value).map(canonicalTag),
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

$("note-search").oninput = showFirstNotes;

function pickTag(chip) {
  state.noteFilter = chip.dataset.noteFilter;
  state.noteEditing = null;
  setTagPanel(false);
  showFirstNotes();
}

$("note-tags-bar").onclick = (event) => {
  if (event.target.closest("[data-note-tags-open]")) { setTagPanel(!state.tagPanelOpen); return; }
  const chip = event.target.closest("[data-note-filter]");
  if (chip) pickTag(chip);
};
$("note-tag-list").onclick = (event) => {
  const chip = event.target.closest("[data-note-filter]");
  if (chip) pickTag(chip);
};
$("note-tag-filter").oninput = () => renderTagPanel();
// Pressing Enter picks the only tag left in the list.
$("note-tag-filter").onkeydown = (event) => {
  const chips = $("note-tag-list").querySelectorAll("[data-note-filter]");
  if (event.key === "Enter" && chips.length === 1) { event.preventDefault(); pickTag(chips[0]); }
};
// A click outside the tag row and list closes the list. The path is taken from the event,
// because the row is redrawn by the click itself and its button is gone by now.
document.addEventListener("click", (event) => {
  const inside = event.composedPath().includes(document.querySelector(".note-tags-wrap"));
  if (state.tagPanelOpen && !inside) setTagPanel(false);
});

async function addTags(id, names) {
  const note = state.notes.find((item) => item.id === id);
  if (!note || !names.length) return;
  await saveNote(id, { tags: [...note.tags, ...names] });
  renderNotes();
  const again = document.querySelector(`[data-note="${CSS.escape(id)}"] .note-tag-input`);
  if (again) again.focus();
}

$("note-list").onclick = async (event) => {
  if (event.target.dataset && "noteBackdrop" in event.target.dataset) {
    finishEditing(event.target.querySelector(".note.editing")).catch(noteError);
    return;
  }
  const card = event.target.closest("[data-note]");
  if (!card) return;
  const id = card.dataset.note;
  const note = state.notes.find((item) => item.id === id);
  if (card.classList.contains("editing")) {
    try { await editorClick(event, card, note); } catch (error) { noteError(error); }
    return;
  }
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
      state.noteLimit = NOTE_BATCH;
    } else if (check !== undefined) {
      const checklist = note.checklist.map((item, index) =>
        (index === Number(check) ? { ...item, done: event.target.checked } : item));
      await saveNote(id, { checklist });
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
    } else if (action === "delete") {
      if (state.noteDeleteArmed !== id) { state.noteDeleteArmed = id; renderNotes(); return; }
      await api(`/notes/${id}`, { method: "DELETE" });
      state.notes = state.notes.filter((item) => item.id !== id);
    } else {
      openEditor(note);
      renderNotes();
      // On a phone, don't pop the keyboard up just for reading a note.
      const body = document.querySelector(".note-modal .note-edit-body");
      if (body && !window.matchMedia("(pointer: coarse)").matches) {
        body.focus();
        body.setSelectionRange(body.value.length, body.value.length);
      }
      return;
    }
    state.noteDeleteArmed = null;
    $("note-error").textContent = "";
    renderNotes();
  } catch (error) { noteError(error); }
};

// The suggestions follow focus and typing in any of the three fields.
$("note-list").addEventListener("focusin", (event) => {
  if (event.target.closest(".note-tag-input")) showTagSuggestions(event.target);
});
$("note-list").addEventListener("input", (event) => {
  if (event.target.closest(".note-tag-input")) showTagSuggestions(event.target);
});
$("note-list").addEventListener("focusout", (event) => {
  if (event.target.closest(".note-tag-input")) hideTagSuggestions(event.target);
});
// Tapping a suggestion must not take the focus away from the field first.
$("note-list").addEventListener("mousedown", (event) => {
  if (event.target.closest(".tag-option")) event.preventDefault();
});
$("note-list").addEventListener("click", (event) => {
  const option = event.target.closest(".tag-option");
  if (!option) return;
  event.stopPropagation();  // not a click on the note behind it
  Promise.resolve(pickSuggestedTag(option)).catch(noteError);
}, true);
$("note-tags").addEventListener("focus", (event) => showTagSuggestions(event.target));
$("note-tags").addEventListener("input", (event) => showTagSuggestions(event.target));
$("note-tags").addEventListener("blur", (event) => hideTagSuggestions(event.target));
$("note-tags").addEventListener("keydown", (event) => handleTagKeys(event, event.target));
$("note-tag-suggest").addEventListener("mousedown", (event) => {
  if (event.target.closest(".tag-option")) event.preventDefault();
});
$("note-tag-suggest").addEventListener("click", (event) => {
  const option = event.target.closest(".tag-option");
  if (option) pickSuggestedTag(option);
});

$("note-list").onkeydown = (event) => {
  const editor = event.target.closest(".note.editing");
  if (editor) { editorKey(event, editor); return; }
  const tagInput = event.target.closest(".note-tag-input");
  if (tagInput) {
    const id = tagInput.closest("[data-note]").dataset.note;
    if (handleTagKeys(event, tagInput)) return;
    if (event.key === "Enter") { event.preventDefault(); addTags(id, splitTags(tagInput.value).map(canonicalTag)).catch(noteError); }
    else if (event.key === "Escape") { state.noteTagging = null; renderNotes(); }
    return;
  }
  const card = event.target.closest(".note:not(.editing)");
  if (card && event.target === card && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    openEditor(state.notes.find((item) => item.id === card.dataset.note));
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
