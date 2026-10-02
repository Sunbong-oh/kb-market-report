"use strict";

const $ = (s) => document.querySelector(s);
const QUICK = [
  ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["373220", "LG엔솔"], ["005380", "현대차"],
  ["035420", "NAVER"], ["105560", "KB금융"], ["012450", "한화에어로"], ["035720", "카카오"],
];
let current = { code: "005930", quote: null };
let futData = null;

// ------------------------------------------------------------ format
const fmt = (v, d = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "-" :
  Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 0) => (v === null || v === undefined ? "-" : (v > 0 ? "+" : "") + fmt(v, d));
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const arrow = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hhmm = (t) => (t && t.length >= 4 ? `${t.slice(0, 2)}:${t.slice(2, 4)}` : t || "");
const ymd = (d) => (d && d.length === 8 ? `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}` : d || "");

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}
function fail(el, e) { el.innerHTML = `<div class="err">불러오기 실패: ${esc(e.message)}</div>`; }

// ------------------------------------------------------------ status
async function loadStatus() {
  const s = await api("/api/status");
  $("#mode-badge").textContent = s.demo_mode ? "데모 데이터" : "KB OpenAPI 연결";
  $("#mode-badge").className = "badge " + (s.demo_mode ? "demo" : "live");
  $("#demo-banner").hidden = !s.demo_mode;
  const rb = $("#rule-badge");
  rb.textContent = s.rule.active ? `상승종목 매수제한 중 ${s.rule.window}` : `매수제한 ${s.rule.window} (비활성)`;
  rb.className = "badge " + (s.rule.active ? "rule-on" : "");
  $("#clock").textContent = `KST ${s.now}`;
  // 리포트 제목 띠: "2026.10.01 (목)  장마감 수급"
  const [y, mo, d] = s.now.slice(0, 10).split("-").map(Number);
  const wd = "일월화수목금토"[new Date(y, mo - 1, d).getDay()];
  const title = s.now.slice(11, 16) < "15:30" && s.now.slice(11, 16) >= "08:30" ? "장중 수급" : "장마감 수급";  // 장중에 받은 것은 '장중'으로 표시
  $("#report-title").innerHTML = `<span class="t">${title}</span><span class="d">${y}.${String(mo).padStart(2, "0")}.${String(d).padStart(2, "0")} (${wd}) · ${s.now.slice(11, 16)} 기준</span>`;
}

// ------------------------------------------------------------ market
// 막대 길이 기준(억원): 이 금액이면 막대가 한쪽 끝까지 찬다. 넘는 값은 끝에서 잘린다.
// scale을 안 주면 묶음 안 최댓값 기준(프로그램 매매 등).
const BAR_SCALE = { kospi: 20000, kosdaq: 5000, futures: 30000, call: 200, put: 200 };  // 2조 · 5천억 · 3조 · 200억

function barRows(items, key, hlNames = [], scale = null) {
  const max = scale || Math.max(1, ...items.map((i) => Math.abs(i[key] || 0)));
  return items.map((i) => {
    const v = i[key] || 0, w = Math.min(1, Math.abs(v) / max) * 50;
    return `<div class="bar-row ${hlNames.includes(i.name) ? "hl" : ""}">
      <span>${esc(i.name)}</span>
      <div class="bar-track"><div class="bar ${v >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>
      <span class="val ${cls(v)}">${signed(v)}</span></div>`;
  }).join("");
}

