const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

const LS_LAST_EVENT_ID = "admin_last_event_id";
let lastEventId = Number(localStorage.getItem(LS_LAST_EVENT_ID) || 0);
let eventsBootstrapped = false;
let pollingPaused = false;
let compactMode = (localStorage.getItem("admin_compact") !== "0");
const DRAFT_STORAGE_KEY = "menuflow_order_drafts_v2";
const orderDrafts = JSON.parse(sessionStorage.getItem(DRAFT_STORAGE_KEY) || "{}");
const PAYMENT_DRAFT_STORAGE_KEY = "menuflow_payment_drafts_v1";
const paymentDrafts = JSON.parse(sessionStorage.getItem(PAYMENT_DRAFT_STORAGE_KEY) || "{}");
let editReleaseTimer = null;
let activeEditOrderId = null;
function persistDrafts(){ try{ sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(orderDrafts)); }catch(e){} }
function persistPaymentDrafts(){ try{ sessionStorage.setItem(PAYMENT_DRAFT_STORAGE_KEY, JSON.stringify(paymentDrafts)); }catch(e){} }
function setPaymentDraft(key, patch){ const d = paymentDrafts[key] || {}; paymentDrafts[key] = Object.assign(d, patch, { dirty: true, updatedAt: Date.now() }); persistPaymentDrafts(); }
function clearPaymentDraft(key){ if(paymentDrafts[key]){ delete paymentDrafts[key]; persistPaymentDrafts(); } }
function getPaymentDraft(key){ return paymentDrafts[key] || null; }
function hasDirtyPaymentDrafts(){ return Object.values(paymentDrafts).some(d => d && d.dirty); }

function setDraft(orderId, patch) {
  const d = orderDrafts[orderId] || {};
  orderDrafts[orderId] = Object.assign(d, patch, { dirty: true, updatedAt: Date.now() });
  persistDrafts();
}
function clearDraft(orderId) { if (orderDrafts[orderId]) { delete orderDrafts[orderId]; persistDrafts(); } }
function getDraft(orderId) { return orderDrafts[orderId] || null; }
function hasDirtyDrafts() { return Object.values(orderDrafts).some(d => d && d.dirty); }
function elementIsEditing() {
  const ae = document.activeElement;
  return !!(ae && /INPUT|TEXTAREA|SELECT/.test(ae.tagName) && !ae.disabled && !ae.readOnly);
}
function adminUiBusy(){
  return pollingPaused || hasDirtyDrafts() || hasDirtyPaymentDrafts() || elementIsEditing();
}
function setEditState(label, tone="") {
  const el = $("#editState");
  if (!el) return;
  el.textContent = label;
  el.className = `admin-chip ${tone}`.trim();
}
function pausePolling(orderId=null) {
  pollingPaused = true;
  if (orderId) activeEditOrderId = Number(orderId);
  if (editReleaseTimer) clearTimeout(editReleaseTimer);
  editReleaseTimer = null;
  setEditState(activeEditOrderId ? `editando #${activeEditOrderId}` : "edição ativa");
}
function scheduleResumePolling(force=false) {
  if (editReleaseTimer) clearTimeout(editReleaseTimer);
  editReleaseTimer = setTimeout(() => {
    if (!force && (elementIsEditing() || hasDirtyDrafts())) {
      pollingPaused = true;
      setEditState(activeEditOrderId ? `editando #${activeEditOrderId}` : "edição ativa");
      return;
    }
    pollingPaused = false;
    activeEditOrderId = null;
    setEditState("sincronizado");
  }, force ? 0 : 900);
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
}
function toast(title, subtitle = "") {
  const wrap = $("#toastWrap");
  if (!wrap) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<strong>${escapeHtml(title)}</strong>${subtitle ? `<small>${escapeHtml(subtitle)}</small>` : ""}`;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(-6px)"; }, 2600);
  setTimeout(() => el.remove(), 3200);
}
function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 920; g.gain.value = .03;
    o.start(); o.stop(ctx.currentTime + .08);
  } catch (e) {}
}
function statusBadge(status) {
  const s = (status || "").toLowerCase();
  if (s.includes("pronto") || s.includes("entregue") || s.includes("confirm")) return "ok";
  if (s.includes("preparo") || s.includes("inform") || s.includes("parcial")) return "warn";
  if (s.includes("cancel") || s.includes("rejeit") || s.includes("pendente")) return "bad";
  return "";
}
const payBadge = statusBadge;
function moneyBR(cents) { return `R$ ${(Number(cents || 0) / 100).toFixed(2).replace('.', ',')}`; }
function fmtTime(isoZ) {
  try {
    const d = new Date(isoZ);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  } catch (e) { return ""; }
}
function waitLabel(mins) {
  const n = Number(mins || 0);
  if (!n) return "agora";
  if (n < 60) return `${n} min`;
  const h = Math.floor(n / 60), m = n % 60;
  return m ? `${h}h ${m}min` : `${h}h`;
}
function waitTone(mins) {
  const n = Number(mins || 0);
  if (n >= 30) return "bad";
  if (n >= 15) return "warn";
  return "";
}
function ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function debounce(fn, ms = 250) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function bootstrapEvents() {
  if (eventsBootstrapped) return;
  const res = await fetch(`/api/events?latest=1`);
  if (res.ok) {
    const data = await res.json();
    const serverLast = Number(data.last_id || 0);
    if (!lastEventId || serverLast < lastEventId) {
      lastEventId = serverLast;
      localStorage.setItem(LS_LAST_EVENT_ID, String(lastEventId));
    }
  }
  eventsBootstrapped = true;
}
async function pollEvents() {
  if (document.hidden) return;
  await bootstrapEvents();
  const res = await fetch(`/api/events?since=${lastEventId}`);
  if (!res.ok) return;
  const data = await res.json();
  (data.events || []).forEach(ev => {
    if (ev.type === "new_order") { beep(); toast("Novo pedido", `#${ev.payload}`); }
    if (ev.type === "table_request") { beep(); toast("Nova solicitação", `#${ev.payload}`); }
    if (ev.type === "reservation_created") { toast("Nova reserva", ev.payload); }
    if (ev.type === "pix_reported") { beep(); toast("Pix informado", `Pagamento #${ev.payload}`); }
    if (ev.type === "pix_confirmed") { toast("Pix confirmado", `Pagamento #${ev.payload}`); }
    if (ev.type === "pix_rejected") { toast("Pix rejeitado", `Pagamento #${ev.payload}`); }
    if (ev.type === "manual_payment") { toast("Pagamento manual", `#${ev.payload}`); }
    if (ev.type === "table_closed") { toast("Mesa fechada", `Mesa ${ev.payload}`); }
  });
  lastEventId = data.last_id || lastEventId;
  localStorage.setItem(LS_LAST_EVENT_ID, String(lastEventId));
}

