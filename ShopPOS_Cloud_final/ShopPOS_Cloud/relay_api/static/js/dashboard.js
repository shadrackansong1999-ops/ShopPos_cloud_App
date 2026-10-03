let trendChart = null;
let reportsChart = null;
let salesPage = 1, salesTotal = 0, salesPageSize = 50;
let logsPage = 1, logsTotal = 0, logsPageSize = 50;
let inventoryCache = [];

function ghs(n) {
  return "GH\u20B5" + (Number(n) || 0).toLocaleString("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function timeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const secs = Math.floor((Date.now() - then.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return Math.floor(secs / 60) + "m ago";
  if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
  return Math.floor(secs / 86400) + "d ago";
}

function fmtDateTime(s) {
  return s ? s.slice(0, 16).replace("T", " ") : "-";
}

function setTable(tbodyId, emptyId, rows, rowFn) {
  const tbody = document.querySelector(`#${tbodyId} tbody`);
  const empty = document.getElementById(emptyId);
  tbody.innerHTML = "";
  if (!rows.length) {
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";
  for (const r of rows) tbody.insertAdjacentHTML("beforeend", rowFn(r));
}

/* ── Theme ────────────────────────────────────────────────────────────── */
const THEME_ACCENTS = {
  midnight: "#0F131A", daylight: "#FAF8F3", ocean: "#081A24",
  sunset: "#1C1410", forest: "#0D1813",
};
function setTheme(name, skipSave) {
  document.documentElement.setAttribute("data-theme", name);
  const sel = document.getElementById("theme-select");
  if (sel) sel.value = name;
  const meta = document.getElementById("theme-color-meta");
  if (meta && THEME_ACCENTS[name]) meta.setAttribute("content", THEME_ACCENTS[name]);
  if (!skipSave) {
    try { localStorage.setItem("shoppos_theme", name); } catch (e) {}
  }
}
(function initTheme() {
  let saved = "midnight";
  try { saved = localStorage.getItem("shoppos_theme") || "midnight"; } catch (e) {}
  setTheme(saved, true);
})();

/* ── Shop switching ──────────────────────────────────────────────────── */
function switchShop(shopId) {
  if (!shopId) return;
  const url = new URL(window.location.href);
  url.searchParams.set("shop", shopId);
  window.location.href = url.toString();
}

/* ── Tabs ─────────────────────────────────────────────────────────────── */
const TAB_LOADERS = {
  overview: refreshOverview,
  sales: () => loadSales(1),
  inventory: loadInventory,
  logs: () => loadLogs(1),
  users: loadUsers,
  reports: loadReports,
};
let activeTab = "overview";

function showTab(name) {
  activeTab = name;
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("hidden", p.id !== `panel-${name}`));
  const title = document.getElementById("page-title");
  if (title && TAB_TITLES[name]) title.textContent = TAB_TITLES[name];
  if (typeof closeDrawer === "function") closeDrawer();
  TAB_LOADERS[name] && TAB_LOADERS[name]();
}

function refreshActiveTab() {
  TAB_LOADERS[activeTab] && TAB_LOADERS[activeTab]();
}

/* ── Date range helpers ──────────────────────────────────────────────── */
function defaultDates() {
  const to = new Date();
  const from = new Date(); from.setDate(from.getDate() - 30);
  return [from.toISOString().slice(0, 10), to.toISOString().slice(0, 10)];
}
(function initDateInputs() {
  const [from, to] = defaultDates();
  for (const prefix of ["sales", "logs", "reports"]) {
    const f = document.getElementById(`${prefix}-from`);
    const t = document.getElementById(`${prefix}-to`);
    if (f) f.value = from;
    if (t) t.value = to;
  }
})();

function apiGet(path, params) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("shop_id", SHOP_ID);
  for (const k in (params || {})) url.searchParams.set(k, params[k]);
  return fetch(url).then(r => r.json());
}