async function loadMarket() {
  try {
    const m = await api("/api/market");
    const t = m.time || "";
    $("#market-time").textContent = "IVSA0070" + (t.length >= 12 ? ` · ${t.slice(8, 10)}:${t.slice(10, 12)}` : t ? ` · ${ymd(t.slice(0, 8))}` : "");
    $("#indices").innerHTML = m.indices.map((i) => `
      <div class="index"><div class="n">${esc(i.name)}</div>
      <div class="v ${cls(i.change)}">${fmt(i.value, 2)}</div>
      <div class="${cls(i.change)}">${arrow(i.change)} ${fmt(Math.abs(i.change ?? 0), 2)} (${signed(i.change_pct, 2)}%)</div>
      ${MINI_IDS.includes(i.id) ? `<svg class="mini" data-id="${esc(i.id)}" role="img" aria-label="${esc(i.name)} 당일 흐름"></svg>` : ""}</div>`).join("")
      || `<div class="empty">지수 데이터 없음</div>`;
    drawMinis();
    const b = m.breadth;
    const br = (n, x) => `<span>${n} <b class="up">▲${fmt((x.up || 0) + (x.ulmt || 0))}</b> · ${fmt(x.unchng)} · <b class="down">▼${fmt((x.dwn || 0) + (x.llmt || 0))}</b></span>`;
    $("#breadth").innerHTML = br("코스피", b.kospi) + br("코스닥", b.kosdaq);
    // 등락 종목수: 상승(빨강)·보합(회색)·하락(파랑) 비율을 가로 막대로
    const bbar = (n, x) => {
      const up = (x.up || 0) + (x.ulmt || 0), dn = (x.dwn || 0) + (x.llmt || 0), st = x.unchng || 0, tot = up + dn + st || 1;
      const seg = (c, v) => v ? `<span class="${c}" style="width:${(v / tot) * 100}%">${v / tot > 0.08 ? fmt(v) : ""}</span>` : "";
      return `<div class="bb"><div class="bb-head"><b>${n}</b><span><span class="up">상승 ${fmt(up)}</span> · 보합 ${fmt(st)} · <span class="down">하락 ${fmt(dn)}</span></span></div>
        <div class="bb-bar" role="img" aria-label="${n} 상승 ${up} 보합 ${st} 하락 ${dn}">${seg("u", up)}${seg("s", st)}${seg("d", dn)}</div>
        <div class="bb-foot"><span>상승 ${fmt((up / tot) * 100, 0)}% · 상한가 ${fmt(x.ulmt || 0)}</span><span>하한가 ${fmt(x.llmt || 0)} · 하락 ${fmt((dn / tot) * 100, 0)}%</span></div></div>`;
    };
    $("#breadth-bars").innerHTML = bbar("코스피", b.kospi) + bbar("코스닥", b.kosdaq);

    const p = m.program;
    $("#program-market").innerHTML = `<div class="pgm">${barRows([
      { name: "차익", v: p.arbitrage }, { name: "비차익", v: p.non_arbitrage }, { name: "합계", v: p.total },
    ], "v", ["합계"])}</div><p class="muted">순매수 금액 · 단위는 KB 전문 원문 기준</p>`;

    const main = ["외국인", "기관계", "개인"];
    const pick = (names) => names.map((n) => m.investors.find((i) => i.name === n)).filter(Boolean);
    // 현물: 외국인·기관계·개인·금융투자 (투신·은행 제외) / 선물·옵션: 외국인·기관계·개인
    const spot = pick([...main, "금융투자"]), big3 = pick(main);
    const scaleNote = (k) => BAR_SCALE[k] >= 10000 ? `${BAR_SCALE[k] / 10000}조` : `${fmt(BAR_SCALE[k])}억`;
    const col = (title, key, rows) => `<div><h3>${title} <span class="scale-note">막대 기준 ${scaleNote(key)}</span></h3>
      <div class="pgm">${barRows(rows, key, main, BAR_SCALE[key])}</div></div>`;
    $("#investors").innerHTML = m.investors.length ? `
      <div class="inv-group"><h4>현물</h4><div class="inv-cols two">${col("코스피", "kospi", spot)}${col("코스닥", "kosdaq", spot)}</div></div>
      <div class="inv-group"><h4>선물 · 옵션 (KOSPI200)</h4>
        ${col("선물", "futures", big3)}
        <div class="inv-cols pair">${col("콜옵션", "call", big3)}${col("풋옵션", "put", big3)}</div></div>
      <p class="muted">IVSA0070 투자자별 순매수 · 단위 억원</p>` : `<div class="empty">수급 데이터 없음</div>`;
  } catch (e) { fail($("#indices"), e); }
}

// ------------------------------------------------------------ index mini charts
let intraday = {};
const MINI_IDS = ["KGG01P", "QGG01P"];  // 미니차트는 코스피·코스닥만 (나스닥 선물·KOSPI200 선물은 수치만)

async function loadIntraday() {
  try { intraday = await api("/api/indices/intraday"); drawMinis(); } catch { /* 미니차트는 없어도 지수 카드는 유지 */ }
}

function drawMinis() {
  document.querySelectorAll("svg.mini").forEach((svg) => {
    const d = intraday[svg.dataset.id], note = document.querySelector(`[data-note="${svg.dataset.id}"]`);
    const pts = (d?.points || []).filter((p) => p.c != null);
    if (note) note.textContent = d?.recorded ? (pts.length ? `서버 기록 ${hhmm(pts[0].t)}~${hhmm(pts[pts.length - 1].t)}` : "서버 기록 없음 (1분마다 쌓임)") : "";
    const W = svg.clientWidth || 200, H = 64;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (pts.length < 2) { svg.innerHTML = ""; return; }
    // 코스피·코스닥은 정규장 09:00~15:30 고정 축, 나스닥 선물은 기록 구간
    const t0 = d.recorded ? toMin(pts[0].t) : 9 * 60, t1 = d.recorded ? toMin(pts[pts.length - 1].t) : 15 * 60 + 30;
    const prev = d.prev_close ?? pts[0].c;
    const vals = [...pts.map((p) => p.c), prev];
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const x = (t) => 2 + ((toMin(t) - t0) / Math.max(1, t1 - t0)) * (W - 4);
    const y = (v) => H - 14 - ((v - lo) / span) * (H - 20);
    const last = pts[pts.length - 1].c, color = last >= prev ? "var(--up)" : "var(--down)";
    const line = pts.map((p) => `${x(p.t)},${y(p.c)}`).join(" ");
    const lx = x(pts[pts.length - 1].t);
    svg.innerHTML =
      `<polygon points="${x(pts[0].t)},${y(prev)} ${line} ${lx},${y(prev)}" fill="${color}" opacity=".12"/>` +
      `<line x1="0" x2="${W}" y1="${y(prev)}" y2="${y(prev)}" stroke="var(--muted)" stroke-dasharray="3 3" opacity=".7"/>` +
      `<polyline fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" points="${line}"/>` +
      `<circle cx="${lx}" cy="${y(last)}" r="2.5" fill="${color}"/>` +
      `<text x="2" y="${H - 1}">${d.recorded ? hhmm(pts[0].t) : "09:00"}</text>` +
      `<text x="${W - 2}" y="${H - 1}" text-anchor="end">${d.recorded ? hhmm(pts[pts.length - 1].t) : "15:30"}</text>`;
  });
}