// ---- Orders ----
async function fetchOrders() {
  const status = $("#filterStatus")?.value || "Todos";
  const q = $("#filterQ")?.value || "";
  const sort = $("#orderSort")?.value || "oldest";
  const res = await fetch(`/api/admin/orders?status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.orders || [];
}
function updateOrderStats(orders) {
  const active = orders.length;
  const count = status => orders.filter(o => o.status === status).length;
  const tables = new Set(orders.map(o => o.table_no)).size;
  if ($("#statActive")) $("#statActive").textContent = active;
  if ($("#statNew")) $("#statNew").textContent = count("Novo");
  if ($("#statPrep")) $("#statPrep").textContent = count("Em preparo");
  if ($("#statTables")) $("#statTables").textContent = tables;
}
function renderOrders(orders) {
  const wrap = $("#ordersList");
  if (!wrap) return;
  if (adminUiBusy()) { setEditState(activeEditOrderId ? `editando #${activeEditOrderId}` : "edição ativa"); return; }
  const openIds = new Set($$(".order-card.open", wrap).map(el => Number(el.dataset.id)));
  wrap.innerHTML = "";
  updateOrderStats(orders);
  if (!orders.length) {
    wrap.innerHTML = `<div class="helper-list">Sem pedidos ativos no momento.</div>`;
    return;
  }
  orders.forEach(o => {
    const draft = getDraft(o.id) || {};
    const card = document.createElement("article");
    card.className = `order-card ${compactMode ? 'compact' : ''}`;
    card.dataset.id = o.id;
    if (openIds.has(o.id) && !compactMode) card.classList.add("open");
    const waitCls = waitTone(o.wait_minutes);
    card.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>#${o.id} • Mesa ${escapeHtml(o.table_no)}</strong>
          <div class="meta-row">
            <small>${fmtTime(o.created_at)} • ${o.items.length} itens • ${escapeHtml(o.money_total)}</small>
            <span class="mini-badge ${waitCls}">espera ${escapeHtml(waitLabel(o.wait_minutes))}</span>
          </div>
        </div>
        <div class="order-card__tools">
          <span class="status-badge ${statusBadge(o.status)}">${escapeHtml(o.status || 'Novo')}</span>
          <button class="icon-btn" data-act="toggle" type="button">▾</button>
        </div>
      </div>
      <div class="order-card__details">
        <div class="detail-grid">
          <div class="detail-stack">
            <div>
              <label>Status</label>
              <select data-field="status">${["Novo", "Em preparo", "Pronto", "Entregue", "Cancelado"].map(s => `<option ${s === (draft.status ?? o.status) ? "selected" : ""}>${s}</option>`).join("")}</select>
            </div>
            <div>
              <label>Mensagem para o cliente</label>
              <textarea data-field="msg" placeholder="Ex.: seu pedido sai em 3 minutos 🙂">${escapeHtml((draft.msg ?? o.admin_message) || "")}</textarea>
            </div>
          </div>
          <div class="detail-stack">
            <div><label>Tempo estimado (min)</label><input data-field="eta" inputmode="numeric" value="${draft.eta ?? (o.eta_minutes ?? "")}"/></div>
            <div><label>Total</label><input value="${escapeHtml(o.money_total)}" disabled/></div>
            <div><label>Entrada</label><input value="${escapeHtml(o.created_br || '')}" disabled/></div>
          </div>
        </div>
        <div class="order-items">
          ${o.items.map(it => `<div class="order-item"><div><strong>${escapeHtml(it.qty)}x</strong> ${escapeHtml(it.name)}</div><small>${moneyBR(it.qty * it.price_cents)}</small></div>`).join("")}
        </div>
        ${o.note ? `<div class="order-note"><strong>Observação:</strong> ${escapeHtml(o.note)}</div>` : ""}
        <div class="order-actions">
          <div class="order-actions__group">
            <button class="btn primary" data-act="save" type="button">Salvar</button>
            <button class="btn ghost" data-act="print" type="button">Imprimir cozinha</button>
          </div>
          <button class="btn ghost" data-act="close" type="button">Arquivar pedido</button>
        </div>
      </div>`;
    wrap.appendChild(card);
  });
}
async function updateOrder(id, payload) {
  const res = await fetch(`/api/admin/orders/${id}/update`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { toast("Não salvou", data.error || "Tenta de novo."); return false; }
  clearDraft(id);
  pollingPaused = false;
  activeEditOrderId = null;
  scheduleResumePolling(true);
  toast("Atualizado", `Pedido #${id}`);
  return true;
}
async function closeOrder(id) {
  const res = await fetch(`/api/admin/orders/${id}/close`, { method: "POST" });
  if (!res.ok) { toast("Ops", "Não foi possível arquivar."); return false; }
  clearDraft(id);
  pollingPaused = false;
  activeEditOrderId = null;
  scheduleResumePolling(true);
  toast("Arquivado", `Pedido #${id}`);
  return true;
}
async function refreshOrdersPage() {
  await pollEvents();
  if (adminUiBusy()) return;
  renderOrders(await fetchOrders());
}
function bindOrdersPage() {
  if ($("#compactToggle")) $("#compactToggle").checked = compactMode;
  localStorage.setItem("admin_compact", compactMode ? "1" : "0");
  $("#compactToggle")?.addEventListener("change", e => {
    compactMode = !!e.target.checked;
    localStorage.setItem("admin_compact", compactMode ? "1" : "0");
    refreshOrdersPage();
  });
  ["#filterStatus", "#orderSort"].forEach(sel => $(sel)?.addEventListener("change", refreshOrdersPage));
  $("#filterQ")?.addEventListener("input", debounce(refreshOrdersPage, 180));
  document.addEventListener("focusin", e => { const card=e.target.closest?.(".order-card"); if (card) pausePolling(Number(card.dataset.id)); });
  document.addEventListener("focusout", e => { if (e.target.closest?.(".order-card")) scheduleResumePolling(); });
  document.addEventListener("change", e => {
    const card = e.target.closest?.(".order-card");
    if (!card) return;
    pausePolling(Number(card.dataset.id));
    const id = Number(card.dataset.id);
    const field = e.target.dataset.field;
    if (!field || !id) return;
    if (field === "status") setDraft(id, { status: e.target.value });
    if (field === "eta") setDraft(id, { eta: e.target.value });
    if (field === "msg") setDraft(id, { msg: e.target.value });
  });
  document.addEventListener("input", e => {
    const card = e.target.closest?.(".order-card");
    if (!card) return;
    pausePolling(Number(card.dataset.id));
    const id = Number(card.dataset.id);
    const field = e.target.dataset.field;
    if (!field || !id) return;
    if (field === "status") setDraft(id, { status: e.target.value });
    if (field === "eta") setDraft(id, { eta: e.target.value });
    if (field === "msg") setDraft(id, { msg: e.target.value });
  });
  $("#ordersList")?.addEventListener("click", async e => {
    const card = e.target.closest(".order-card"); if (!card) return;
    const act = e.target.closest("button")?.dataset.act; if (!act) return;
    const id = Number(card.dataset.id);
    if (act === "toggle") { card.classList.toggle("open"); return; }
    if (act === "print") { window.open(`/admin/print/order/${id}`, "_blank"); return; }
    if (act === "close") { if (await closeOrder(id)) await refreshOrdersPage(); return; }
    if (act === "save") {
      const status = card.querySelector('[data-field="status"]').value;
      const eta = card.querySelector('[data-field="eta"]').value;
      const msg = card.querySelector('[data-field="msg"]').value;
      if (await updateOrder(id, { status, eta_minutes: eta, admin_message: msg })) await refreshOrdersPage();
    }
  });
  setEditState(hasDirtyDrafts() ? "edição ativa" : "sincronizado");
  (async function loop() { await refreshOrdersPage(); setTimeout(loop, adminUiBusy() ? 9000 : 6500); })();
}

// ---- Requests ----
async function fetchRequests() {
  const res = await fetch('/api/admin/table_requests');
  if (!res.ok) return [];
  const data = await res.json();
  return data.requests || [];
}
function renderRequests(rows) {
  const wrap = $("#requestsList"); if (!wrap) return;
  wrap.innerHTML = '';
  if (!rows.length) { wrap.innerHTML = `<div class="helper-list">Sem solicitações no momento.</div>`; return; }
  rows.forEach(r => {
    const card = document.createElement('article');
    card.className = 'order-card compact open';
    card.dataset.id = r.id;
    card.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>Mesa ${escapeHtml(r.table_no)} • ${escapeHtml(r.req_type)}</strong>
          <div class="meta-row"><small>${fmtTime(r.created_at)} ${r.note ? `• ${escapeHtml(r.note)}` : ''}</small><span class="mini-badge warn">novo</span></div>
        </div>
        <div class="order-card__tools"><button class="btn primary btn-xs" data-act="resolve" type="button">Resolver</button></div>
      </div>`;
    wrap.appendChild(card);
  });
}
async function resolveRequest(id) {
  const res = await fetch(`/api/admin/table_requests/${id}/resolve`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    toast('Resolvido', `Chamado #${id}`);
    return data;
  }
  toast('Ops', data.error || 'Não foi possível concluir.');
  return null;
}
function bindRequestsPage() {
  const refresh = async () => { await pollEvents(); renderRequests(await fetchRequests()); };
  $("#requestsList")?.addEventListener('click', async e => {
    const card = e.target.closest('.order-card'); if (!card) return;
    const act = e.target.closest('button')?.dataset.act; if (act !== 'resolve') return;
    const data = await resolveRequest(Number(card.dataset.id));
    if (data?.redirect_url) {
      location.href = data.redirect_url;
      return;
    }
    refresh();
  });
  (async function loop() { await refresh(); setTimeout(loop, document.hidden ? 8000 : 4200); })();
}

// ---- Reservations ----
async function fetchReservations(start, end) {
  const qs = new URLSearchParams({ start, end });
  const res = await fetch(`/api/admin/reservations?${qs.toString()}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.reservations || [];
}
function renderCalendar(month, reservations) {
  const label = $("#calLabel");
  if (label) label.textContent = month.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
  const grid = $("#calGrid");
  if (!grid) return;
  grid.innerHTML = '';
  const start = new Date(month.getFullYear(), month.getMonth(), 1);
  const end = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  const offset = (start.getDay() + 6) % 7;
  const totalCells = Math.ceil((offset + end.getDate()) / 7) * 7;
  for (let i = 0; i < totalCells; i++) {
    const d = new Date(month.getFullYear(), month.getMonth(), i - offset + 1);
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'calendar-cell';
    if (d.getMonth() !== month.getMonth()) cell.classList.add('is-muted');
    const dayKey = ymd(d);
    const count = reservations.filter(r => (r.starts_at || '').startsWith(dayKey)).length;
    cell.dataset.day = dayKey;
    cell.innerHTML = `<strong>${d.getDate()}</strong><small>${count ? `${count} reserva(s)` : '—'}</small>`;
    grid.appendChild(cell);
  }
}
function renderReservationsList(day, rows) {
  const wrap = $("#resList"); if (!wrap) return;
  wrap.innerHTML = '';
  if (!rows.length) { wrap.innerHTML = `<div class="helper-list">Sem reservas para ${day.split('-').reverse().join('/')}.</div>`; return; }
  rows.forEach(r => {
    const card = document.createElement('article');
    card.className = 'order-card compact open';
    card.dataset.code = r.code;
    card.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>${escapeHtml(r.name)} • ${escapeHtml(r.party_size)} pessoas</strong>
          <div class="meta-row"><small>${escapeHtml(r.starts_at.replace('T', ' '))} • ${escapeHtml(r.phone)}</small><span class="mini-badge ${statusBadge(r.status)}">${escapeHtml(r.status)}</span></div>
        </div>
      </div>
      <div class="order-card__details" style="display:block">
        <div class="order-actions">
          <div class="order-actions__group">
            <button class="btn primary btn-xs" data-act="confirm" type="button">Confirmar</button>
            <button class="btn ghost btn-xs" data-act="pend" type="button">Pendente</button>
            <button class="btn ghost btn-xs" data-act="cancel" type="button">Cancelar</button>
          </div>
        </div>
      </div>`;
    wrap.appendChild(card);
  });
}
async function setReservationStatus(code, status) {
  const res = await fetch(`/api/admin/reservations/${encodeURIComponent(code)}/set_status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
  if (res.ok) { toast('Reserva atualizada', `${code} → ${status}`); return true; }
  toast('Ops', 'Não foi possível atualizar.'); return false;
}
function bindReservationsPage() {
  let month = new Date(); month.setDate(1);
  let selectedDay = ymd(new Date());
  async function refresh() {
    await pollEvents();
    const start = new Date(month.getFullYear(), month.getMonth(), 1);
    const end = new Date(month.getFullYear(), month.getMonth() + 1, 0);
    const rows = await fetchReservations(ymd(start), ymd(end));
    renderCalendar(month, rows);
    renderReservationsList(selectedDay, rows.filter(r => (r.starts_at || '').startsWith(selectedDay)));
  }
  $("#calPrev")?.addEventListener('click', () => { month = new Date(month.getFullYear(), month.getMonth() - 1, 1); refresh(); });
  $("#calNext")?.addEventListener('click', () => { month = new Date(month.getFullYear(), month.getMonth() + 1, 1); refresh(); });
  $("#calGrid")?.addEventListener('click', e => {
    const btn = e.target.closest('.calendar-cell'); if (!btn || btn.classList.contains('is-muted')) return;
    selectedDay = btn.dataset.day; refresh();
  });
  $("#resList")?.addEventListener('click', async e => {
    const card = e.target.closest('.order-card'); if (!card) return;
    const code = card.dataset.code; const act = e.target.closest('button')?.dataset.act; if (!act) return;
    if (act === 'confirm') await setReservationStatus(code, 'Confirmada');
    if (act === 'pend') await setReservationStatus(code, 'Pendente');
    if (act === 'cancel') await setReservationStatus(code, 'Cancelada');
    refresh();
  });
  (async function loop() { await refresh(); setTimeout(loop, document.hidden ? 12000 : 8500); })();
}

// ---- Comandas ----
function groupOrdersByTable(orders) {
  const by = {};
  orders.forEach(o => {
    const key = o.table_no;
    if (!by[key]) by[key] = { table_no: o.table_no, orders: [], total_cents: 0, oldest_at: o.created_at, newest_at: o.created_at };
    by[key].orders.push(o);
    by[key].total_cents += Number(o.total_cents || 0);
    if ((o.created_at || '') < (by[key].oldest_at || '')) by[key].oldest_at = o.created_at;
    if ((o.created_at || '') > (by[key].newest_at || '')) by[key].newest_at = o.created_at;
  });
  return Object.values(by);
}
function filterSortTables(tables) {
  const q = ($("#tableSearch")?.value || '').trim().toLowerCase();
  const sort = $("#tableSort")?.value || 'table';
  let out = tables.filter(t => !q || String(t.table_no).toLowerCase().includes(q) || t.orders.some(o => String(o.id).includes(q)));
  if (sort === 'oldest') out.sort((a, b) => (a.oldest_at || '').localeCompare(b.oldest_at || ''));
  else if (sort === 'newest') out.sort((a, b) => (b.newest_at || '').localeCompare(a.newest_at || ''));
  else if (sort === 'highest' || sort === 'due') out.sort((a, b) => b.total_cents - a.total_cents);
  else out.sort((a, b) => String(a.table_no).localeCompare(String(b.table_no)));
  return out;
}
function renderComandas(tables) {
  const wrap = $("#tablesList"); if (!wrap) return;
  wrap.innerHTML = "";
  if (!tables.length) { wrap.innerHTML = `<div class="helper-list">Sem mesas com pedidos ativos.</div>`; return; }
  tables.forEach(t => {
    const waits = t.orders.map(o => Number(o.wait_minutes || 0));
    const maxWait = Math.max(...waits, 0);
    const el = document.createElement('article');
    el.className = 'order-card open compact';
    el.dataset.table = t.table_no;
    el.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>Mesa ${escapeHtml(t.table_no)}</strong>
          <div class="meta-row"><small>${t.orders.length} pedido(s) • ${moneyBR(t.total_cents)}</small><span class="mini-badge ${waitTone(maxWait)}">espera ${waitLabel(maxWait)}</span></div>
        </div>
        <div class="order-card__tools">
          <button class="btn ghost btn-xs" data-act="print" type="button">Imprimir</button>
          <button class="btn primary btn-xs" data-act="close" type="button">Fechar mesa</button>
        </div>
      </div>
      <div class="order-card__details" style="display:block">
        ${t.orders.slice(0, 6).map(o => `<div class="order-item"><div>Pedido #${o.id} • ${escapeHtml(o.status)}</div><small>${fmtTime(o.created_at)} • ${moneyBR(o.total_cents)}</small></div>`).join('')}
        ${t.orders.length > 6 ? `<div class="helper-list">+${t.orders.length - 6} pedidos</div>` : ''}
      </div>`;
    wrap.appendChild(el);
  });
}
async function closeTable(tableNo) {
  const res = await fetch(`/api/admin/tables/${encodeURIComponent(tableNo)}/close`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { toast('Ops', data.error || 'Não foi possível fechar.'); return false; }
  toast('Mesa fechada', `${tableNo} • ${data.receipt_code || ''}`.trim());
  return true;
}
function bindComandasPage() {
  const qs = new URLSearchParams(location.search);
  if (qs.get('table') && $("#tableSearch")) $("#tableSearch").value = qs.get('table');
  const refresh = async () => { await pollEvents(); renderComandas(filterSortTables(groupOrdersByTable(await fetchOrders()))); };
  $("#tablesRefresh")?.addEventListener('click', refresh);
  $("#tableSearch")?.addEventListener('input', debounce(refresh, 180));
  $("#tableSort")?.addEventListener('change', refresh);
  $("#tablesList")?.addEventListener('click', async e => {
    const card = e.target.closest('.order-card'); if (!card) return;
    const act = e.target.closest('button')?.dataset.act; const table = card.dataset.table;
    if (act === 'print') window.open(`/admin/print/table/${encodeURIComponent(table)}`, '_blank');
    if (act === 'close') { await closeTable(table); refresh(); }
  });
  (async function loop() { await refresh(); setTimeout(loop, adminUiBusy() ? 11000 : 8000); })();
}

// ---- Payments ----
async function fetchPayments() {
  const params = new URLSearchParams({
    status: $("#payStatus")?.value || 'TODOS',
    table: $("#payTable")?.value || '',
    q: $("#payQuery")?.value || '',
    method: $("#payMethod")?.value || 'TODOS',
    sort: $("#paySort")?.value || 'newest',
  });
  const res = await fetch(`/api/admin/payments?${params.toString()}`);
  if (!res.ok) return { payments: [], stats: null };
  return await res.json();
}
function renderPayments(rows, stats) {
  const wrap = $("#paymentsList"); if (!wrap) return;
  wrap.innerHTML = '';
  if (stats) {
    $("#payStatConfirmed").textContent = stats.today_confirmed || 'R$ 0,00';
    $("#payStatPending").textContent = String(stats.today_pending || 0);
    $("#payStatReported").textContent = String(stats.today_reported || 0);
    $("#payStatCount").textContent = String(stats.today_count || 0);
  }
  const today = new Date().toISOString().slice(0, 10);
  const pdfDay = $("#payPdfDay"); if (pdfDay) pdfDay.href = `/admin/pdf/pagamentos?date=${encodeURIComponent(today)}`;
  const csvDay = $("#payCsvDay"); if (csvDay) csvDay.href = `/admin/csv/pagamentos?date=${encodeURIComponent(today)}`;
  if (!rows.length) { wrap.innerHTML = `<div class="helper-list">Sem pagamentos para este filtro.</div>`; return; }
  const methodOptions = [
    ['DINHEIRO','Dinheiro'],
    ['CARTAO','Cartão'],
    ['PIX_QR','Pix'],
    ['OUTRO','Outro'],
  ];
  rows.forEach(p => {
    const card = document.createElement('article');
    card.className = 'order-card open compact';
    card.dataset.id = p.id;
    const draft = getPaymentDraft(`pay-${p.id}`) || {};
    const currentMethod = (draft.method ?? ((p.method === 'CAIXA' || !p.method) ? '' : p.method));
    const methodSelect = `<select data-field="method" ${p.status === 'CONFIRMADO' ? 'disabled' : ''}>`
      + `<option value="">Selecionar no caixa</option>`
      + methodOptions.map(([v,label]) => `<option value="${v}" ${v===currentMethod ? 'selected' : ''}>${label}</option>`).join('')
      + `</select>`;
    const pdfBtn = p.receipt_enabled ? `<a class="btn ghost btn-xs" href="/admin/pdf/pagamento/${p.id}" target="_blank">Comprovante PDF</a>` : `<span class="admin-chip">libera após confirmar</span>`;
    card.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>${escapeHtml(p.ref_code || ('#' + p.id))} • Mesa ${escapeHtml(p.table_no)}</strong>
          <div class="meta-row"><small>${escapeHtml(p.created_br || fmtTime(p.created_at))} • ${escapeHtml((currentMethod || 'CAIXA').replace('_',' '))} • ${escapeHtml(p.money_amount)}</small><span class="mini-badge ${payBadge(p.status)}">${escapeHtml(p.status)}</span></div>
        </div>
        <div class="order-card__tools">${pdfBtn}</div>
      </div>
      <div class="order-card__details" style="display:block">
        <div class="detail-grid detail-grid--wide">
          <div class="detail-stack">
            <div><label>Referência</label><input value="${escapeHtml(p.ref_code || '-')}" disabled/></div>
            <div><label>TXID</label><input value="${escapeHtml(p.pix_txid || '-')}" disabled/></div>
            <div><label>Obs do caixa</label><input data-field="note" placeholder="Ex.: pago no balcão 12:34" value="${escapeHtml(draft.note ?? (p.admin_note || ''))}"/></div>
          </div>
          <div class="detail-stack">
            <div><label>Comanda</label><input value="${escapeHtml(p.receipt_code || '-')}" disabled/></div>
            <div><label>Forma de pagamento</label>${methodSelect}</div>
            <div><label>Confirmado em</label><input value="${escapeHtml(p.confirmed_br || '-')}" disabled/></div>
          </div>
        </div>
        <div class="order-actions">
          <div class="order-actions__group">
            <button class="btn primary btn-xs" data-act="confirm" type="button" ${p.status === 'CONFIRMADO' ? 'disabled' : ''}>Confirmar no caixa</button>
            <button class="btn ghost btn-xs" data-act="reject" type="button" ${p.status === 'CONFIRMADO' ? 'disabled' : ''}>Rejeitar</button>
          </div>
        </div>
      </div>`;
    wrap.appendChild(card);
  });
}
async function confirmPayment(id, note, method) {
  const res = await fetch(`/api/admin/payments/${id}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note, method }) });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    toast('Confirmado', data.receipt_url ? 'Comprovante liberado' : `Pagamento #${id}`);
    return true;
  }
  toast('Ops', data.error || 'Não deu pra confirmar.'); return false;
}
async function rejectPayment(id, note) {
  const res = await fetch(`/api/admin/payments/${id}/reject`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
  if (res.ok) { toast('Rejeitado', `Pagamento #${id}`); return true; }
  toast('Ops', 'Não deu pra rejeitar.'); return false;
}
function bindPaymentsPage() {
  const qs = new URLSearchParams(location.search);
  if(qs.get("table") && $("#payTable")) $("#payTable").value = qs.get("table");
  const manualKey = "manual";

  function captureManualDraft(){
    setPaymentDraft(manualKey, {
      table_no: $("#mTable")?.value || '',
      method: $("#mMethod")?.value || 'DINHEIRO',
      amount: $("#mAmount")?.value || '',
      note: $("#mNote")?.value || ''
    });
  }
  function applyManualDraft(){
    const d = getPaymentDraft(manualKey) || {};
    if($("#mTable") && d.table_no !== undefined) $("#mTable").value = d.table_no;
    if($("#mMethod") && d.method) $("#mMethod").value = d.method;
    if($("#mAmount") && d.amount !== undefined) $("#mAmount").value = d.amount;
    if($("#mNote") && d.note !== undefined) $("#mNote").value = d.note;
  }

  const refresh = async () => {
    await pollEvents();
    if (adminUiBusy()) return;
    const data = await fetchPayments();
    renderPayments(data.payments || [], data.stats || null);
    applyManualDraft();
  };

  ["#payStatus", "#payMethod", "#paySort"].forEach(sel => $(sel)?.addEventListener('change', refresh));
  $("#payRefresh")?.addEventListener('click', refresh);
  $("#payTable")?.addEventListener('input', debounce(refresh, 180));
  $("#payQuery")?.addEventListener('input', debounce(refresh, 180));

  document.addEventListener('focusin', e => {
    if (e.target.closest?.('#paymentsList, .panel')) pausePolling();
  });
  document.addEventListener('focusout', e => {
    if (e.target.closest?.('#paymentsList, .panel')) scheduleResumePolling();
  });
  document.addEventListener('input', e => {
    const card = e.target.closest?.('#paymentsList .order-card');
    if (card) {
      pausePolling();
      const id = Number(card.dataset.id);
      const field = e.target.dataset.field;
      if (id && field) setPaymentDraft(`pay-${id}`, { [field]: e.target.value });
      return;
    }
    if (e.target.closest?.('.panel')) {
      pausePolling();
      if (["mTable","mMethod","mAmount","mNote"].includes(e.target.id)) captureManualDraft();
    }
  });
  document.addEventListener('change', e => {
    const card = e.target.closest?.('#paymentsList .order-card');
    if (card) {
      pausePolling();
      const id = Number(card.dataset.id);
      const field = e.target.dataset.field;
      if (id && field) setPaymentDraft(`pay-${id}`, { [field]: e.target.value });
      return;
    }
    if (e.target.closest?.('.panel') && ["mTable","mMethod","mAmount","mNote"].includes(e.target.id)) captureManualDraft();
  });

  $("#mSave")?.addEventListener('click', async () => {
    const table = $("#mTable")?.value.trim();
    const method = $("#mMethod")?.value || 'DINHEIRO';
    const raw = ($("#mAmount")?.value || '').trim();
    const note = ($("#mNote")?.value || '').trim();
    if (!table) { toast('Mesa', 'Preencha a mesa.'); return; }
    let s = raw;
    if (s.includes(',') && s.includes('.')) s = s.replace(/\./g, '').replace(',', '.');
    else if (s.includes(',')) s = s.replace(',', '.');
    const cents = Math.round(Number(s) * 100);
    if (!cents || cents <= 0) { toast('Valor', 'Informe um valor válido.'); return; }
    const res = await fetch('/api/admin/payments/manual', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ table_no: table, method, amount_cents: cents, note }) });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      toast('Registrado', `${data.ref_code || method} • Mesa ${table}`);
      clearPaymentDraft(manualKey);
      if($("#mAmount")) $("#mAmount").value='';
      if($("#mNote")) $("#mNote").value='';
      scheduleResumePolling(true);
      refresh();
    } else toast('Ops', data.error || 'Não foi possível registrar.');
  });
  $("#paymentsList")?.addEventListener('click', async e => {
    const card = e.target.closest('.order-card'); if (!card) return;
    const act = e.target.closest('button')?.dataset.act; if (!act) return;
    const id = Number(card.dataset.id);
    const note = card.querySelector('[data-field="note"]').value;
    const method = card.querySelector('[data-field="method"]')?.value || '';
    if (act === 'confirm') {
      if (!method) { toast('Forma de pagamento', 'Selecione como foi pago no caixa.'); return; }
      if (await confirmPayment(id, note, method)) { clearPaymentDraft(`pay-${id}`); scheduleResumePolling(true); }
    }
    if (act === 'reject') { if (await rejectPayment(id, note)) { clearPaymentDraft(`pay-${id}`); scheduleResumePolling(true); } }
    refresh();
  });
  applyManualDraft();
  (async function loop() { await refresh(); setTimeout(loop, adminUiBusy() ? 9000 : 6000); })();
}

