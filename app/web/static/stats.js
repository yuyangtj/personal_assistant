// Stats: how the assistant is doing (reply times, failures, sizes).

// "8.2 s", "3 min 12 s", "1 h 5 min".
function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

async function loadStats() {
  try {
    const [stats, abilities] = await Promise.all([
      api(`/stats?days=${encodeURIComponent($("stats-days").value)}`), api("/abilities"),
    ]);
    $("stats-error").textContent = "";
    renderStats(stats);
    renderAbilities(abilities.abilities);
  } catch (error) { $("stats-error").textContent = error.message || String(error); }
}

// What the assistant can do (abilities/*.yaml): the same list it is given about itself.
const BANDS = [
  ["free", "Done without asking"],
  ["review", "Done, then you review the result"],
  ["ask", "Only after you confirm"],
  ["never", "Never done by the assistant"],
];

function renderAbilities(abilities) {
  $("abilities-body").innerHTML = BANDS.map(([band, heading]) => {
    const listed = abilities.filter((ability) => ability.band === band);
    if (!listed.length) return "";
    return `<h3>${esc(heading)}</h3><ul class="abilities">${listed.map((ability) =>
      `<li class="${ability.available ? "" : "unavailable"}"><strong>${esc(ability.name)}</strong>
        ${ability.kind === "job" ? `<span class="status-pill">runs in the background</span>` : ""}
        ${ability.available ? "" : `<span class="status-pill warn">not set up on this server</span>`}
        <div class="sub">${esc(ability.summary)}${ability.example ? ` · “${esc(ability.example)}”` : ""}</div></li>`).join("")}</ul>`;
  }).join("");
}

const count = (number, one, many = one + "s") =>
  `<strong>${number.toLocaleString()}</strong> ${number === 1 ? one : many}`;

function renderStats(stats) {
  const rows = stats.kinds.map((kind) => `<tr>
    <th scope="row">${esc(kind.kind)}</th>
    <td>${kind.tasks}</td><td>${kind.completed}</td>
    <td class="${kind.failed ? "bad" : ""}">${kind.failed}</td>
    <td>${kind.unfinished}</td>
    <td>${formatSeconds(kind.reply_median_s)}</td><td>${formatSeconds(kind.reply_p90_s)}</td>
    <td>${formatSeconds(kind.queue_wait_median_s)}</td></tr>`).join("");
  const table = stats.kinds.length ? `<div class="stats-scroll"><table class="stats-table">
    <thead><tr><th></th><th>Tasks</th><th>Done</th><th>Failed</th><th>Not finished</th>
      <th title="Half of the replies were faster than this">Typical reply</th>
      <th title="9 in 10 replies were faster than this">Slow replies</th>
      <th title="Time spent queued before work started">Queue wait</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`
    : `<div class="empty">No work in this period.</div>`;
  const counts = stats.counts, system = stats.system;
  $("stats-body").innerHTML = `${table}
    <h2>In the app</h2>
    <div class="stats-facts">
      <div>${count(counts.chats, "chat")}</div>
      <div>${count(counts.messages, "message")} in this period</div>
      <div>${count(counts.notes, "note")}</div>
      <div>${count(counts.memories, "memory", "memories")}</div>
      <div>${count(counts.work_items, "work item")}</div></div>
    <h2>Server</h2>
    <div class="stats-facts">
      <div>Disk <strong>${system.disk_used_percent}%</strong> used
        (${system.disk_free_gb} of ${system.disk_total_gb} GB free)</div>
      ${system.database_mb === null ? "" : `<div>Database <strong>${system.database_mb} MB</strong></div>`}</div>`;
}

$("stats-days").onchange = loadStats;