async function loadThemes() {
  try {
    const d = await api("/api/themes");
    $("#themes-source").textContent = d.source === "naver" ? "네이버 금융 테마 · 등락률 상위" : "KB 업종랭킹 (네이버 테마 조회 실패 시 대체)";
    // 요약은 수치로만: 상승·하락 종목수와 주도주 등락률
    const sum = (t) => {
      const parts = [];
      if (t.total) parts.push(t.fall ? `${fmt(t.total)}종목 중 <b>${fmt(t.rise)} 상승</b> · ${fmt(t.fall)} 하락` : `<b>${fmt(t.total)}종목 모두 상승</b>`);
      if (t.leaders?.length) parts.push("주도주 " + t.leaders.map((s) => `<b>${esc(s.name)}</b> <span class="${cls(s.change_pct)}">${signed(s.change_pct, 2)}%</span>`).join(", "));
      return parts.join("<br>");
    };
    // 한 줄 요약: 상위 3개 테마 이름(괄호 설명 제외)과 평균 등락률
    const short = (n) => String(n).replace(/\(.*?\)/g, "").trim();
    const pcts = d.themes.map((t) => t.change_pct).filter((v) => v != null);
    const lead = d.themes.length ? `<p class="theme-lead">오늘은 <b>${d.themes.slice(0, 3).map((t) => esc(short(t.name))).join(" · ")}</b> 테마가 강세
      (상위 ${d.themes.length}개 평균 <span class="${cls(pcts[0])}">${signed(pcts.reduce((a, c) => a + c, 0) / (pcts.length || 1), 2)}%</span>)</p>` : "";
    $("#themes").innerHTML = d.themes.length ? lead + d.themes.map((t, i) => `
      <div class="theme-row"><span class="rank">${i + 1}</span><span class="tname">${esc(t.name)}</span>
      <span class="pct ${cls(t.change_pct)}">${signed(t.change_pct, 2)}%</span>
      ${sum(t) ? `<span class="tsum">${sum(t)}</span>` : ""}</div>`).join("") : `<div class="empty">테마 데이터 없음</div>`;
  } catch (e) { fail($("#themes"), e); }
}

// 금리 변화: bp(0.01%p)와, 전일 금리 대비 변동률(%)을 괄호로 - 예: 3.5bp (+0.79%)
function bondMove(b) {
  if (b.change == null) return "-";
  const prev = b.yield - b.change;
  const pct = prev ? (b.change / prev) * 100 : null;
  return `${fmt(Math.abs(b.change) * 100, 1)}bp` + (pct == null ? "" : ` (${signed(pct, 2)}%)`);
}

async function loadMacro() {
  try {
    const { usd_krw: u, treasury_10y: b } = await api("/api/macro");
    $("#usdkrw").innerHTML = u
      ? `<div class="v ${cls(u.change)}">${fmt(u.rate, 2)}</div><div class="${cls(u.change)}">${arrow(u.change)} ${fmt(Math.abs(u.change ?? 0), 2)} (${signed(u.change_pct, 2)}%)</div>`
      : `<div class="empty">데이터 없음</div>`;
    $("#bond-date").textContent = b && b.date ? `IVA10370 · ${ymd(b.date)}` : "IVA10370";
    $("#tsy10").innerHTML = b
      ? `<div class="v ${cls(b.change)}">${fmt(b.yield, 3)}%</div><div class="${cls(b.change)}">${arrow(b.change)} ${bondMove(b)}</div>`
      : `<div class="empty">데이터 없음</div>`;
  } catch (e) { fail($("#usdkrw"), e); fail($("#tsy10"), e); }
}

// ------------------------------------------------------------ futures chart
// HTS 0783 '투자자별매매동향-추이'처럼: 좌측 축 = KOSPI200 선물지수(검정),
// 우측 축 = 투자자별 선물 순매수 누적(억원), 우측 체크박스로 계열 선택
const SESSION_START = 8 * 60 + 45, SESSION_END = 15 * 60 + 45;
const toMin = (t) => +t.slice(0, 2) * 60 + +t.slice(2, 4);
const INVESTORS = [
  { key: "개인", label: "개인", color: "#f08c00" },
  { key: "외국인", label: "외인", color: "#e03131" },
  { key: "기관계", label: "기관", color: "#1c7ed6" },
  { key: "금융투자", label: "금투", color: "#2f9e44" },
  { key: "투신", label: "투신", color: "#ae3ec9" },
  { key: "은행", label: "은행", color: "#868e96" },
];
const INDEX_KEY = "지수", INDEX_COLOR = "var(--ink)";
const futSelected = new Set(loadSelected());

