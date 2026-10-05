// Purchases: import Klarna exports and browse what was imported.

let purchaseSearchTimer = null;

async function loadPurchases() {
  const merchant = $("purchase-search").value.trim();
  try {
    const body = await api(`/purchases?limit=200${merchant ? "&merchant=" + encodeURIComponent(merchant) : ""}`);
    renderPurchases(body);
    $("purchase-error").textContent = "";
  } catch (error) { $("purchase-error").textContent = error.message || String(error); }
}

function renderPurchases({ overview, purchases }) {
  $("purchase-status").textContent = overview.count
    ? `${overview.count} purchases from ${overview.first} to ${overview.last}; last import ${String(overview.last_imported_at).slice(0, 10)}.`
    : "Nothing imported yet. Import a Klarna export (CSV) above.";
  $("purchase-list").innerHTML = purchases.length
    ? `<table class="purchases"><tbody>${purchases.map((p) => `<tr>
        <td class="sub">${esc(p.date)}</td>
        <td>${esc(p.merchant)}${p.status ? `<div>${statusPill(p.status.toLowerCase() === "pending" ? "pending" : "failed", p.status)}</div>` : ""}</td>
        <td class="num">${esc(Number(p.amount).toLocaleString("sv-SE"))} ${esc(p.currency)}</td>
        <td class="sub pay">${esc(p.payment_type)}</td>
      </tr>`).join("")}</tbody></table>`
    : (overview.count ? `<div class="empty">No purchase matches.</div>` : "");
}

$("purchase-import").onsubmit = async (event) => {
  event.preventDefault();
  const file = $("purchase-file").files[0];
  if (!file) return;
  $("purchase-error").textContent = "";
  $("purchase-status").textContent = "Importing…";
  try {
    const result = await api("/purchases/import", {
      method: "POST", body: JSON.stringify({ source: "klarna", csv: await file.text() }),
    });
    $("purchase-file").value = "";
    await loadPurchases();
    $("purchase-status").textContent = `Imported ${result.rows} rows: ${result.added} new, ${result.updated} updated, ${result.unchanged} already there. `
      + $("purchase-status").textContent;
  } catch (error) {
    $("purchase-status").textContent = "";
    $("purchase-error").textContent = error.message || String(error);
  }
};

$("purchase-search").oninput = () => {
  clearTimeout(purchaseSearchTimer);
  purchaseSearchTimer = setTimeout(loadPurchases, 250);
};
