const $ = (sel, el=document) => el.querySelector(sel);
const $$ = (sel, el=document) => Array.from(el.querySelectorAll(sel));
const LS = { table:"mf_client_table", customer:"mf_client_customer", customerTaxId:"mf_client_customer_tax_id", order:"mf_client_order" };
const state = { cart:{}, activeOrderId:null, lastOrderSnapshot:null, pollTimer:null, table_no:"", customer_name:"", customer_tax_id:"", note:"", activeSheet:null, pix:{paymentId:null,payload:"",poll:null,autoConfirm:false,provider:"LOCAL_PIX"}, tableSummary:null };
function moneyBR(cents){ const n=(Number(cents||0)/100).toFixed(2).replace('.', ','); return `R$ ${n}`; }
function escapeHtml(s){ return String(s||"").replace(/[&<>"']/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m])); }
function cartTotals(){ let qty=0,cents=0; Object.values(state.cart).forEach(it=>{ qty += it.qty; cents += it.qty * it.price_cents; }); return {qty,cents}; }
function vibrate(ms=12){ try{ navigator.vibrate?.(ms); }catch(e){} }
function toast(title, subtitle=""){ const wrap=$("#toastWrap"); const el=document.createElement("div"); el.className="toast"; el.innerHTML=`<strong>${escapeHtml(title)}</strong>${subtitle?`<small>${escapeHtml(subtitle)}</small>`:""}`; wrap.appendChild(el); setTimeout(()=>{ el.style.opacity="0"; el.style.transform="translateY(-6px)"; },2600); setTimeout(()=>el.remove(),3100); }
function showPaidOverlay(){
  const ov = $("#paidOverlay");
  if(!ov) return;
  ov.classList.add("show");
  ov.setAttribute("aria-hidden","false");
  // Auto hide
  setTimeout(()=>{
    ov.classList.remove("show");
    ov.setAttribute("aria-hidden","true");
  }, 1400);
}
function setOverlay(id, show){ const el=$(id); if(!el) return; el.classList.toggle("show", show); }
function stopPixPolling(){ if(state.pix.poll){ clearInterval(state.pix.poll); state.pix.poll=null; } }
function openPixModal(){ setOverlay("#pixBack", true); }
function closePixModal(){ setOverlay("#pixBack", false); stopPixPolling(); }
function openSheet(name){ closeModal(); if(name==="cart"){ setOverlay("#drawerBack", true); $("#drawer").classList.add("open"); renderCartDrawer(); state.activeSheet="cart"; } if(name==="actions"){ setOverlay("#actionsBack", true); $("#sheet").classList.add("open"); state.activeSheet="actions"; } }
function closeSheet(name){ if(name==="cart"||!name){ setOverlay("#drawerBack", false); $("#drawer").classList.remove("open"); if(state.activeSheet==="cart") state.activeSheet=null; } if(name==="actions"||!name){ setOverlay("#actionsBack", false); $("#sheet").classList.remove("open"); if(state.activeSheet==="actions") state.activeSheet=null; } }
function renderCartMini(){ const {qty,cents}=cartTotals(); const show=qty>0; $("#cartFab").style.display=show?"flex":"none"; $("#cartFabQty").textContent=`${qty} ${qty===1?"item":"itens"}`; $("#cartFabTotal").textContent=moneyBR(cents); $("#cartCount").textContent=qty; }
function persistClientState(){ try{ if(state.table_no) localStorage.setItem(LS.table, state.table_no); else localStorage.removeItem(LS.table); if(state.customer_name) localStorage.setItem(LS.customer, state.customer_name); else localStorage.removeItem(LS.customer); if(state.customer_tax_id) localStorage.setItem(LS.customerTaxId, state.customer_tax_id); else localStorage.removeItem(LS.customerTaxId); if(state.activeOrderId) localStorage.setItem(LS.order, String(state.activeOrderId)); else localStorage.removeItem(LS.order); }catch(e){} }
function restoreClientState(){ try{ state.table_no = localStorage.getItem(LS.table) || ""; state.customer_name = localStorage.getItem(LS.customer) || ""; state.customer_tax_id = localStorage.getItem(LS.customerTaxId) || ""; const oid = localStorage.getItem(LS.order); state.activeOrderId = oid ? Number(oid) : null; }catch(e){} }
function renderCartDrawer(){ const list=$("#cartList"); const items=Object.values(state.cart); list.innerHTML=""; if(!items.length){ list.innerHTML=`<small>Seu pedido está vazio. Adicione itens do cardápio 🙂</small>`; } else { items.forEach(it=>{ const row=document.createElement("div"); row.className="cart-line"; row.innerHTML=`<div class="left"><strong>${escapeHtml(it.name)}</strong><small>${moneyBR(it.price_cents)} cada</small></div><div class="stepper"><button data-act="dec" data-id="${it.id}" type="button">−</button><span>${it.qty}</span><button data-act="inc" data-id="${it.id}" type="button">+</button></div>`; list.appendChild(row); }); } $("#tableNo").value=state.table_no; $("#customerName").value=state.customer_name; if($("#customerTaxId")) $("#customerTaxId").value=state.customer_tax_id; $("#note").value=state.note; const {cents}=cartTotals(); $("#drawerTotal").textContent=moneyBR(cents); $("#cartTotal").textContent=moneyBR(cents); $("#sendBtn").disabled=!items.length; }
function addItem(item){ const existing=state.cart[item.id]; if(existing) existing.qty += 1; else state.cart[item.id]={id:item.id,name:item.name,price_cents:item.price_cents,qty:1}; renderCartMini(); if($("#drawer").classList.contains("open")) renderCartDrawer(); vibrate(); }
function changeQty(itemId, delta){ const it=state.cart[itemId]; if(!it) return; it.qty += delta; if(it.qty<=0) delete state.cart[itemId]; renderCartMini(); renderCartDrawer(); }
function openModal(item){ $("#modalTitle").textContent=item.name; $("#modalDesc").textContent=item.details||item.description||""; $("#modalPrice").textContent=moneyBR(item.price_cents); $("#modalImg").src=`/static/img/${item.image}`; $("#modalImg").alt=item.name; $("#modalAdd").onclick=()=>{ addItem(item); toast("Adicionado ao pedido", item.name); closeModal(); }; setOverlay("#modalBack", true); }
function closeModal(){ setOverlay("#modalBack", false); }
async function sendOrder(){ state.table_no=$("#tableNo").value.trim(); state.customer_name=$("#customerName").value.trim(); state.note=$("#note").value.trim(); const items=Object.values(state.cart).map(it=>({id:it.id, qty:it.qty})); const res=await fetch("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({table_no:state.table_no,customer_name:state.customer_name,note:state.note,items})}); const data=await res.json().catch(()=>({})); if(!res.ok){ toast("Não foi possível enviar", data.error||"Tenta de novo."); return; } state.activeOrderId=data.order_id; state.lastOrderSnapshot=null; state.cart={}; renderCartMini(); renderCartDrawer(); $("#trackHint").style.display="block"; toast("Pedido enviado!", `Número #${data.order_id} • Mesa ${state.table_no}`); startOrderPolling(); }
function statusBadge(status){ const s=(status||"").toLowerCase(); if(s.includes("pronto")||s.includes("entregue")) return "ok"; if(s.includes("preparo")||s.includes("cozinha")) return "warn"; if(s.includes("cancel")) return "bad"; return ""; }
function renderTracking(snapshot){
  const box=$("#trackingBox");
  const summary = state.tableSummary;
  if(!snapshot && !summary){ box.innerHTML=`<small>Nenhum pedido ativo ainda.</small>`; return; }
  const orderBlock = snapshot ? (()=>{
    const o=snapshot.order;
    const items=snapshot.items||[];
    const eta=o.eta_minutes==null?"Sem previsão":`${o.eta_minutes} min`;
    const msg=o.admin_message?`<div class="toast" style="position:static;opacity:1;transform:none;animation:none;margin-top:12px"><strong>Mensagem do restaurante</strong><small>${escapeHtml(o.admin_message)}</small></div>`:"";
    return `
      <section class="tracking-pane">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
          <div>
            <strong>Pedido #${o.id}</strong>
            <small style="margin-top:6px">Mesa ${escapeHtml(o.table_no)} • ${eta}</small>
          </div>
          <span class="badge ${statusBadge(o.status)}">${escapeHtml(o.status||'Novo')}</span>
        </div>
        <div style="margin-top:12px;display:flex;flex-direction:column;gap:8px">${items.map(it=>`<div style="display:flex;justify-content:space-between;gap:10px"><small>${escapeHtml(it.qty)}x ${escapeHtml(it.name)}</small><small>${moneyBR(it.qty*it.price_cents)}</small></div>`).join("")}</div>
        <div style="margin-top:12px;padding-top:12px;border-top:1px dashed rgba(29,36,48,.12);display:flex;justify-content:space-between;gap:10px"><strong>Total do pedido</strong><strong>${snapshot.money_total}</strong></div>
        ${msg}
      </section>`;
  })() : '';

  const summaryBlock = summary ? `
    <section class="tracking-pane tracking-pane--summary">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
        <div>
          <strong>Comanda da mesa ${escapeHtml(summary.table_no)}</strong>
          <small style="margin-top:6px">${summary.orders_count} pedido(s) abertos • aberta desde ${escapeHtml(summary.oldest_br || '')}</small>
        </div>
        <span class="badge">Saldo ${escapeHtml(summary.money_due)}</span>
      </div>
      <div class="summary-grid">
        <div><small>Total aberto</small><strong>${escapeHtml(summary.money_total)}</strong></div>
        <div><small>Pago</small><strong>${escapeHtml(summary.money_paid)}</strong></div>
        <div><small>Falta pagar</small><strong>${escapeHtml(summary.money_due)}</strong></div>
      </div>
      <div style="margin-top:10px;display:flex;flex-direction:column;gap:8px">${(summary.items||[]).map(it=>`<div style="display:flex;justify-content:space-between;gap:10px"><small>${escapeHtml(it.qty)}x ${escapeHtml(it.name)}</small><small>${moneyBR(it.qty*it.price_cents)}</small></div>`).join("")}</div>
      <div class="summary-actions">
        ${Number(summary.due_cents||0) > 0 ? `<button class="btn primary" id="payTablePixBtn" type="button">Pagar saldo via Pix</button>` : ''}
        <button class="btn ghost" id="refreshTableBtn" type="button">Atualizar comanda</button>
        <button class="btn ghost" id="billBtn" type="button">Pedir fechamento no caixa</button>
      </div>
      <small style="color:var(--muted)">No restaurante, o fechamento principal pode ser feito no caixa pela mesa. O Pix continua disponível nesta demonstração.</small>
    </section>` : '';

  box.innerHTML = orderBlock + summaryBlock;
  const btn = $("#payTablePixBtn", box);
  if(btn && summary){ btn.onclick = ()=> startPixForTable(summary); }
  $("#refreshTableBtn", box)?.addEventListener('click', ()=> pollTableSummary(true));
  $("#billBtn", box)?.addEventListener('click', ()=> sendTableRequest("Fechar comanda no caixa"));
}


async function startPixForTable(summary){
  const table = (summary.table_no||state.table_no||"").trim();
  const amountCents = Number(summary.due_cents||0);
  if(!table || amountCents<=0){ toast("Comanda em dia", "Não há saldo pendente nessa mesa."); return; }
  const res = await fetch('/api/pix/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
    table_no: table,
    amount_cents: amountCents,
    customer_name: state.customer_name || "",
    customer_tax_id: state.customer_tax_id || "",
  })});
  const data = await res.json().catch(()=>({}));
  if(!res.ok){ toast("Pix indisponível", data.error || "Peça para configurar a chave Pix no admin."); if(data.code === "CPF_CNPJ_REQUIRED"){ openSheet("cart"); $("#customerTaxId")?.focus(); } return; }
  state.pix.paymentId = data.payment_id;
  state.pix.payload = data.payload;
  state.pix.autoConfirm = !!data.auto_confirm;
  state.pix.provider = data.provider || "LOCAL_PIX";
  $("#pixQrImg").src = data.qr;
  $("#pixAmount").textContent = data.amount;
  $("#pixCode").value = data.payload;
  $("#pixSubtitle").textContent = state.pix.autoConfirm ? "Pague no app do banco. A confirmação tende a acontecer automaticamente em instantes." : "Abra o app do banco e escaneie o QR Code.";
  $("#pixIpaid").textContent = state.pix.autoConfirm ? "Já paguei / agilizar" : "Já paguei";
  $("#pixStatus").textContent = state.pix.autoConfirm ? "Aguardando pagamento e confirmação automática…" : "Aguardando pagamento…";
  openPixModal();
  toast("Pix gerado", `Mesa ${table} • ${data.amount}`);
  startPixPolling();
}

async function startPixPayment(order){
  const table = (order.table_no||"").trim();
  if(!table){ toast("Informe a mesa", "Preencha a mesa no pedido."); return; }
  const amountCents = Number(order.total_cents||0);
  const res = await fetch('/api/pix/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
    table_no: table,
    order_id: order.id,
    amount_cents: amountCents,
    customer_name: state.customer_name || order.customer_name || "",
    customer_tax_id: state.customer_tax_id || "",
  })});
  const data = await res.json().catch(()=>({}));
  if(!res.ok){ toast("Pix indisponível", data.error || "Peça para configurar a chave Pix no admin."); if(data.code === "CPF_CNPJ_REQUIRED"){ openSheet("cart"); $("#customerTaxId")?.focus(); } return; }
  state.pix.paymentId = data.payment_id;
  state.pix.payload = data.payload;
  state.pix.autoConfirm = !!data.auto_confirm;
  state.pix.provider = data.provider || "LOCAL_PIX";
  $("#pixQrImg").src = data.qr;
  $("#pixAmount").textContent = data.amount;
  $("#pixCode").value = data.payload;
  $("#pixSubtitle").textContent = state.pix.autoConfirm ? "Pague no app do banco. A confirmação tende a acontecer automaticamente em instantes." : "Abra o app do banco e escaneie o QR Code.";
  $("#pixIpaid").textContent = state.pix.autoConfirm ? "Já paguei / agilizar" : "Já paguei";
  $("#pixStatus").textContent = state.pix.autoConfirm ? "Aguardando pagamento e confirmação automática…" : "Aguardando pagamento…";
  openPixModal();
  toast("Pix gerado", "Escaneie o QR e conclua o pagamento.");
  startPixPolling();
}