function loadSelected() {
  try { const v = JSON.parse(localStorage.getItem("futSelected")); if (Array.isArray(v) && v.length) return v; } catch {}
  return ["외국인", INDEX_KEY];
}
function saveSelected() { try { localStorage.setItem("futSelected", JSON.stringify([...futSelected])); } catch {} }

function renderChecks() {
  const items = [...INVESTORS.map((i) => ({ key: i.key, label: i.label, color: i.color })), { key: INDEX_KEY, label: "지수", color: INDEX_COLOR }];
  $("#fut-checks").innerHTML = items.map((i) => `
    <label style="--series:${i.color}"><input type="checkbox" data-key="${i.key}" ${futSelected.has(i.key) ? "checked" : ""}>
    <span class="sw2" style="background:${i.color}"></span>${i.label}</label>`).join("");
}
renderChecks();
$("#fut-checks").addEventListener("change", (e) => {
  const k = e.target.dataset.key;
  if (!k) return;
  e.target.checked ? futSelected.add(k) : futSelected.delete(k);
  saveSelected();
  drawFutures();
});

async function loadFutures() {
  try {
    futData = await api(`/api/futures?minutes=1`);
    const f = futData.futures, cf = futData.current_flows || {};
    $("#fut-name").textContent = `· ${f.name} (${f.code})`;
    $("#fut-head").innerHTML = `
      <span class="price">${fmt(f.price, 2)}</span>
      <span class="${cls(f.change)}">${arrow(f.change)} ${fmt(Math.abs(f.change ?? 0), 2)} (${signed(f.change_pct, 2)}%)</span>
      <span class="flow">미결제 ${fmt(f.open_interest)}</span>`;
    const names = INVESTORS.map((i) => i.key).filter((k) => k in cf);
    $("#fut-table").innerHTML = `<thead><tr><th></th>${names.map((n) => `<th>${n}</th>`).join("")}</tr></thead>
      <tbody><tr><td>순매수</td>${names.map((n) => `<td class="${cls(cf[n])}">${signed(cf[n])}</td>`).join("")}</tr></tbody>`;
    let note;
    if (!futData.recording) note = "데모 모드: 투자자별 선물 순매수 추이는 가짜 데이터입니다.";
    else if (!futData.flows.length) note = "투자자별 선물 순매수는 KB OpenAPI가 분 단위 추이를 제공하지 않아, 서버가 장중(08:45~15:45) 1분마다 기록합니다. 아직 기록이 없습니다.";
    else {
      const first = futData.flows[0].t;
      note = `순매수 추이는 서버가 1분마다 기록한 값입니다 (${ymd(futData.flow_date)}, ${futData.flows.length}건).` +
        (first > "0845" ? ` 08:45~${hhmm(first)}은 서버가 꺼져 있어 기록이 없습니다 (KB OpenAPI는 지난 분 단위 수급을 제공하지 않음).` : "") +
        " 서버를 장 시작 전에 켜 두면 하루 전체가 그려집니다.";
    }
    $("#fut-note").textContent = note;
    drawFutures();
  } catch (e) { fail($("#fut-head"), e); }
}