// ---- History ----
async function fetchHistory(dateStr) {
  const params = new URLSearchParams({
    date: dateStr || '',
    q: $("#histQuery")?.value || '',
    pay_status: $("#histPayStatus")?.value || 'TODOS',
    sort: $("#histSort")?.value || 'newest',
  });
  const res = await fetch(`/api/admin/history?${params.toString()}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.tabs || [];
}
function renderHistory(rows, dateStr) {
  const wrap = $("#historyList"); if (!wrap) return;
  wrap.innerHTML = '';
  if (!rows.length) { wrap.innerHTML = `<div class="helper-list">Sem comandas fechadas aqui.</div>`; return; }
  rows.forEach(t => {
    const el = document.createElement('article');
    el.className = 'order-card open compact';
    el.innerHTML = `
      <div class="order-card__summary">
        <div class="order-card__meta">
          <strong>${escapeHtml(t.receipt_code || ('CMD-' + t.id))} • Mesa ${escapeHtml(t.table_no)}</strong>
          <div class="meta-row"><small>${escapeHtml(t.closed_br || fmtTime(t.closed_at))} • Total ${escapeHtml(t.money_total)} • Pago ${escapeHtml(t.money_paid)}</small><span class="mini-badge ${statusBadge(t.pay_status)}">${escapeHtml(t.pay_status)}</span></div>
        </div>
        <div class="order-card__tools"><a class="btn ghost btn-xs" href="/admin/pdf/comanda/${t.id}" target="_blank">PDF</a></div>
      </div>
      <div class="order-card__details" style="display:block"><div class="helper-list compact"><div>Comanda #${t.id} • Recibo ${escapeHtml(t.receipt_code || ('CMD-' + t.id))}</div></div></div>`;
    wrap.appendChild(el);
  });
  const useDate = dateStr || new Date().toISOString().slice(0, 10);
  const pdf = $("#dailyPdf");
  if (pdf) pdf.href = `/admin/pdf/fechamento?date=${encodeURIComponent(useDate)}`;
  const csv = $("#histCsv");
  if (csv) csv.href = `/admin/csv/historico?date=${encodeURIComponent(useDate)}`;
}
function bindHistoryPage() {
  const input = $("#histDate");
  const today = new Date().toISOString().slice(0, 10);
  if (input) input.value = today;
  const refresh = async () => { await pollEvents(); const d = input?.value || ''; renderHistory(await fetchHistory(d), d); };
  $("#histRefresh")?.addEventListener('click', refresh);
  $("#histToday")?.addEventListener('click', () => { if (input) input.value = today; refresh(); });
  $("#histQuery")?.addEventListener('input', debounce(refresh, 180));
  $("#histPayStatus")?.addEventListener('change', refresh);
  $("#histSort")?.addEventListener('change', refresh);
  (async function loop() { await refresh(); setTimeout(loop, adminUiBusy() ? 11000 : 9000); })();
}