/* ── Overview ─────────────────────────────────────────────────────────── */
function renderTrend(trend) {
  const labels = trend.map(t => t.date.slice(5));
  const data = trend.map(t => t.total_revenue);
  const ctx = document.getElementById("trend-chart").getContext("2d");
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ label: "Revenue", data, borderColor: "#4C7CF3",
      backgroundColor: "rgba(76,124,243,.15)", fill: true, tension: 0.3, pointRadius: 2 }] },
    options: { responsive: true, plugins: { legend: { display: false } },
      scales: { x: { grid: { color: "#3335" }, ticks: { color: "#8A93AC" } },
                y: { grid: { color: "#3335" }, ticks: { color: "#8A93AC" } } } },
  });
}

async function refreshOverview() {
  const d = await apiGet("/api/dashboard/data");
  if (!d.ok) { if (d.error === "Not authenticated") window.location.href = "/login"; return; }

  document.getElementById("last-seen").textContent = "Last synced " + timeAgo(d.last_seen);
  document.getElementById("kpi-revenue").textContent = ghs(d.today.revenue);
  document.getElementById("kpi-sales").textContent = d.today.num_sales;
  document.getElementById("kpi-discounts").textContent = ghs(d.today.discounts);
  document.getElementById("kpi-lowstock").textContent = d.low_stock_count;

  renderTrend(d.trend);

  setTable("cashier-table", "cashier-empty", d.today.by_cashier, c => `
    <tr><td>${c.cashier || "-"}</td><td>${c.txn}</td><td>${ghs(c.revenue)}</td></tr>`);

  setTable("recent-table", "recent-empty", d.recent_sales, s => `
    <tr>
      <td>${s.invoice_number || "-"}</td>
      <td>${s.customer_name || "Walk-in"}</td>
      <td>${s.cashier || "-"}</td>
      <td>${ghs(s.total)}</td>
      <td>${s.payment_method || "-"}</td>
      <td>${fmtDateTime(s.created_at)}</td>
    </tr>`);

  setTable("lowstock-preview-table", "lowstock-preview-empty", d.low_stock, p => `
    <tr>
      <td>${p.name || "-"}</td>
      <td>${p.sku || "-"}</td>
      <td><span class="badge ${p.quantity <= 0 ? "badge-red" : "badge-amber"}">${p.quantity} ${p.unit || ""}</span></td>
      <td>${p.reorder_level}</td>
    </tr>`);
}

/* ── Sales History ────────────────────────────────────────────────────── */
async function loadSales(page) {
  salesPage = page;
  const from = document.getElementById("sales-from").value;
  const to = document.getElementById("sales-to").value;
  const d = await apiGet("/api/dashboard/sales", { from, to, page, page_size: salesPageSize });
  if (!d.ok) return;
  salesTotal = d.total;
  setTable("sales-table", "sales-empty", d.sales, s => `
    <tr>
      <td>${s.invoice_number || "-"}</td>
      <td>${s.customer_name || "Walk-in"}</td>
      <td>${s.cashier || "-"}</td>
      <td>${ghs(s.subtotal)}</td>
      <td>${ghs(s.discount)}</td>
      <td>${ghs(s.total)}</td>
      <td>${s.payment_method || "-"}</td>
      <td><span class="badge ${s.status === 'completed' ? 'badge-green' : (s.status === 'voided' ? 'badge-red' : 'badge-amber')}">${s.status || "-"}</span></td>
      <td>${fmtDateTime(s.created_at)}</td>
    </tr>`);
  const pages = Math.max(1, Math.ceil(salesTotal / salesPageSize));
  document.getElementById("sales-pageinfo").textContent = `Page ${salesPage} of ${pages} (${salesTotal} sales)`;
}
function pageSales(delta) {
  const pages = Math.max(1, Math.ceil(salesTotal / salesPageSize));
  const next = Math.min(pages, Math.max(1, salesPage + delta));
  if (next !== salesPage) loadSales(next);
}