function drawFutures() {
  const svg = $("#fut-chart");
  if (!futData) return;
  // 가로로 긴 차트: 높이 = 폭의 절반 (180~420px) → 폰에서도 세로로 길어지지 않음
  const W = svg.clientWidth || 900, H = Math.round(Math.min(420, Math.max(180, W * 0.5)));
  svg.style.height = `${H}px`;
  const TICKS = H < 320 ? 4 : 8;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  // 폰처럼 좁으면 좌우 축 여백·글자를 줄여 그래프 영역을 넓힌다
  const narrow = W < 520;
  svg.classList.toggle("narrow", narrow);
  const L = narrow ? 44 : 66, R = narrow ? 40 : 64, T = 12, B = H - (narrow ? 20 : 26);
  const plotW = W - L - R;
  const x = (min) => L + ((min - SESSION_START) / (SESSION_END - SESSION_START)) * plotW;
  const bars = futData.bars;
  const investors = INVESTORS.filter((i) => futSelected.has(i.key));
  const showIndex = futSelected.has(INDEX_KEY);

  // 시간축: 08:45 ~ 15:45. 눈금선 30분(좁으면 1시간), 글자는 서로 겹치지 않게 간격을 두고 표시
  const step = plotW < 560 ? 60 : 30;
  const labelGap = narrow ? 30 : 40;
  const hm = (m) => narrow ? `${Math.floor(m / 60)}시` : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const ty = H - (narrow ? 5 : 8);
  let out = `<rect x="${L}" y="${T}" width="${plotW}" height="${B - T}" fill="none" stroke="var(--line)"/><g class="grid">`;
  let lastLabel = L;
  out += `<text x="${L}" y="${ty}" text-anchor="start">${narrow ? "8:45" : "08:45"}</text>`;
  for (let m = 9 * 60; m <= SESSION_END; m += step) {
    const xx = x(m);
    out += `<line x1="${xx}" x2="${xx}" y1="${T}" y2="${B}"/>`;
    if (xx - lastLabel >= labelGap && W - R - xx >= labelGap) {
      out += `<text x="${xx}" y="${ty}" text-anchor="middle">${hm(m)}</text>`;
      lastLabel = xx;
    }
  }
  out += `<text x="${W - R}" y="${ty}" text-anchor="end">15:45</text></g>`;

  // 좌측 축: KOSPI200 선물지수
  if (showIndex && bars.length) {
    const lo = Math.min(...bars.map((b) => b.c)), hi = Math.max(...bars.map((b) => b.c));
    const pad = (hi - lo) * 0.05 || 0.5;
    const y = (v) => B - ((v - (lo - pad)) / (hi - lo + pad * 2)) * (B - T);
    out += `<g class="grid">`;
    for (let i = 0; i <= TICKS; i++) {
      const v = lo - pad + ((hi - lo + pad * 2) * i) / TICKS;
      out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
             `<text x="${L - 4}" y="${y(v) + 4}" text-anchor="end">${fmt(v, narrow ? 0 : 2)}</text>`;
    }
    out += `</g>`;
    const pts = bars.map((b) => [x(toMin(b.t)), y(b.c)]);
    out += `<polyline fill="none" stroke="${INDEX_COLOR}" stroke-width="${narrow ? 2.2 : 3.6}" stroke-linejoin="round" stroke-linecap="round" points="${pts.join(" ")}"/>`;
  }

  // 우측 축: 투자자별 선물 순매수 누적 (선택된 계열 공통 축)
  const flows = futData.flows;
  // 실제로 기록된 값만 그린다 (기록 없는 구간을 이어 그리지 않음)
  const seriesOf = (key) => flows.filter((f) => f[key] != null).map((f) => ({ t: f.t, v: f[key] }));
  const all = investors.map((i) => ({ ...i, s: seriesOf(i.key) })).filter((i) => i.s.length);
  if (all.length) {
    const vals = all.flatMap((i) => i.s.map((p) => p.v));
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const pad = (hi - lo) * 0.05 || 100;
    lo -= pad; hi += pad;
    const y = (v) => B - ((v - lo) / (hi - lo)) * (B - T);
    const tick = niceStep((hi - lo) / TICKS);
    for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick) {
      out += `<text x="${W - R + 6}" y="${y(v) + 4}">${fmt(v)}</text>`;
      if (!showIndex) out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/>`;
    }
    for (const inv of all) {
      const width = inv.key === "외국인" ? (narrow ? 2.2 : 3.6) : (narrow ? 1.5 : 2.4);
      const pts = inv.s.map((p) => [x(toMin(p.t)), y(p.v)]);
      // 5분 넘게 기록이 끊긴 곳은 선을 끊는다 (모두 실선)
      let seg = [pts[0]];
      const flush = () => {
        if (seg.length > 1) out += `<polyline fill="none" stroke="${inv.color}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round" points="${seg.join(" ")}"/>`;
      };
      for (let k = 1; k < pts.length; k++) {
        if (toMin(inv.s[k].t) - toMin(inv.s[k - 1].t) > 5) { flush(); seg = [pts[k]]; } else seg.push(pts[k]);
      }
      flush();
      const [lx, ly] = pts[pts.length - 1];
      out += `<circle cx="${lx}" cy="${ly}" r="${width}" fill="${inv.color}"/>`;
    }
  }

  // 좌상단 범례 (HTS처럼 ■외국인 ■KOSPI200)
  const legend = [...investors.map((i) => [i.key, i.color]), ...(showIndex ? [["KOSPI200", INDEX_COLOR]] : [])];
  let lx = L + 8;
  for (const [name, color] of legend) {
    out += `<rect x="${lx}" y="${T + 7}" width="4" height="10" fill="${color}"/><text x="${lx + 7}" y="${T + 16}" style="fill:${color};font-weight:600">${name}</text>`;
    lx += 16 + name.length * 12;
  }
  out += `<line id="cross" class="cross" x1="0" x2="0" y1="${T}" y2="${B}" visibility="hidden"/>`;
  svg.innerHTML = out;
  svg.onmousemove = (e) => hover(e, x, plotW, L);
  svg.onmouseleave = () => { $("#fut-tip").hidden = true; $("#cross")?.setAttribute("visibility", "hidden"); };
}

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1)), n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function hover(e, x, plotW, L) {
  const svg = $("#fut-chart"), rect = svg.getBoundingClientRect();
  const px = ((e.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
  const min = SESSION_START + ((px - L) / plotW) * (SESSION_END - SESSION_START);
  const nearest = (arr) => arr.reduce((best, r) => (Math.abs(toMin(r.t) - min) < Math.abs(toMin(best.t) - min) ? r : best), arr[0]);
  const bar = futData.bars.length ? nearest(futData.bars) : null;
  const flow = futData.flows.length ? nearest(futData.flows) : null;
  if (!bar && !flow) return;
  const t = (bar || flow).t;
  const cross = $("#cross");
  cross.setAttribute("x1", x(toMin(t))); cross.setAttribute("x2", x(toMin(t))); cross.setAttribute("visibility", "visible");
  const invs = INVESTORS.filter((i) => futSelected.has(i.key) && flow && flow[i.key] != null);
  const tip = $("#fut-tip");
  tip.innerHTML = `<b>${hhmm(t)}</b>` +
    (bar ? `<br>KOSPI200 <b>${fmt(bar.c, 2)}</b>` : "") +
    invs.map((i) => `<br><span style="color:${i.color}">${i.key}</span> <b>${signed(flow[i.key])}</b>억`).join("") +
    (invs.length && flow.t !== t ? ` <span class="muted">(${hhmm(flow.t)})</span>` : "");
  tip.hidden = false;
  const left = e.clientX - rect.left;
  tip.style.left = left > rect.width / 2 ? `${left - tip.offsetWidth - 12}px` : `${left + 12}px`;
}

// ------------------------------------------------------------ quote
async function loadQuote(code = current.code) {
  const box = $("#quote");
  try {
    const q = await api(`/api/quote/${encodeURIComponent(code)}`);
    current = { code: q.code, quote: q };
    const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
    box.innerHTML = `
      <div class="quote-head">
        <span class="name">${esc(q.name)}</span><span class="muted">${esc(q.code)} ${esc(q.market)}</span>
      </div>
      <div class="quote-head">
        <span class="price ${cls(q.change)}">${fmt(q.price)}</span>
        <span class="${cls(q.change)}">${arrow(q.change)} ${fmt(Math.abs(q.change ?? 0))} (${signed(q.change_pct, 2)}%)</span>
      </div>
      <div class="quote-grid">
        ${kv("전일종가", fmt(q.prev_close))}${kv("시가", fmt(q.open))}
        ${kv("고가", `<span class="up">${fmt(q.high)}</span>`)}${kv("저가", `<span class="down">${fmt(q.low)}</span>`)}
        ${kv("상한가", fmt(q.upper_limit))}${kv("하한가", fmt(q.lower_limit))}
        ${kv("거래량", fmt(q.volume))}${kv("외국인비중", fmt(q.foreign_ratio, 2) + "%")}
        ${kv("매도1호가", fmt(q.ask1))}${kv("매수1호가", fmt(q.bid1))}
        ${kv("PER", fmt(q.per, 2))}${kv("PBR", fmt(q.pbr, 2))}
        ${kv("250일 최고", fmt(q.high_250))}${kv("250일 최저", fmt(q.low_250))}
      </div>`;
    fillPriceForStock(q);
    renderRule(q.buy_rule);
    updateEstimate();
    $("#pgm-stock-name").textContent = `${q.name} · IVU10450`;
  } catch (e) { fail(box, e); $("#rule-box").innerHTML = ""; }
  loadProgramStock();
}

function renderRule(rule) {
  const box = $("#rule-box");
  box.className = "rule-box " + (rule.allowed ? "ok" : "block");
  box.textContent = (rule.allowed ? "매수 가능 · " : "매수 불가 · ") + rule.reason;
  updateOrderButton();
}

// ------------------------------------------------------------ order form (지정가/시장가)
let priceCode = null;   // 가격칸에 현재가를 채워 넣은 종목 (종목이 바뀔 때만 다시 채움)
let power = null;       // 주문가능금액·보유수량

// KRX 호가가격단위 (2023년 개편, 코스피·코스닥 공통) - 서버 trading.tick_size와 동일
function tickSize(p) {
  for (const [limit, tick] of [[2000, 1], [5000, 5], [20000, 10], [50000, 50], [200000, 100], [500000, 500]]) if (p < limit) return tick;
  return 1000;
}
const side = () => document.querySelector("input[name=side]:checked").value;
const otype = () => document.querySelector("input[name=otype]:checked").value;
const readNum = (id) => parseInt(String($(id).value).replace(/[^\d]/g, ""), 10) || 0;
const writeNum = (id, v) => { $(id).value = v ? Number(v).toLocaleString("ko-KR") : ""; };

function priceProblem(p) {
  const q = current.quote;
  if (otype() !== "limit") return "";
  if (!p) return "가격을 입력하세요";
  if (p % tickSize(p)) return `호가단위(${fmt(tickSize(p))}원)에 맞지 않아요`;
  if (q && q.upper_limit && p > q.upper_limit) return "상한가를 넘었어요";
  if (q && q.lower_limit && p < q.lower_limit) return "하한가보다 낮아요";
  return "";
}

function updateOrderButton() {
  const s = side(), limit = otype() === "limit";
  const blocked = s === "buy" && current.quote && !current.quote.buy_rule.allowed;
  const bad = priceProblem(readNum("#price"));
  const btn = $("#order-btn");
  btn.disabled = !current.quote || blocked || !!bad || readNum("#qty") <= 0;
  btn.textContent = blocked && s === "buy" ? "매수 제한" : `${limit ? "지정가" : "시장가"} ${s === "buy" ? "매수" : "매도"} 주문`;
  btn.style.background = s === "buy" ? "var(--up)" : "var(--down)";
  btn.style.color = "#fff";
}

function updateEstimate() {
  const q = current.quote, n = readNum("#qty"), limit = otype() === "limit";
  const p = limit ? readNum("#price") : q?.price;
  $("#price-field").classList.toggle("disabled", !limit);
  const bad = priceProblem(readNum("#price"));
  $("#tick-hint").innerHTML = limit
    ? (bad ? `<span class="up">${bad}</span>` : `호가단위 ${fmt(tickSize(readNum("#price") || q?.price || 0))}원${q?.upper_limit ? ` · 상한 ${fmt(q.upper_limit)} / 하한 ${fmt(q.lower_limit)}` : ""}`)
    : "시장가: 현재가로 즉시 체결";
  $("#est").textContent = q && p ? `예상금액 ${fmt(p * n)}원 (${q.name} ${fmt(n)}주 × ${fmt(p)}원)` : "";
  if (power) {
    const held = power.positions?.[current.code]?.qty || 0;
    $("#power").textContent = side() === "buy" ? `주문가능 ${fmt(power.available)}원` : `보유 ${fmt(held)}주`;
  }
  updateOrderButton();
}

async function loadPower() {
  try { power = await api("/api/buying-power"); updateEstimate(); } catch { /* 표시만 생략 */ }
}

function fillPriceForStock(q) {
  $("#order-name").textContent = `${q.name} · 현재가 ${fmt(q.price)}원`;
  if (priceCode !== q.code) {   // 새 종목을 고르면 가격칸을 현재가로 채움 (같은 종목 새로고침 때는 입력값 유지)
    priceCode = q.code;
    writeNum("#price", q.price);
  }
}

async function loadProgramStock() {
  const t = $("#program-stock");
  try {
    const rows = await api(`/api/program/stock/${current.code}`);
    t.innerHTML = rows.length ? `<thead><tr><th>시간</th><th class="r">현재가</th><th class="r">등락률</th><th class="r">순매수</th><th class="r">증감</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${hhmm(r.time)}</td><td class="r">${fmt(r.price)}</td>
        <td class="r ${cls(r.change_pct)}">${signed(r.change_pct, 2)}%</td>
        <td class="r ${cls(r.net_buy)}">${signed(r.net_buy)}</td>
        <td class="r ${cls(r.net_buy_delta)}">${signed(r.net_buy_delta)}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">데이터 없음</td></tr></tbody>`;
  } catch (e) { fail(t, e); }
}

