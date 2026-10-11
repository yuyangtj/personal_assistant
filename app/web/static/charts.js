// Draws chart and table blocks from data. The server sends numbers and labels only; every
// string reaches the page through esc(), so a block can never inject markup.
const chartHidden = new Set(); // "messageId|seriesName" the user switched off in the legend
const CHART_COLORS = ["var(--accent)", "var(--warn)", "var(--success)", "var(--danger)", "#8aa4ff", "#d49bff"];
const CHART = { width: 320, height: 180, left: 44, right: 10, top: 10, bottom: 26 };

function niceMax(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((candidate) => candidate * power >= value / 4) * power;
  return Math.ceil(value / step) * step;
}
const compact = (n) => Math.abs(n) >= 1e6 ? `${+(n / 1e6).toFixed(1)}M`
  : Math.abs(n) >= 1e3 ? `${+(n / 1e3).toFixed(1)}k` : String(+n.toFixed(2));

function chartSvg(block, m) {
  const shown = block.series.filter((s) => !chartHidden.has(`${m.id}|${s.name}`));
  const xs = [...new Set(block.series.flatMap((s) => s.points.map((p) => p[0])))].sort();
  const { width, height, left, right, top, bottom } = CHART;
  const max = niceMax(Math.max(0, ...shown.flatMap((s) => s.points.map((p) => p[1]))));
  const slot = (width - left - right) / xs.length;
  const px = (x) => left + slot * (xs.indexOf(x) + 0.5);
  const py = (y) => top + (height - top - bottom) * (1 - y / max);
  const unit = block.y.unit ? ` ${block.y.unit}` : "";
  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => `<line x1="${left}" x2="${width - right}" y1="${py(max * f)}"`
    + ` y2="${py(max * f)}" class="grid"/><text x="${left - 4}" y="${py(max * f) + 3}" text-anchor="end">`
    + `${esc(compact(max * f))}</text>`).join("");
  const every = Math.ceil(xs.length / 6);
  const ticks = xs.map((x, i) => i % every ? "" : `<text x="${px(x)}" y="${height - 8}" text-anchor="middle">`
    + `${esc(block.x.type === "time" ? x.slice(2) : x.slice(0, 8))}</text>`).join("");
  const barWidth = Math.min(28, (slot * 0.8) / Math.max(1, shown.length));
  const marks = shown.map((s) => {
    const color = CHART_COLORS[block.series.indexOf(s) % CHART_COLORS.length];
    const dots = s.points.map(([x, y]) => {
      const tip = `${s.name}, ${x}: ${compact(y)}${unit}`;
      const cx = block.kind === "bar"
        ? px(x) + (shown.indexOf(s) - (shown.length - 1) / 2) * barWidth : px(x);
      return block.kind === "bar"
        ? `<rect x="${cx - barWidth / 2}" y="${py(y)}" width="${barWidth - 1}" height="${Math.max(0, py(0) - py(y))}"`
          + ` fill="${color}" class="mark" data-tip="${esc(tip)}"/>`
        : `<circle cx="${cx}" cy="${py(y)}" r="3.5" fill="${color}" class="mark" data-tip="${esc(tip)}"/>`;
    }).join("");
    if (block.kind === "bar") return dots;
    const line = s.points.map(([x, y]) => `${px(x)},${py(y)}`).join(" ");
    const area = block.kind === "area" ? `<polygon points="${px(s.points[0][0])},${py(0)} ${line} `
      + `${px(s.points[s.points.length - 1][0])},${py(0)}" fill="${color}" opacity="0.15"/>` : "";
    return `${area}<polyline points="${line}" fill="none" stroke="${color}" stroke-width="2"/>${dots}`;
  }).join("");
  return `<svg viewBox="0 0 ${width} ${height}" class="chart-svg" role="img" aria-label="${esc(block.title)}">`
    + `${grid}${ticks}${marks}</svg>`;
}

const BLOCK_CHARTS = {
  chart: (block, m) => {
    const legend = block.series.length > 1 ? `<div class="chart-legend">${block.series.map((s, i) =>
      `<button type="button" class="chart-key ${chartHidden.has(`${m.id}|${s.name}`) ? "off" : ""}"`
      + ` data-series="${esc(`${m.id}|${s.name}`)}"><i style="background:${CHART_COLORS[i % CHART_COLORS.length]}"></i>`
      + `${esc(s.name)}</button>`).join("")}</div>` : "";
    // Screen readers get the numbers as a table; sighted users get the drawing.
    const rows = block.series.flatMap((s) => s.points.map(([x, y]) => `<tr><td>${esc(s.name)}</td><td>${esc(x)}</td>`
      + `<td>${esc(y)}</td></tr>`)).join("");
    return `<figure class="chart"><figcaption>${esc(block.title)}</figcaption>${legend}${chartSvg(block, m)}`
      + `<table class="sr-only"><tbody>${rows}</tbody></table></figure>`;
  },
  table: (block) => `<table class="block-table">${block.title ? `<caption>${esc(block.title)}</caption>` : ""}`
    + `<thead><tr>${block.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>`
    + block.rows.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")
    + `</tbody></table>`,
};
Object.assign(BLOCKS, BLOCK_CHARTS);

// The transcript is re-rendered on every poll, so the handlers sit on it, not on the marks.
(function wireCharts() {
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  document.body.appendChild(tip);
  const show = (mark, event) => {
    tip.textContent = mark.dataset.tip;
    tip.hidden = false;
    tip.style.left = `${Math.min(event.clientX + 10, window.innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(8, event.clientY - 34)}px`;
  };
  const transcript = document.getElementById("transcript");
  transcript.addEventListener("pointermove", (event) => {
    const mark = event.target.closest?.(".mark");
    if (mark) show(mark, event); else tip.hidden = true;
  });
  transcript.addEventListener("pointerleave", () => { tip.hidden = true; });
  transcript.addEventListener("click", (event) => {
    const mark = event.target.closest?.(".mark");
    if (mark) show(mark, event); // a tap on a phone, where there is no hover
    const key = event.target.closest?.("[data-series]");
    if (!key) return;
    const id = key.dataset.series;
    if (chartHidden.has(id)) chartHidden.delete(id); else chartHidden.add(id);
    renderTranscript();
  });
})();