/* ── Inventory ────────────────────────────────────────────────────────── */
async function loadInventory() {
  const d = await apiGet("/api/dashboard/inventory");
  if (!d.ok) return;
  inventoryCache = d.inventory;
  renderInventory();
}
function renderInventory() {
  const q = (document.getElementById("inv-search").value || "").toLowerCase();
  const rows = q ? inventoryCache.filter(p =>
    (p.name || "").toLowerCase().includes(q) || (p.sku || "").toLowerCase().includes(q)) : inventoryCache;
  setTable("inventory-table", "inventory-empty", rows, p => {
    const low = p.quantity <= (p.reorder_level || 0);
    return `<tr>
      <td>${p.name || "-"}</td>
      <td>${p.sku || "-"}</td>
      <td>${p.quantity}</td>
      <td>${p.reorder_level}</td>
      <td>${p.unit || "-"}</td>
      <td>${ghs(p.selling_price)}</td>
      <td><span class="badge ${low ? "badge-amber" : "badge-green"}">${low ? "Low" : "OK"}</span></td>
    </tr>`;
  });
}

/* ── Product Logs ─────────────────────────────────────────────────────── */
async function loadLogs(page) {
  logsPage = page;
  const from = document.getElementById("logs-from").value;
  const to = document.getElementById("logs-to").value;
  const d = await apiGet("/api/dashboard/product-logs", { from, to, page, page_size: logsPageSize });
  if (!d.ok) return;
  logsTotal = d.total;
  setTable("logs-table", "logs-empty", d.logs, m => `
    <tr>
      <td>${m.product_name || "-"}</td>
      <td>${m.movement_type || "-"}</td>
      <td>${m.quantity}</td>
      <td>${m.reference || "-"}</td>
      <td>${m.notes || "-"}</td>
      <td>${fmtDateTime(m.created_at)}</td>
    </tr>`);
  const pages = Math.max(1, Math.ceil(logsTotal / logsPageSize));
  document.getElementById("logs-pageinfo").textContent = `Page ${logsPage} of ${pages} (${logsTotal} entries)`;
}
function pageLogs(delta) {
  const pages = Math.max(1, Math.ceil(logsTotal / logsPageSize));
  const next = Math.min(pages, Math.max(1, logsPage + delta));
  if (next !== logsPage) loadLogs(next);
}

/* ── Users ────────────────────────────────────────────────────────────── */
async function loadUsers() {
  const d = await apiGet("/api/dashboard/users");
  if (!d.ok) return;
  setTable("users-table", "users-empty", d.users, u => `
    <tr>
      <td>${u.username || "-"}</td>
      <td>${u.full_name || "-"}</td>
      <td>${u.role || "-"}</td>
      <td><span class="badge ${u.is_active ? "badge-green" : "badge-red"}">${u.is_active ? "Active" : "Inactive"}</span></td>
      <td>${u.last_login ? fmtDateTime(u.last_login) : "Never"}</td>
    </tr>`);
}

/* ── Reports ──────────────────────────────────────────────────────────── */
async function loadReports() {
  const from = document.getElementById("reports-from").value;
  const to = document.getElementById("reports-to").value;
  const d = await apiGet("/api/dashboard/reports", { from, to });
  if (!d.ok) return;

  document.getElementById("rep-revenue").textContent = ghs(d.totals.revenue);
  document.getElementById("rep-sales").textContent = d.totals.num_sales;
  document.getElementById("rep-discounts").textContent = ghs(d.totals.discounts);

  const labels = d.by_day.map(r => r.date.slice(5));
  const data = d.by_day.map(r => r.total_revenue);
  const ctx = document.getElementById("reports-chart").getContext("2d");
  if (reportsChart) reportsChart.destroy();
  reportsChart = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: "Revenue", data, backgroundColor: "#4C7CF3" }] },
    options: { responsive: true, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color: "#8A93AC" } },
                y: { grid: { color: "#3335" }, ticks: { color: "#8A93AC" } } } },
  });

  setTable("payment-table", "payment-empty", d.by_payment, p => `
    <tr><td>${p.method}</td><td>${ghs(p.revenue)}</td></tr>`);

  setTable("top-products-table", "top-products-empty", d.top_products, p => `
    <tr><td>${p.product_name}</td><td>${p.qty}</td><td>${ghs(p.revenue)}</td></tr>`);
}

/* ── Boot ─────────────────────────────────────────────────────────────── */
refreshOverview();
setInterval(() => { if (activeTab === "overview") refreshOverview(); }, 15000);