// ------------------------------------------------------------ orders
async function loadOrders() {
  try {
    const orders = await api("/api/orders");
    const STATUS = { filled: "체결", open: "미체결", rejected: "거절", cancelled: "취소" };
    $("#orders").innerHTML = orders.length ? `<thead><tr><th>시각</th><th>종목</th><th>구분</th><th class="r">수량</th><th class="r">주문가</th><th class="r">체결가</th><th>상태</th><th>사유</th></tr></thead><tbody>` +
      orders.map((o) => `<tr><td>${esc(o.ts.slice(5, 16))}</td><td>${esc(o.name)}</td>
        <td class="${o.side === "buy" ? "up" : "down"}">${o.order_type === "limit" ? "지정" : "시장"} ${o.side === "buy" ? "매수" : "매도"}</td>
        <td class="r">${fmt(o.qty)}</td><td class="r">${o.limit_price ? fmt(o.limit_price) : "-"}</td><td class="r">${o.status === "filled" ? fmt(o.price) : "-"}</td>
        <td><span class="pill ${o.status}">${STATUS[o.status] || o.status}</span>
          ${o.status === "open" ? `<button type="button" class="mini-btn" data-cancel="${o.id}">취소</button>` : ""}</td>
        <td class="muted">${esc(o.reason)}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">주문 내역이 없습니다.</td></tr></tbody>`;
  } catch (e) { fail($("#orders"), e); }
}

// ------------------------------------------------------------ events
$("#quick").innerHTML = QUICK.map(([c, n]) => `<button type="button" data-code="${c}">${n}</button>`).join("");
$("#quick").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-code]");
  if (b) { $("#code").value = b.textContent; loadQuote(b.dataset.code); }
});