// ---- Reports ----
async function fetchDailyReport(dateStr) {
  const res = await fetch(`/api/admin/reports/daily?date=${encodeURIComponent(dateStr)}`);
  if (!res.ok) return null;
  return await res.json();
}
function bindReportsPage() {
  const input = $("#repDate");
  const today = new Date().toISOString().slice(0, 10);
  if (input) input.value = today;
  const apply = async () => {
    await pollEvents();
    const d = input?.value || today;
    const data = await fetchDailyReport(d);
    if (!data) return;
    $("#repTotal").textContent = data.money_total;
    $("#repPaid").textContent = data.money_paid;
    $("#repCount").textContent = String(data.tabs_count || 0);
    $("#repAvg").textContent = data.avg_ticket || 'R$ 0,00';
    $("#repPendingTabs").textContent = String(data.pending_tabs || 0);
    $("#repDateLabel").textContent = d;
    const box = $("#repBreakdown");
    if (box) {
      const b = data.breakdown || {};
      box.innerHTML = Object.keys(b).length ? Object.entries(b).map(([k, v]) => `<div><strong>${escapeHtml(k)}</strong> — ${escapeHtml(v)}</div>`).join('') : '<div>Sem pagamentos confirmados no período.</div>';
    }
    const link = `/admin/pdf/fechamento?date=${encodeURIComponent(d)}`;
    const payLink = `/admin/pdf/pagamentos?date=${encodeURIComponent(d)}`;
    const csvHistory = `/admin/csv/historico?date=${encodeURIComponent(d)}`;
    const csvPayments = `/admin/csv/pagamentos?date=${encodeURIComponent(d)}`;
    $("#repPdf").href = link; $("#repPdf2").href = link;
    $("#repPayPdf").href = payLink; $("#repPayPdf2").href = payLink;
    if ($("#repCsvHistory")) $("#repCsvHistory").href = csvHistory;
    if ($("#repCsvPayments")) $("#repCsvPayments").href = csvPayments;
  };
  $("#repLoad")?.addEventListener('click', apply);
  apply();
}