async function markPixPaid(){
  if(!state.pix.paymentId) return;
  const res = await fetch('/api/pix/mark_paid', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({payment_id: state.pix.paymentId})});
  const data = await res.json().catch(()=>({}));
  if(res.ok){
    if(data.auto_confirmed){
      $("#pixStatus").textContent = "Pagamento confirmado! ✅";
      toast("Pagamento confirmado", "O pagamento já foi identificado automaticamente.");
      vibrate(35);
      stopPixPolling();
      showPaidOverlay();
      setTimeout(()=>closePixModal(), 650);
      pollOrderOnce();
      return;
    }
    $("#pixStatus").textContent = state.pix.autoConfirm ? "Recebido. Vamos tentar localizar a cobrança automaticamente…" : "Ok! Aguarde a confirmação do caixa 🙂";
    toast("Enviado", state.pix.autoConfirm ? "Vamos conferir a cobrança no provedor." : "Avisamos o caixa para confirmar.");
    vibrate(22);
  } else {
    toast("Ops", data.error || "Não deu pra registrar agora.");
  }
}

async function pollPixStatusOnce(){
  if(!state.pix.paymentId) return;
  const res = await fetch(`/api/pix/status/${state.pix.paymentId}`);
  if(!res.ok) return;
  const data = await res.json().catch(()=>({}));
  const p = data.payment;
  if(!p) return;
  if(p.status === 'PENDENTE') $("#pixStatus").textContent = state.pix.autoConfirm ? "Aguardando pagamento e confirmação automática…" : "Aguardando pagamento…";
  if(p.status === 'INFORMADO') $("#pixStatus").textContent = state.pix.autoConfirm ? "Pagamento informado. Conferindo no provedor…" : "Aguardando confirmação do caixa…";
  if(p.status === 'CONFIRMADO'){
    $("#pixStatus").textContent = "Pagamento confirmado! ✅";
    toast("Pagamento confirmado", "Obrigado! 🙂");
    vibrate(35);
    stopPixPolling();
    showPaidOverlay();
    setTimeout(()=>closePixModal(), 650);
    pollOrderOnce();
  }
  if(p.status === 'REJEITADO'){
    $("#pixStatus").textContent = "Pagamento rejeitado. Chame a equipe.";
    toast("Rejeitado", "Fale com a equipe.");
  }
}