// 종목 검색: 한글 이름(또는 코드) 입력 → 자동완성 목록 → 선택
let suggestItems = [], suggestIdx = -1, suggestTimer = null;
function renderSuggest() {
  const ul = $("#suggest"), input = $("#code");
  if (!suggestItems.length) { ul.hidden = true; input.setAttribute("aria-expanded", "false"); return; }
  ul.innerHTML = suggestItems.map((s, i) => s.none
    ? `<li class="none">${esc(s.none)}</li>`
    : `<li role="option" data-i="${i}" class="${i === suggestIdx ? "on" : ""}"><span>${esc(s.name)}</span><span class="c">${esc(s.code)}</span></li>`).join("");
  ul.hidden = false; input.setAttribute("aria-expanded", "true");
}
function chooseStock(s) {
  $("#code").value = s.name; suggestItems = []; suggestIdx = -1; renderSuggest();
  loadQuote(s.code);
}
async function fetchSuggest(q) {
  try { return await api(`/api/search?q=${encodeURIComponent(q)}`); } catch { return []; }
}
$("#code").addEventListener("input", () => {
  clearTimeout(suggestTimer);
  const q = $("#code").value.trim();
  if (!q) { suggestItems = []; renderSuggest(); return; }
  suggestTimer = setTimeout(async () => {
    const items = await fetchSuggest(q);
    if ($("#code").value.trim() !== q) return;  // 그 사이 입력이 바뀌었으면 무시
    suggestItems = items.length ? items : [{ none: "찾는 종목이 없어요" }];
    suggestIdx = items.length ? 0 : -1;
    renderSuggest();
  }, 150);
});
$("#code").addEventListener("keydown", (e) => {
  const n = suggestItems.filter((s) => !s.none).length;
  if (e.key === "ArrowDown" && n) { e.preventDefault(); suggestIdx = (suggestIdx + 1) % n; renderSuggest(); }
  else if (e.key === "ArrowUp" && n) { e.preventDefault(); suggestIdx = (suggestIdx - 1 + n) % n; renderSuggest(); }
  else if (e.key === "Escape") { suggestItems = []; renderSuggest(); }
});
$("#code").addEventListener("blur", () => setTimeout(() => { suggestItems = []; renderSuggest(); }, 150));
$("#suggest").addEventListener("mousedown", (e) => {
  const li = e.target.closest("li[data-i]");
  if (li) { e.preventDefault(); chooseStock(suggestItems[+li.dataset.i]); }
});
$("#search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#code").value.trim();
  if (!q) return;
  if (suggestIdx >= 0 && suggestItems[suggestIdx] && !suggestItems[suggestIdx].none) return chooseStock(suggestItems[suggestIdx]);
  const items = await fetchSuggest(q);
  if (items.length) return chooseStock(items[0]);
  suggestItems = [{ none: `'${q}' 종목을 찾지 못했어요` }]; suggestIdx = -1; renderSuggest();
});
window.addEventListener("resize", () => { drawFutures(); drawMinis(); });
document.querySelectorAll("input[name=side], input[name=otype]").forEach((r) => r.addEventListener("change", updateEstimate));
for (const id of ["#qty", "#price"]) {
  $(id).addEventListener("input", updateEstimate);
  $(id).addEventListener("blur", () => { writeNum(id, readNum(id)); updateEstimate(); });  // 쉼표 넣어 보기 좋게
}
$("#order-form").addEventListener("click", (e) => {
  const b = e.target.closest("button.step");
  if (!b) return;
  const dir = +b.dataset.dir;
  if (b.dataset.target === "qty") {
    writeNum("#qty", Math.max(1, readNum("#qty") + dir));
  } else {
    const p = readNum("#price") || current.quote?.price || 0;
    // 호가단위에 맞춰 한 칸 이동 (단위가 바뀌는 경계에서는 내려갈 가격 기준 단위 사용)
    const aligned = Math.round(p / tickSize(p)) * tickSize(p);
    writeNum("#price", Math.max(1, dir > 0 ? aligned + tickSize(aligned) : aligned - tickSize(aligned - 1)));
  }
  updateEstimate();
});
$("#use-current").addEventListener("click", () => { if (current.quote) { writeNum("#price", current.quote.price); updateEstimate(); } });
$("#orders").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-cancel]");
  if (!b || !confirm("이 미체결 주문을 취소할까요?")) return;
  try { await api(`/api/orders/${b.dataset.cancel}/cancel`, { method: "POST" }); } catch (err) { alert(err.message); }
  loadOrders(); loadPower();
});