// ---- Settings ----
function bindSettingsPage() {
  const previewImg = $("#logoPreviewImg");
  const placeholder = $("#logoPreviewPlaceholder");
  const fileName = $("#logoFileName");
  function updateLogoPreview(dataUri, name='') {
    if (dataUri) {
      if (previewImg) { previewImg.src = dataUri; previewImg.style.display = 'block'; }
      if (placeholder) placeholder.style.display = 'none';
      if (fileName) fileName.textContent = name || 'Logo salva';
    } else {
      if (previewImg) { previewImg.removeAttribute('src'); previewImg.style.display = 'none'; }
      if (placeholder) placeholder.style.display = 'flex';
      if (fileName) fileName.textContent = 'Nenhum arquivo enviado';
    }
  }
  $("#saveSettings")?.addEventListener('click', async () => {
    const payload = {
      restaurant_name: $("#s_restaurant_name")?.value || "",
      restaurant_tagline: $("#s_restaurant_tagline")?.value || "",
      client_primary: $("#s_client_primary")?.value || "",
      pix_key: $("#s_pix_key")?.value || "",
      pix_merchant_name: $("#s_pix_merchant_name")?.value || "",
      pix_merchant_city: $("#s_pix_merchant_city")?.value || "",
      pix_txid_prefix: $("#s_pix_txid_prefix")?.value || "",
      asaas_enabled: $("#s_asaas_enabled")?.value || "0",
      asaas_env: $("#s_asaas_env")?.value || "sandbox",
      asaas_api_key: $("#s_asaas_api_key")?.value || "",
      asaas_webhook_url: $("#s_asaas_webhook_url")?.value || "",
      asaas_webhook_token: $("#s_asaas_webhook_token")?.value || "",
      asaas_webhook_email: $("#s_asaas_webhook_email")?.value || "",
    };
    const res = await fetch('/api/admin/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (res.ok) toast('Salvo', 'Configurações atualizadas.');
    else toast('Ops', 'Não foi possível salvar.');
  });
  $("#logoFileInput")?.addEventListener('change', async e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('logo', file);
    const res = await fetch('/api/admin/settings/logo', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { updateLogoPreview(data.logo_data_uri, data.logo_file_name); toast('Logo salva', 'A nova marca já vale para os comprovantes.'); }
    else toast('Ops', data.error || 'Não foi possível enviar.');
    e.target.value = '';
  });
  $("#removeLogoBtn")?.addEventListener('click', async () => {
    const res = await fetch('/api/admin/settings/logo/delete', { method: 'POST' });
    if (res.ok) { updateLogoPreview('', ''); toast('Logo removida', 'O sistema voltou ao cabeçalho padrão.'); }
    else toast('Ops', 'Não foi possível remover.');
  });
  $("#registerAsaasWebhookBtn")?.addEventListener('click', async () => {
    const savePayload = {
      restaurant_name: $("#s_restaurant_name")?.value || "",
      restaurant_tagline: $("#s_restaurant_tagline")?.value || "",
      client_primary: $("#s_client_primary")?.value || "",
      pix_key: $("#s_pix_key")?.value || "",
      pix_merchant_name: $("#s_pix_merchant_name")?.value || "",
      pix_merchant_city: $("#s_pix_merchant_city")?.value || "",
      pix_txid_prefix: $("#s_pix_txid_prefix")?.value || "",
      asaas_enabled: $("#s_asaas_enabled")?.value || "0",
      asaas_env: $("#s_asaas_env")?.value || "sandbox",
      asaas_api_key: $("#s_asaas_api_key")?.value || "",
      asaas_webhook_url: $("#s_asaas_webhook_url")?.value || "",
      asaas_webhook_token: $("#s_asaas_webhook_token")?.value || "",
      asaas_webhook_email: $("#s_asaas_webhook_email")?.value || "",
    };
    await fetch('/api/admin/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(savePayload) });
    const res = await fetch('/api/admin/asaas/webhook/register', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      if ($("#s_asaas_webhook_url") && data.webhook_url) $("#s_asaas_webhook_url").value = data.webhook_url;
      if ($("#s_asaas_webhook_token") && data.auth_token) $("#s_asaas_webhook_token").value = data.auth_token;
      toast('Webhook cadastrado', 'O Asaas já pode avisar o MenuFlow sobre pagamentos Pix.');
    } else {
      toast('Ops', data.error || 'Não foi possível cadastrar o webhook no Asaas.');
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  if (window.MENUFLOW_VERSION) {
    const seenVersion = localStorage.getItem("menuflow_seen_version");
    if (seenVersion !== window.MENUFLOW_VERSION) {
      localStorage.setItem("menuflow_seen_version", window.MENUFLOW_VERSION);
      localStorage.removeItem(LS_LAST_EVENT_ID);
      sessionStorage.removeItem(DRAFT_STORAGE_KEY);
      sessionStorage.removeItem(PAYMENT_DRAFT_STORAGE_KEY);
      lastEventId = 0;
    }
  }
  const page = document.body.dataset.page;
  if (page === "orders") bindOrdersPage();
  if (page === "requests") bindRequestsPage();
  if (page === "reservas") bindReservationsPage();
  if (page === "comandas") bindComandasPage();
  if (page === "payments") bindPaymentsPage();
  if (page === "history") bindHistoryPage();
  if (page === "reports") bindReportsPage();
  if (page === "settings") bindSettingsPage();
});