function startPixPolling(){
  stopPixPolling();
  pollPixStatusOnce();
  state.pix.poll = setInterval(pollPixStatusOnce, 2200);
}

async function pollTableSummary(forceToast=false){
  const table = (($("#tableNo")?.value || state.table_no || '').trim());
  if(!table){ state.tableSummary=null; if(!state.activeOrderId) renderTracking(null); return; }
  const res = await fetch(`/api/tables/${encodeURIComponent(table)}/summary`);
  if(!res.ok){ state.tableSummary=null; if(!state.activeOrderId) renderTracking(null); return; }
  const data = await res.json().catch(()=>({}));
  state.tableSummary = data.summary || null;
  if(forceToast && state.tableSummary) toast('Comanda atualizada', `Mesa ${table} • ${state.tableSummary.money_due} em aberto`);
  renderTracking(state.lastOrderSnapshot);
}

async function pollOrderOnce(){ if(!state.activeOrderId){ await pollTableSummary(); return; } const res=await fetch(`/api/orders/${state.activeOrderId}`); if(!res.ok) return; const snapshot=await res.json(); if(state.lastOrderSnapshot){ const prev=state.lastOrderSnapshot.order; const now=snapshot.order; if(prev.status!==now.status) toast("Status atualizado", now.status||"Novo"); else if((prev.eta_minutes||null)!==(now.eta_minutes||null)) toast("Tempo atualizado", now.eta_minutes==null?"Sem previsão":`${now.eta_minutes} min`); else if((prev.admin_message||"")!==(now.admin_message||"")) toast("Nova mensagem", now.admin_message||""); } state.lastOrderSnapshot=snapshot; await pollTableSummary(); renderTracking(snapshot); }
function startOrderPolling(){ clearInterval(state.pollTimer); pollOrderOnce(); state.pollTimer=setInterval(pollOrderOnce, 3500); }
async function sendTableRequest(type){ const table=($("#tableNo").value.trim()||state.table_no||"").trim(); if(!table){ toast("Informe a mesa", "Preencha a mesa no pedido antes 🙂"); openSheet("cart"); return; } const res=await fetch("/api/table_requests",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({table_no:table, req_type:type})}); if(res.ok){ toast("Solicitação enviada", type); closeSheet("actions"); } else toast("Ops", "Não deu pra enviar agora."); }
function applyFilter(target){ $$(".category-pill").forEach(c=>c.classList.toggle("active", c.dataset.target===target)); $$(".menu-section").forEach(s=>{ s.style.display=(target==="all"||s.dataset.section===target)?"flex":"none"; }); }
function bind(){ $$(".category-pill").forEach(ch=>ch.addEventListener("click", ()=>{ applyFilter(ch.dataset.target); window.scrollTo({top:0, behavior:"smooth"}); })); $("#q").addEventListener("input", e=>{ const q=e.target.value.trim().toLowerCase(); $$(".food-card").forEach(card=>{ const ok=card.dataset.name.includes(q)||card.dataset.desc.includes(q); card.style.display=ok?"grid":"none"; }); }); $$(".food-card").forEach(card=>{ const item=JSON.parse(card.dataset.item); const addBtn=$(".add",card); const moreBtn=$(".more",card); addBtn.addEventListener("click", ()=>{ addItem(item); addBtn.classList.remove("boom"); void addBtn.offsetWidth; addBtn.classList.add("boom"); toast("Adicionado ao pedido", item.name); }); moreBtn.addEventListener("click", ()=>openModal(item)); }); $("#modalBack").addEventListener("click", e=>{ if(e.target.id==="modalBack") closeModal(); }); $("#modalClose").addEventListener("click", closeModal); $("#openReserveBtn").addEventListener("click", ()=>location.href="/reserve"); $("#spotlightOpenCart").addEventListener("click", ()=>openSheet("cart")); $("#cartFab").addEventListener("click", ()=>openSheet("cart")); $("#dockCart").addEventListener("click", ()=>openSheet("cart")); $("#dockTrack").addEventListener("click", ()=>{ if(!state.activeOrderId && !($("#tableNo")?.value.trim()||state.table_no)){ toast("Informe a mesa", "Digite sua mesa para consultar a comanda."); openSheet("cart"); return; } openSheet("cart"); pollTableSummary(true); }); $("#dockMesa").addEventListener("click", ()=>openSheet("actions")); $("#drawerBack").addEventListener("click", ()=>closeSheet("cart")); $("#actionsBack").addEventListener("click", ()=>closeSheet("actions")); $("#drawerClose").addEventListener("click", ()=>closeSheet("cart")); $("#sheetClose").addEventListener("click", ()=>closeSheet("actions")); $("#tableNo").addEventListener("input", ()=>{ state.table_no=$("#tableNo").value.trim(); persistClientState(); clearTimeout(window.__mt); window.__mt=setTimeout(()=>pollTableSummary(), 250); }); $("#customerName").addEventListener("input", ()=>{ state.customer_name=$("#customerName").value.trim(); persistClientState(); }); $("#customerTaxId")?.addEventListener("input", ()=>{ state.customer_tax_id=$("#customerTaxId").value.replace(/\D+/g, "").slice(0,14); $("#customerTaxId").value = state.customer_tax_id; persistClientState(); }); $("#note").addEventListener("input", ()=>{ state.note=$("#note").value.trim(); }); $("#cartList").addEventListener("click", e=>{ const btn=e.target.closest("button"); if(!btn) return; const id=btn.dataset.id; if(btn.dataset.act==="inc") changeQty(id,1); if(btn.dataset.act==="dec") changeQty(id,-1); }); $("#sendBtn").addEventListener("click", sendOrder); $("#reqWaiter").addEventListener("click", ()=>sendTableRequest("Chamar garçom")); $("#reqBill").addEventListener("click", ()=>sendTableRequest("Pedir conta")); $("#reqWater").addEventListener("click", ()=>sendTableRequest("Água")); $("#reqHelp").addEventListener("click", ()=>sendTableRequest("Preciso de ajuda")); restoreClientState(); renderCartMini(); renderTracking(null); if(state.activeOrderId || state.table_no || state.customer_name || state.customer_tax_id){ pollTableSummary(); if(state.activeOrderId) startOrderPolling(); if(state.table_no) { $("#tableNo").value = state.table_no; } if(state.customer_name) { $("#customerName").value = state.customer_name; } if(state.customer_tax_id && $("#customerTaxId")) { $("#customerTaxId").value = state.customer_tax_id; } } }
document.addEventListener("DOMContentLoaded", bind);

document.addEventListener("DOMContentLoaded", ()=>{
  $("#pixBack")?.addEventListener('click', e=>{ if(e.target.id==='pixBack') closePixModal(); });
  $("#pixClose")?.addEventListener('click', closePixModal);
  $("#pixCopy")?.addEventListener('click', async ()=>{
    const code = $("#pixCode")?.value || "";
    try{ await navigator.clipboard.writeText(code); toast("Copiado", "Código Pix copiado."); }catch(e){ toast("Copiar", "Selecione e copie manualmente."); }
  });
  $("#pixIpaid")?.addEventListener('click', markPixPaid);
});