$("#order-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const s = side(), limit = otype() === "limit";
  const qty = readNum("#qty"), price = readNum("#price");
  const msg = $("#order-msg");
  if (!current.quote || qty <= 0 || (limit && priceProblem(price))) return;
  const label = s === "buy" ? "매수" : "매도";
  const how = limit ? `지정가 ${fmt(price)}원` : "시장가";
  if (!confirm(`[모의매매] ${current.quote.name} ${fmt(qty)}주 ${how} ${label} 하시겠습니까?`)) return;
  try {
    const o = await api("/api/orders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: current.code, side: s, qty, order_type: limit ? "limit" : "market", price: limit ? price : null }),
    });
    msg.className = "order-msg " + (o.status === "rejected" ? "bad" : "ok");
    msg.textContent = o.status === "filled" ? `${label} 체결: ${o.name} ${fmt(o.qty)}주 @ ${fmt(o.price)}원`
      : o.status === "open" ? `미체결 접수: ${o.name} ${fmt(o.qty)}주 ${how} ${label} · 가격이 닿으면 체결돼요`
      : `주문 거절: ${o.reason}`;
    if (o.rule) renderRule(o.rule);
  } catch (err) {
    msg.className = "order-msg bad"; msg.textContent = err.message;
  }
  loadOrders(); loadPower();
});

// ------------------------------------------------------------ boot
if (new URLSearchParams(location.search).has("report") || window.__SNAPSHOT__) document.body.classList.add("report");
if (window.__SNAPSHOT__) {
  // 텔레그램으로 보낸 스냅샷 파일: 서버 없이 내장 데이터로 동작
  const b = $("#snapshot-banner");
  b.textContent = `스냅샷 · ${window.__SNAPSHOT_TIME__} 기준 데이터입니다 (실시간 아님)`;
  b.hidden = false;
}
function refreshFast() { loadStatus().catch(() => {}); loadQuote(); loadMarket(); loadFutures(); loadOrders(); loadPower(); }
function refreshSlow() { loadMacro(); loadIntraday(); loadThemes(); }
refreshFast(); refreshSlow();
setInterval(refreshFast, 15000);
setInterval(refreshSlow, 60000);
