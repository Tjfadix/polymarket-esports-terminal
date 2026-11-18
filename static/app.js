/* EsOps terminal front-end. Vanilla JS, no build step. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const fmtUsd = (v) => v == null ? "–" : (v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? "$" + (v / 1e3).toFixed(0) + "k" : "$" + v.toFixed(0));
const fmtCents = (v) => v == null ? "–" : (v * 100).toFixed(1);
const fmtSigned = (v, f) => v == null ? "–" : (v >= 0 ? "+" : "") + f(v);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ACTION_CLASS = { "STRONG BUY": "tag-strong", "BUY": "tag-buy", "WATCH": "tag-watch" };

const state = { rows: [], selected: null, mode: "live", detailTimer: null, boardTimer: null, tick: 0 };

/* ---------------- toast ---------------- */
let toastTimer = null;
function toast(msg, isErr) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

/* ---------------- filters ---------------- */
function filterQuery() {
  const p = new URLSearchParams();
  p.set("game", $("#game").value);
  p.set("min_vol", $("#minvol").value);
  if ($("#q").value.trim()) p.set("q", $("#q").value.trim());
  return p.toString();
}

/* ---------------- board ---------------- */
async function loadBoard() {
  let data;
  try {
    const r = await fetch("/api/board?" + filterQuery());
    data = await r.json();
  } catch {
    setConn("bad", "server unreachable");
    return;
  }
  state.rows = data.rows || [];
  state.mode = data.mode;
  const badge = $("#mode-badge");
  badge.textContent = data.mode === "demo" ? "demo feed" : "live data";
  badge.className = "badge " + data.mode;
  setConn(data.error && !data.rows.length ? "bad" : "ok",
          data.error ? "partial upstream" : (data.rows.length ? "live" : "empty"));
  renderBoard();
}

function setConn(cls, label) {
  const el = $("#conn");
  el.className = "conn " + cls;
  el.lastChild.textContent = " " + label;
}

function actionClass(action) {
  return { "STRONG BUY": "tag-strong", "BUY": "tag-buy", "WATCH": "tag-watch" }[action] || "tag-none";
}

function skewBar(v, maxAbs) {
  const pct = Math.max(-1, Math.min(1, v / maxAbs));
  const w = Math.abs(pct) * 50;
  const side = pct < 0 ? "neg" : "pos";
  const left = pct < 0 ? 50 - w : 50;
  return `<span class="skewbar"><i class="${side}" style="left:${left}%;width:${w}%"></i></span>`;
}

