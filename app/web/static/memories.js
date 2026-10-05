// Memories: what the assistant remembers, to read, edit, mark private, or delete.

state.memories = [];
state.memoryEditing = null;

async function loadMemories() {
  try {
    state.memories = (await api("/memories")).memories;
    $("memory-error").textContent = "";
  } catch (error) { $("memory-error").textContent = error.message || String(error); }
  renderMemories();
}

function memoryCard(memory) {
  const food = (memory.tags || []).includes("food");
  if (state.memoryEditing === memory.id) {
    return `<div class="memory editing" data-memory="${esc(memory.id)}">
      <textarea class="memory-edit" rows="3" maxlength="4000" aria-label="Memory">${esc(memory.content)}</textarea>
      <div class="row wrap">
        <select class="memory-edit-kind" aria-label="Kind">${["fact", "preference", "project"].map((kind) =>
          `<option value="${kind}" ${kind === memory.kind ? "selected" : ""}>${kind[0].toUpperCase() + kind.slice(1)}</option>`).join("")}</select>
        <label class="check"><input type="checkbox" class="memory-edit-food" ${food ? "checked" : ""}> Food</label>
        <label class="check"><input type="checkbox" class="memory-edit-private" ${memory.private ? "checked" : ""}> Private</label>
        <span class="grow"></span>
        <button type="button" class="quiet" data-memory-action="cancel">Cancel</button>
        <button type="button" data-memory-action="save">Save</button></div></div>`;
  }
  return `<div class="memory" data-memory="${esc(memory.id)}">
    <div class="memory-text">${esc(memory.content)}</div>
    <div class="row wrap memory-meta">
      <span class="status-pill">${esc(memory.kind)}</span>
      ${food ? `<span class="status-pill">food</span>` : ""}
      ${memory.private ? `<span class="status-pill warn" title="Never sent to any AI model">🔒 private</span>` : ""}
      <span class="sub">${esc(String(memory.created_at).slice(0, 10))}</span>
      <span class="grow"></span>
      <button type="button" class="quiet" data-memory-action="edit">Edit</button>
      <button type="button" class="quiet" data-memory-action="delete">Delete</button></div></div>`;
}

function renderMemories() {
  const query = $("memory-search").value.trim().toLowerCase();
  const shown = state.memories.filter((memory) => !query
    || (memory.content + " " + (memory.tags || []).join(" ")).toLowerCase().includes(query));
  $("memory-list").innerHTML = shown.length ? shown.map(memoryCard).join("")
    : `<div class="empty">${state.memories.length ? "No memory matches." : "Nothing saved yet."}</div>`;
}

const withFood = (tags, food) =>
  [...(tags || []).filter((tag) => tag !== "food"), ...(food ? ["food"] : [])];

$("memory-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/memories", { method: "POST", body: JSON.stringify({
      content: $("memory-content").value, kind: $("memory-kind").value,
      tags: $("memory-food").checked ? ["food"] : [], private: $("memory-private").checked,
    }) });
    $("memory-content").value = "";
    $("memory-food").checked = false;
    $("memory-private").checked = false;
    await loadMemories();
  } catch (error) { $("memory-error").textContent = error.message || String(error); }
};

$("memory-search").oninput = renderMemories;

$("memory-list").onclick = async (event) => {
  const action = event.target.dataset && event.target.dataset.memoryAction;
  const card = event.target.closest("[data-memory]");
  if (!action || !card) return;
  const id = card.dataset.memory;
  const memory = state.memories.find((item) => item.id === id);
  try {
    if (action === "edit") state.memoryEditing = id;
    else if (action === "cancel") state.memoryEditing = null;
    else if (action === "save") {
      await api(`/memories/${id}`, { method: "PATCH", body: JSON.stringify({
        content: card.querySelector(".memory-edit").value,
        kind: card.querySelector(".memory-edit-kind").value,
        tags: withFood(memory.tags, card.querySelector(".memory-edit-food").checked),
        private: card.querySelector(".memory-edit-private").checked,
      }) });
      state.memoryEditing = null;
      return loadMemories();
    } else if (action === "delete") {
      await api(`/memories/${id}`, { method: "DELETE" });
      return loadMemories();
    }
    renderMemories();
  } catch (error) { $("memory-error").textContent = error.message || String(error); }
};