function sparkline(points, steam) {
  if (!points || points.length < 2) return `<svg class="spark"></svg>`;
  const w = 110, h = 24;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 0.02) { lo -= 0.01; hi += 0.01; }
  const step = w / (points.length - 1 || 1);
  const path = points.map((p, i) =>
    `${(i * step).toFixed(1)},${(h - 2 - ((p[1] - lo) / (hi - lo)) * (h - 4)).toFixed(1)}`).join(" ");
  const color = steam > 0.002 ? "var(--green)" : steam < -0.002 ? "var(--red)" : "var(--dim)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${color}" stroke-width="1.4" points="${path}"/></svg>`;
}

function entryFor(row, sig) {
  if (sig.entry != null) return sig.entry;
  if (!sig.side) return row.mid;
  if (sig.side === "YES") return row.ask ?? row.mid;
  return row.noBid != null ? 1 - row.noBid : (row.noAsk != null ? row.noAsk : (1 - (row.bid ?? 0.5)));
}

function renderBoard() {
  const onlyTrades = $("#only-trades").checked;
  const rows = onlyTrades ? state.rows.filter((r) => r.sig && r.sig.side) : state.rows;
  $("#rowcount").textContent = rows.length + " markets";
  const tb = $("#rows");
  tb.innerHTML = rows.map((r) => {
    const sig = r.sig || {};
    const conv = sig.conviction ?? 0;
    const sideTxt = sig.side ? `<span class="side-${sig.side.toLowerCase()}">${sig.side}</span>` : "–";
    const entryC = fmtCents(entryFor(r, sig));
    const edge = sig.edgePts;
    const k = sig.kelly != null ? (sig.kelly * 100).toFixed(1) + "%" : "–";
    const imB = sig.factors?.imbalance ?? 0;
    const flw = sig.factors?.flow ?? 0;
    const mom = sig.factors?.momentum ?? 0;
    const chg = r.chg1h;
    const liq = sig.liq ?? 0;
    const spark = (r.spark || []).map((p) => [p.t ?? p[0], p.p ?? p[1]]);
    const convCls = ACTION_CLASS[sig.action] || "tag-none";
    return `<tr data-cid="${esc(r.conditionId)}" class="${r.conditionId === state.selected ? "selected" : ""}">
      <td class="c-conv"><span class="conv ${convCls}">${sig.action || "NO TRADE"}<span class="muted"> ${conv}</span></span></td>
      <td class="c-side">${sideTxt}</td>
      <td class="c-px num">${entryC}</td>
      <td class="c-fair num">${sig.fair != null ? fmtCents(sig.fair) : "–"}</td>
      <td class="c-edge num ${edge > 0 ? "up" : edge < 0 ? "down" : ""}">${edge != null ? fmtSigned(edge, (v) => (v * 100).toFixed(1)) : "–"}</td>
      <td class="c-kelly num ${sig.kelly > 0.02 ? "up" : "muted"}">${k}</td>
      <td class="c-market">${esc(r.eventTitle)}${r.question ? `<span class="muted"> · ${esc(shortQ(r.question, r.eventTitle))}</span>` : ""}</td>
      <td class="c-game muted">${esc(r.game)}</td>
      <td class="c-mom num ${chg > 0 ? "up" : chg < 0 ? "down" : "muted"}">${chg != null ? fmtSigned(chg, (v) => (v * 100).toFixed(1)) : "–"}</td>
      <td class="c-fac">${skewBar(imB, 1)}</td>
      <td class="c-fac">${skewBar(flw, 1)}</td>
      <td class="c-mom">${momBar(mom)}</td>
      <td class="c-vol num">${fmtUsd(r.volume24)}${r.sig?.vmult >= 2 ? ` <span class="up small">x${r.sig.vmult.toFixed(1)}</span>` : ""}</td>
      <td class="c-liq num ${liq >= 70 ? "up" : liq < 40 ? "down" : ""}">${liq}</td>
      <td class="c-spark">${sparkline(spark, r.steam || 0)}</td>
    </tr>`;
  }).join("");
  $("#board-empty").classList.toggle("hidden", rows.length > 0);
  tb.querySelectorAll("tr").forEach((tr) =>
    tr.addEventListener("click", () => openDrawer(tr.dataset.cid)));
}

function shortQ(question, eventTitle) {
  let q = question.replace(/^Will /, "").replace(/ win\??$/i, "").replace(/\?$/, "");
  if (q.length > 34) q = q.slice(0, 33) + "…";
  return q;
}

function momBar(v) {
  const w = Math.min(Math.abs(v), 1) * 50;
  const side = v < 0 ? "neg" : "pos";
  return `<span class="skewbar"><i class="${side}" style="left:${v < 0 ? 50 - w : 50}%;width:${w}"></i></span>`;
}

/* ---------------- drawer ---------------- */
async function openDrawer(cid) {
  state.selected = cid;
  $("#drawer").classList.remove("hidden");
  clearInterval(state.detailTimer);
  await loadDetail();
  state.detailTimer = setInterval(() => state.selected && loadDetail(), 6000);
  renderBoard();
}

function closeDrawer() {
  state.selected = null;
  clearInterval(state.detailTimer);
  $("#drawer").classList.add("hidden");
  renderBoard();
}

let dMarket = null;

async function loadDetail() {
  if (!state.selected) return;
  const r = await fetch("/api/market/" + state.selected);
  if (!r.ok) return;
  const d = await r.json();
  dMarket = d;
  $("#d-title").textContent = d.event.title;
  $("#d-game").textContent = d.event.game;
  $("#d-game").classList.add(d.mode === "demo" ? "demo" : "live");
  $("#d-end").textContent = "resolves " + (d.market.endDate || d.event.endDate || "").slice(0, 10);
  renderFactors(d);
  renderLadders(d);
  renderTicket(d);
  renderTape(d);
}

function factorBar(name, v, txt) {
  const w = Math.min(Math.abs(v), 1) * 50;
  const side = v < 0 ? "neg" : "pos";
  return `<div class="factor"><label>${name}</label>
    <div class="fbar"><i class="${side}" style="left:${v < 0 ? 50 - w : 50}%;width:${w}%"></i></div>
    <b>${txt}</b></div>`;
}

function renderFactors(d) {
  const sig = d.live?.sig || {};
  const f = sig.factors || {};
  const fair = sig.fair != null ? sig.fair.toFixed(3) : "–";
  let html =
    factorBar("momentum", f.momentum ?? 0, f.momentum?.toFixed(2) || "–") +
    factorBar("book imbalance", f.imbalance ?? 0, f.imbalance?.toFixed(2) || "–") +
    factorBar("taker flow", f.flow ?? 0, f.flow?.toFixed(2) || "–") +
    factorBar("edge / model", f.edge ?? 0, f.edge?.toFixed(2) || "–") +
    `<div class="factor"><label>volume shock</label><div class="fbar"><i class="pos" style="left:0;width:${Math.min(sig.vmult ?? 0, 4) / 4 * 100}%;background:#4fc3ff"></i></div><b>x${(sig.vmult ?? 0).toFixed(1)}</b></div>
     <div class="factor"><label>liquidity</label><div class="fbar"><i class="pos" style="left:0;width:${sig.liq ?? 0}%;background:var(--blue)"></i></div><b>${sig.liq ?? 0}</b></div>`;
  $("#d-factors").innerHTML = html;

  const v = $("#d-verdict");
  const conv = sig.conviction ?? 0;
  const vColor = conv >= 63 ? "var(--green)" : conv >= 40 ? "var(--green-dim)" : conv >= 25 ? "var(--amber)" : "var(--dim)";
  v.innerHTML = `<span style="color:${vColor};font-weight:700">${esc(sig.action || "NO TRADE")} ${sig.side ? esc(sig.side) + " @ " + (sig.entry != null ? (sig.entry * 100).toFixed(1) + "¢" : "") : ""}</span>
    <span class="muted"> · conviction ${conv}/100 · fair ${fair} · edge ${sig.edgePts != null ? fmtSigned(sig.edgePts, (e) => (e * 100).toFixed(1)) + "pts" : "–"} · ½kelly ${(sig.kelly ?? 0) > 0 ? ((sig.kelly) * 100).toFixed(1) + "%" : "–"}</span>
    <div class="muted small" style="margin-top:6px">${esc(sig.insight || "")}</div>`;
}

function ladderHtml(levels, isBid, bestRef) {
  const rows = levels.slice(0, 8);
  const maxSz = Math.max(...rows.map((l) => l[1]), 1);
  return rows.map((l) => {
    const w = (l[1] / maxSz * 100).toFixed(0);
    const bg = isBid ? "rgba(51,255,153,.14)" : "rgba(255,84,112,.14)";
    return `<div class="lv ${isBid ? "bid" : "ask"}"><b>${(l[0] * 100).toFixed(1)}¢</b>
      <span style="position:relative;text-align:right">${l[1].toLocaleString()}
        <i style="position:absolute;right:0;top:0;bottom:0;width:${w}%;background:${bg};z-index:-1"></i></span></div>`;
  }).join("");
}

function renderLadders(d) {
  const yb = d.live?.yesBook, nb = d.live?.noBook;
  $("#d-book-yes").innerHTML =
    (yb?.asks ? ladderHtml(topAsks(yb.asks), false) : "") +
    `<div class="lv mid"><span>${d.live?.mid != null ? (d.live.mid * 100).toFixed(1) + "¢ mid" : ""}</span></div>` +
    (yb?.bids ? ladderHtml(topBids(yb.bids), true) : "");
}

function topBids(bids) { return [...bids].sort((a, b) => b[0] - a[0]).slice(0, 8); }
function topAsks(asks) { return [...asks].sort((a, b) => a[0] - b[0]).slice(0, 8); }

/* ticket: YES token usage; NO via complement button */
function currentOutcome() { return $("#t-buy").dataset.outcome === "NO" ? 1 : 0; }

function renderTicket(d) {
  const m = d.market, live = d.live || {};
  const yesBid = live.bid, yesAsk = live.ask;
  const noAsk = live.noAsk != null ? live.noAsk : (yesBid != null ? 1 - yesBid : null);
  const noBid = live.noBid != null ? live.noBid : (yesAsk != null ? 1 - yesAsk : null);
  const oc = currentOutcome();
  const bid = oc === 0 ? yesBid : noBid, ask = oc === 0 ? yesAsk : noAsk;
  $("#oc-yes").classList.toggle("active", oc === 0);
  $("#oc-no").classList.toggle("active", oc === 1);
  $("#t-quote").innerHTML = `<span class="muted">${oc === 0 ? "YES" : "NO"} token</span>
    <span><b class="bid">${bid != null ? (bid * 100).toFixed(1) + "¢" : "–"}</b> / <b class="ask">${ask != null ? (ask * 100).toFixed(1) + "¢" : "–"}</b></span>`;
  const shares = Math.max(1, parseInt($("#t-shares").value || "1", 10));
  const book = oc === 0 ? live.yesBook : live.noBook;
  const walk = (levels, n, isBid) => {
    const lv = (levels?.[isBid ? "bids" : "asks"] || []).slice();
    lv.sort((a, b) => isBid ? b[0] - a[0] : a[0] - b[0]);
    let rem = n, cost = 0;
    for (const [p, s] of lv) { const t = Math.min(rem, s); cost += t * p; rem -= t; if (!rem) break; }
    return rem ? null : cost / n;
  };
  const buyVwap = book ? walk(book, shares, false) : null;
  const fee = buyVwap != null ? (d.market.feeRate ?? 0.03) * Math.min(buyVwap, 1 - buyVwap) : 0;
  $("#t-slip").innerHTML = buyVwap == null ? "not enough book depth for that size"
    : `fill ≈ <b>${(buyVwap * 100).toFixed(2)}¢</b> (${shares} sh) · fee ≈ ${fee.toFixed(2)}¢/sh · cost <b>$${(buyVwap * shares + fee * shares).toFixed(2)}</b>`;
}

function renderTape(d) {
  const tape = (d.tape || []).slice(0, 40);
  $("#d-tape").innerHTML = tape.map((t) => {
    const b = String(t.side || "").toUpperCase() === "BUY";
    const when = t.ts ? new Date(t.ts * 1000).toLocaleTimeString([], { hour12: false }) : "–";
    return `<div class="tr ${b ? "b" : "s"}"><span class="when">${when}</span><span>${b ? "BUY" : "SELL"}</span>
      <span class="num">${fmtCents(t.price)}¢</span><span class="who num">$${(t.size * t.price).toFixed(0)}</span></div>`;
  }).join("") || `<div class="muted small">no tape yet</div>`;
}

/* ---------------- paper trading ---------------- */
async function paperOrder(side) {
  if (!dMarket) return;
  const shares = Math.max(1, parseInt($("#t-shares").value || "1", 10));
  const body = {
    conditionId: dMarket.market.conditionId,
    outcomeIdx: currentOutcome(),
    side, shares,
  };
  const r = await fetch("/api/paper/order", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) return toast(data.error || "order rejected", true);
  toast(`${side} ${data.shares} @ ${(data.price * 100).toFixed(1)}¢ — fill ok`);
  loadPaper();
}

async function closePosition(token) {
  const r = await fetch("/api/paper/order", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, side: "SELL", shares: 1e9, closeAll: true }),
  });
  const data = await r.json();
  if (!r.ok) return toast(data.error || "close rejected", true);
  toast("closed: " + data.shares + " @ " + (data.price * 100).toFixed(1) + "¢");
  loadPaper();
}

async function loadPaper() {
  const r = await fetch("/api/paper/account");
  const a = await r.json();
  $("#p-equity").textContent = "$" + a.equity.toLocaleString();
  $("#p-cash").textContent = "$" + a.cash.toLocaleString();
  $("#p-ret").textContent = fmtSigned(a.returnPct, (v) => v.toFixed(2) + "%");
  $("#p-ret").className = a.returnPct >= 0 ? "up" : "down";
  $("#p-realized").textContent = fmtSigned(a.realized, (v) => "$" + v.toFixed(2));
  $("#p-realized").className = a.realized >= 0 ? "up" : "down";
  $("#p-unrealized").textContent = fmtSigned(a.unrealized, (v) => "$" + v.toFixed(2));
  $("#p-unrealized").className = a.unrealized >= 0 ? "up" : "down";
  $("#p-fills").textContent = a.filled;

  $("#positions tbody").innerHTML = (a.positions || []).map((p, i) => {
    return `<tr>
      <td>${esc(p.label || p.question || "–")}</td>
      <td><span class="side-${String(p.side).toLowerCase() === "yes" ? "yes" : "no"}">${esc(p.side)}</span></td>
      <td class="num">${p.shares}</td>
      <td class="num">${(p.avgCost * 100).toFixed(1)}¢</td>
      <td class="num">${(p.mark * 100).toFixed(1)}¢</td>
      <td class="num ${p.upnl >= 0 ? "up" : "down"}">${fmtSigned(p.upnl, (v) => "$" + v.toFixed(2))}</td>
      <td class="num"><button class="close-btn" data-tok="${esc(p.token)}">close</button></td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">flat — no open positions</td></tr>`;
  document.querySelectorAll("#positions .close-btn").forEach((b) =>
    b.addEventListener("click", () => closePosition(b.dataset.tok)));

  $("#fills tbody").innerHTML = (a.history || []).map((h) => {
    return `<tr>
      <td class="num muted">${new Date(h.ts * 1000).toLocaleTimeString([], { hour12: false })}</td>
      <td>${esc(h.label || "–")}</td>
      <td class="${h.side === "BUY" ? "up" : "down"}">${h.side}</td>
      <td class="num">${h.shares}</td>
      <td class="num">${(h.price * 100).toFixed(1)}¢</td>
      <td class="num muted">${h.fee.toFixed(2)}¢</td>
      <td class="num ${h.realized >= 0 ? "up" : "down"}">${h.realized == null ? "–" : fmtSigned(h.realized, (v) => "$" + v.toFixed(2))}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">no fills yet</td></tr>`;
}

/* ---------------- shell ---------------- */
function switchTab(tab) {
  const isBoard = tab === "board";
  $("#view-board").classList.toggle("hidden", !isBoard);
  $("#view-paper").classList.toggle("hidden", isBoard);
  $("#tab-board").classList.toggle("active", isBoard);
  $("#tab-paper").classList.toggle("active", !isBoard);
  if (!isBoard) loadPaper();
}

function tickClock() {
  $("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false, timeZoneName: "short" }).toUpperCase();
  state.tick += 1;
  const left = 10 - (state.tick % 10);
  $("#refresh-in").textContent = "next refresh " + left + "s";
  if (state.tick % 10 === 0 && $("#view-board").classList.contains("hidden") === false) loadBoard();
}

const METHODOLOGY = `
<p><b>Movement + microstructure composite.</b> Each market is graded on its YES token; the engine then maps the view onto the cheaper actionable side using outcome parity (buying NO at 1 - YES_bid, which the CLOB enforces via).</p>
<p><b>Book imbalance (25%)</b> — near-touch depth of the *merged* YES book: the native YES book joined with the mirrored NO book (a NO bid at p supplies YES at 1-p). Positive = resting demand for YES.</p>
<p><b>Taker flow (25%)</b> — recency-weighted notional skew of the last hour of prints from the Data API tape. Buying pressure weighted with a 15-minute half-life.</p>
<p><b>Momentum (20%)</b> — fast-vs-slow EWA divergence of the 1-minute price series (5min vs 45min), normalized by realized vol and tanh-squashed.</p>
<p><b>Edge (30%)</b> — modelled fair value (recency-weighted median of trade prices) minus the after-fee entry touch. Fees use the market's own schedule (esports ≈ 3% taker, scaled by min(p, 1-p)). An edge signal only shows when the tape's fair value is materially away from the quote.</p>
<p><b>Volume shock</b> — 24h rate vs the past week's average day. Multiplies a conviction boost up to 2x and is flagged on the board.</p>
<p><b>Liquidity gate (0-100)</b> — spread tightness, $ depth and activity. Below 40 the composite is masked: signals on thin books are noise.</p>
<p><b>Conviction</b> = |composite| boosted by volume shock, capped 0-100. Tiers: ≥75 STRONG BUY, ≥45 BUY, ≥30 WATCH, else NO TRADE. Sizing shown is ½-Kelly with a 25% cap.</p>
<p class="muted small" style="margin-top:14px">This is a research terminal, not financial advice. Signals are microstructure-based and degrade on stale or thin books. Positions marked to mid; paper fills walk the live book plus taker fees.</p>`;

document.addEventListener("DOMContentLoaded", () => {
  $("#tab-board").addEventListener("click", () => switchTab("board"));
  $("#tab-paper").addEventListener("click", () => switchTab("paper"));
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#t-buy").addEventListener("click", () => paperOrder("BUY"));
  $("#t-sell").addEventListener("click", () => paperOrder("SELL"));
  $("#t-buy").dataset.outcome = "YES";
  $("#oc-yes").addEventListener("click", () => { if (dMarket) { $("#t-buy").dataset.outcome = "YES"; renderTicket(dMarket); } });
  $("#oc-no").addEventListener("click", () => { if (dMarket) { $("#t-buy").dataset.outcome = "NO"; renderTicket(dMarket); } });
  $("#t-shares").addEventListener("input", () => dMarket && renderTicket(dMarket));
  $("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadBoard(); });
  $("#game").addEventListener("change", loadBoard);
  $("#minvol").addEventListener("change", loadBoard);
  $("#only-trades").addEventListener("change", loadBoard);
  $("#p-reset").addEventListener("click", async () => {
    if (!confirm("Reset paper account? This wipes all positions and history.")) return;
    await fetch("/api/paper/reset", { method: "POST" });
    toast("paper account reset");
    loadPaper();
  });
  $("#modal-body").innerHTML = METHODOLOGY;
  $("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#modal").classList.add("hidden"); closeDrawer(); } });
  const badge = $("#mode-badge");
  badge.addEventListener("dblclick", () => $("#modal").classList.remove("hidden"));
  loadBoard();
  setInterval(tickClock, 1000);
});
