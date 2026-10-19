(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 1, sign = false) => (x == null ? "—" : (sign && x > 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
  const num = (x, d = 2) => (x == null ? "—" : Number(x).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const yuan = (x) => (x == null ? "—" : Math.round(x).toLocaleString("zh-CN"));
  const cls = (x) => (x == null ? "" : x > 0 ? "up" : x < 0 ? "down" : "");
  const stock = (name, code) => `${esc(name)}<span class="code">${esc(code)}</span>`;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // ---------- 主题
  const themeKey = "dash-theme";
  try { const t = localStorage.getItem(themeKey); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  $("#theme-btn").onclick = () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const nxt = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nxt;
    try { localStorage.setItem(themeKey, nxt); } catch (e) {}
    redrawCharts();
  };

  // ---------- 标签页
  const showTab = (t) => {
    $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    $$(".tab").forEach((s) => s.classList.toggle("on", s.id === "tab-" + t));
    try { history.replaceState(null, "", "#" + t); } catch (e) {}
    lazy(t);
  };
  $$("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

  const getJSON = (p) => fetch(p + "?v=" + Date.now()).then((r) => { if (!r.ok) throw new Error(p + " " + r.status); return r.json(); });
  const D = {};

  // ---------- 今日
  function renderToday(L) {
    $("#asof").textContent = `数据日期 ${L.date} · 更新于 ${L.generated_at}（北京时间）`;
    const reds = L.alerts.filter((a) => a.level === "red"), yel = L.alerts.filter((a) => a.level === "yellow");
    const b = $("#status-badge");
    if (reds.length) { b.className = "badge crit"; b.textContent = `数据异常 ${reds.length}`; }
    else { b.className = "badge ok"; b.textContent = "数据正常"; }
        $("#alerts").innerHTML = (reds.length ? "" : `<div class="alerts-ok">✓ 今日数据检查通过（${esc(L.quality.sources.join("；"))}）</div>`) +
      reds.map((a) => `<div class="alert red"><span class="ic">!</span><div><div class="t">${esc(a.title)}</div><div class="dt">${esc(a.detail)}</div></div></div>`).join("") +
      (yel.length ? `<div class="alert yellow"><span class="ic">•</span><div>${yel.map((a) => `<div class="t">${esc(a.title)}</div><div class="dt">${esc(a.detail)}</div>`).join('<div class="sep"></div>')}</div></div>` : "");

    const m = L.market;
    const tiles = [
      ["跌停家数", m.ldn, `主板非ST收盘跌停 · 主信号条件 ${m.main_ok ? "满足（>3）" : "不满足（≤3）"}`],
      ["市场宽度", pct(m.breadth), `收盘>MA20：${m.breadth_above}/${m.breadth_valid}`],
      ["赚钱效应 MA3", pct(m.eff_ma3, 2, true), `今日 ${pct(m.eff, 2, true)}（前一日前10名平均涨跌）`],
      ["涨 / 跌", `${m.up} / ${m.down}`, `涨停 ${m.limit_up} 家 · 有成交 ${m.n_traded} 只`],
    ];
    $("#market-tiles").innerHTML = tiles.map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");
    renderTop(L, 10);
  }
  function renderTop(L, n) {
    const rows = L.top30.filter((r) => r.rank <= n);
    $("#top-table").innerHTML = `<thead><tr><th>排名</th><th class="l">股票</th><th>20日涨幅</th><th>收盘</th><th>今日</th><th>MA3</th><th>收盘/MA3</th><th>流通市值(亿)</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${r.rank}</td><td class="l">${stock(r.name, r.code)} ${r.in_pool ? '<span class="pill pool">观察池</span>' : ""}</td>
        <td class="${cls(r.r20)}">${pct(r.r20, 2, true)}</td><td>${num(r.close)}</td><td class="${cls(r.pct)}">${pct(r.pct, 2, true)}</td>
        <td>${num(r.ma3, 3)}</td><td class="${r.ma3 ? cls(r.close - r.ma3) : ""}">${r.ma3 ? (r.close >= r.ma3 ? "上方" : "下方") : "—"}</td><td>${num(r.fmv, 1)}</td></tr>`).join("") + "</tbody>";
  }
  $$("#top-n button").forEach((b) => (b.onclick = () => {
    $$("#top-n button").forEach((x) => x.classList.toggle("on", x === b)); renderTop(D.latest, +b.dataset.n);
  }));

  // ---------- 信号
  function renderSignal(L) {
    const accts = Object.entries(L.accounts);
    const sigs = L.signals;
    const box = $("#next-signals");
    if (!sigs.length) box.innerHTML = `<div class="empty">今日收盘没有产生信号，下一交易日开盘不买入。</div>`;
    else box.innerHTML = sigs.map((s) => {
      const main = s.kind === "主信号";
      const pct0 = main ? 0 : 0;
      const plans = accts.map(([n, a]) => {
        if (main) return `${n}：首笔 ≈ 总资产×${(a.main_pct * 100).toFixed(0)}% ≈ ${yuan(a.eq * a.main_pct)} 元`;
        const holds = a.holdings.map((h) => h.name).join("、");
        return a.holdings.length ? `${n}：开盘时持有 ${esc(holds)}，除非开盘就止盈卖完，否则按规则不买` : `${n}：空仓，可买；首笔 ≈ 总资产×${(a.fill_pct * 100).toFixed(0)}% ≈ ${yuan(a.eq * a.fill_pct)} 元（不足100股按100股）`;
      }).join("<br>");
      return `<div class="sig"><div class="h"><span class="pill ${main ? "main" : "fill"}">${esc(s.kind)}</span>${stock(s.name, s.code)}</div>
        <div class="b">信号日 ${esc(s.sig_date)} 收盘 ${num(s.close)}（<span class="${cls(s.pct)}">${pct(s.pct, 2, true)}</span>），流通市值 ${num(s.fmv, 1)} 亿${s.prev_rank ? `，前一日20日涨幅排名第 ${s.prev_rank}` : ""}${s.r20_at != null ? `，进池时20日涨幅 ${pct(s.r20_at)}` : ""}</div>
        <div class="b">下一交易日开盘集合竞价买；开盘价 = 涨停 ${num(s.next_lu)} 或 跌停 ${num(s.next_ld)} 则放弃。</div>
        <div class="b">${plans}</div></div>`;
    }).join("");

    const pool = L.pool;
    $("#pool-table").innerHTML = pool.length ? `<thead><tr><th class="l">股票</th><th class="l">状态</th><th>首次进前二</th><th>当时排名</th><th>今日排名</th><th>今日20日涨幅</th><th>收盘</th><th>今日</th><th>市值(亿)</th><th>剩余观察日</th><th>下一日触发区间（收盘）</th></tr></thead><tbody>` +
      pool.map((p) => `<tr><td class="l">${stock(p.name, p.code)}</td><td class="l"><span class="pill ${p.fired ? "" : p.status.startsWith("有效") ? "ok" : "warn"}">${esc(p.status)}</span></td>
      <td>${esc(p.first_date)}</td><td>${p.rank_at}</td><td>${p.rank_now ?? ">30"}</td><td class="${cls(p.r20_now)}">${pct(p.r20_now)}</td><td>${num(p.close)}</td>
      <td class="${cls(p.pct)}">${p.traded ? pct(p.pct, 2, true) : "停牌"}</td><td>${num(p.fmv, 1)}</td><td>${p.days_left}</td>
      <td>${p.fired ? '<span class="muted">已用掉买点</span>' : `${num(p.trig_lo)} ~ ${num(p.trig_hi)}`}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="l empty">观察池为空</td></tr></tbody>`;

    const fills = L.top30.filter((r) => r.rank <= 20 && !r.in_pool);
    $("#fill-table").innerHTML = `<thead><tr><th>排名</th><th class="l">股票</th><th>20日涨幅</th><th>收盘</th><th>市值(亿)</th><th>市值≥80亿</th><th>下一日触发区间（收盘）</th></tr></thead><tbody>` +
      fills.map((r) => {
        const hi = Math.floor(r.close * 0.95 * 100 + 1e-7) / 100, lo = Math.floor(r.close * 0.9 * 100 + 0.5 + 1e-7) / 100;
        return `<tr><td>${r.rank}</td><td class="l">${stock(r.name, r.code)}</td><td class="${cls(r.r20)}">${pct(r.r20)}</td><td>${num(r.close)}</td><td>${num(r.fmv, 1)}</td>
        <td>${r.fmv >= 80 ? "是" : '<span class="muted">否</span>'}</td><td>${r.fmv >= 80 ? `${num(lo)} ~ ${num(hi)}` : "—"}</td></tr>`;
      }).join("") + "</tbody>";
  }

  // ---------- 账户
  let curAcct = null, tradeFilter = "all";
  function renderAccountTabs() {
    const names = Object.keys(D.latest.accounts);
    curAcct = curAcct || names[0];
    $("#acct-seg").innerHTML = names.map((n) => `<button data-a="${esc(n)}" class="${n === curAcct ? "on" : ""}">${esc(n)}</button>`).join("");
    $$("#acct-seg button").forEach((b) => (b.onclick = () => { curAcct = b.dataset.a; renderAccountTabs(); renderAccount(); }));
  }
  function renderAccount() {
    const a = D.latest.accounts[curAcct], H = D.accounts[curAcct];
    const desc = `主信号首笔${(a.main_pct * 100).toFixed(0)}%（单票最多约${(a.main_pct * 250).toFixed(0)}%），补位首笔${(a.fill_pct * 100).toFixed(0)}%`;
    $("#acct-tiles").innerHTML = [
      ["总资产", yuan(a.eq) + " 元", `今日 <span class="${cls(a.day_pnl)}">${a.day_pnl > 0 ? "+" : ""}${yuan(a.day_pnl)}</span> · 现金 ${yuan(a.cash)}`],
      ["累计收益", pct(a.total_ret, 1, true), `年化 ${pct(a.cagr, 1)} · ${desc}`],
      ["最大回撤", pct(a.mdd), `当前距高点 ${pct(a.dd_now)}`],
      ["交易统计", `${H.stats.n} 笔`, `胜率 ${pct(H.stats.win)} · 盈亏比(金额) ${num(H.stats.pf)} · 空仓日 ${pct(H.stats.empty_ratio)}`],
    ].map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");

    $("#holdings").innerHTML = (a.today.length ? `<div class="hint">今日成交：${a.today.map((e) => `${esc(e.action)} ${esc(e.name)} ${e.shares}股 @ ${num(e.px)}${e.reason ? "（" + esc(e.reason) + "）" : ""}`).join("；")}</div>` : "") +
      (a.holdings.length ? a.holdings.map((h) => `<div class="hold"><div class="h"><span><span class="pill ${h.src === "主信号" ? "main" : "fill"}">${esc(h.src)}</span> ${stock(h.name, h.code)}</span>
        <span class="${cls(h.pnl)}">${h.pnl > 0 ? "+" : ""}${yuan(h.pnl)} 元（${pct(h.ret, 2, true)}）</span></div>
        <div class="kv"><div><span>持股</span>${h.shares} 股</div><div><span>持仓均价</span>${num(h.avg, 3)}</div><div><span>收盘</span>${num(h.close)}</div><div><span>市值</span>${yuan(h.mv)}</div>
        <div><span>止盈挂单（均价×1.05）</span>${num(h.tp)}${h.tp_reachable ? "" : `<br><span class="muted">明日涨停${num(h.next_lu)}，到不了</span>`}</div>
        <div><span>已买笔数</span>${h.lots}/3${h.next_add ? `（收盘≤${num(h.next_add.trigger)} 补仓）` : ""}</div>
        <div><span>时间止损</span>${h.pend ? "封跌停顺延卖出中" : `再过 ${h.time_stop_in} 个交易日收盘（未止盈时）`}</div>
        <div><span>买入 / 信号日</span>${esc(h.entry)} / ${esc(h.sig)}</div></div></div>`).join("") : `<div class="empty">空仓</div>`);

    $("#year-table").innerHTML = `<thead><tr><th>年份</th><th>年初</th><th>年末</th><th>年收益</th><th>年内最大回撤</th><th>交易笔数</th><th>胜率</th><th>主信号盈亏</th><th>补位盈亏</th></tr></thead><tbody>` +
      H.yearly.map((y) => `<tr><td>${y.year}</td><td>${yuan(y.start)}</td><td>${yuan(y.end)}</td><td class="${cls(y.ret)}">${pct(y.ret, 1, true)}</td><td>${pct(y.mdd)}</td><td>${y.n}</td><td>${pct(y.win)}</td><td class="${cls(y.pnl_main)}">${yuan(y.pnl_main)}</td><td class="${cls(y.pnl_fill)}">${yuan(y.pnl_fill)}</td></tr>`).join("") + "</tbody>";
    renderTrades();
    drawEquity();
  }
  function renderTrades() {
    const H = D.accounts[curAcct];
    const rows = H.trades.filter((t) => tradeFilter === "all" || t.src === tradeFilter).slice().reverse();
    $("#trade-table").innerHTML = `<thead><tr><th class="l">来源</th><th class="l">股票</th><th>信号日</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th class="l">卖出原因</th><th>笔数</th><th>持有天数</th><th>累计买入</th><th>净盈亏</th><th>收益率</th></tr></thead><tbody>` +
      rows.map((t) => `<tr><td class="l"><span class="pill ${t.src === "主信号" ? "main" : "fill"}">${esc(t.src)}</span></td><td class="l">${stock(t.name, t.code)}</td><td>${t.sig}</td><td>${t.entry}</td><td>${num(t.entry_px)}</td><td>${t.exit}</td><td>${num(t.exit_px)}</td><td class="l">${esc(t.reason)}</td><td>${t.lots}</td><td>${t.hold}</td><td>${yuan(t.invested)}</td><td class="${cls(t.pnl)}">${yuan(t.pnl)}</td><td class="${cls(t.ret)}">${pct(t.ret, 2, true)}</td></tr>`).join("") + "</tbody>";
  }
  $$("#trade-filter button").forEach((b) => (b.onclick = () => {
    tradeFilter = b.dataset.f; $$("#trade-filter button").forEach((x) => x.classList.toggle("on", x === b)); renderTrades();
  }));
  $("#dl-trades").onclick = (e) => {
    e.preventDefault();
    const H = D.accounts[curAcct];
    const cols = [["src", "信号来源"], ["code", "代码"], ["name", "名称"], ["sig", "信号日"], ["entry", "买入日"], ["entry_px", "买入价"], ["exit", "卖出日"], ["exit_px", "卖出价"], ["reason", "卖出原因"], ["lots", "买入笔数"], ["hold", "持有天数"], ["invested", "累计买入(元)"], ["pnl", "净盈亏(元)"], ["ret", "收益率"]];
    const csv = "﻿" + cols.map((c) => c[1]).join(",") + "\n" + H.trades.map((t) => cols.map((c) => t[c[0]] ?? "").join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `F版_${curAcct}_交易记录_${D.latest.date}.csv`; a.click();
  };

  // ---------- 图表（原生 SVG，无外部依赖，国内打开也快）
  const RANGES = [["1y", "近1年", 250], ["3y", "近3年", 750], ["all", "全部", 1e9]];
  const ranges = {};
  function rangeBar(id, def, onChange) {
    const host = document.getElementById(id);
    const box = document.createElement("div"); box.className = "seg range";
    box.innerHTML = RANGES.map(([k, t]) => `<button data-r="${k}">${t}</button>`).join("");
    host.parentNode.insertBefore(box, host);
    ranges[id] = ranges[id] || def;
    const sync = () => $$("button", box).forEach((b) => b.classList.toggle("on", b.dataset.r === ranges[id]));
    $$("button", box).forEach((b) => (b.onclick = () => { ranges[id] = b.dataset.r; sync(); onChange(); }));
    sync();
  }
  const rangeN = (id) => RANGES.find((r) => r[0] === ranges[id])[2];
  function niceTicks(lo, hi, n = 5) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
    const t = []; for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) t.push(+v.toFixed(10));
    return t;
  }
  const NS = "http://www.w3.org/2000/svg";
  function svgChart(id, { dates, series, type = "line", fmt = (v) => v, area = false, zeroLine = false }) {
    const el = document.getElementById(id);
    el.innerHTML = ""; el.style.position = "relative";
    const W = el.clientWidth || 600, H = el.clientHeight || 300;
    const m = { l: 52, r: 12, t: 10, b: 24 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, n = dates.length;
    const vals = series.flatMap((s) => s.values).filter((v) => v != null && isFinite(v));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (type === "bar" || zeroLine) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
    const ticks = niceTicks(lo, hi, H < 200 ? 3 : 5); lo = ticks[0]; hi = ticks[ticks.length - 1];
    const x = (i) => m.l + (n <= 1 ? iw / 2 : (type === "bar" ? (i + 0.5) * iw / n : i * iw / (n - 1)));
    const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;
    const line = cssVar("--line"), t2 = cssVar("--text-2"), t3 = cssVar("--text-3");
    let g = `<svg xmlns="${NS}" width="${W}" height="${H}" style="display:block;font-size:11px">`;
    ticks.forEach((v) => { g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${line}" stroke-width="1"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end" fill="${t2}">${fmt(v)}</text>`; });
    const nl = Math.max(2, Math.floor(iw / 90));
    for (let k = 0; k < nl; k++) { const i = Math.round(k * (n - 1) / (nl - 1)); g += `<text x="${x(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === nl - 1 ? "end" : "middle"}" fill="${t2}">${dates[i] || ""}</text>`; }
    series.forEach((s) => {
      if (type === "bar") {
        const bw = Math.max(1, Math.min(6, iw / n - 1));
        let path = "";
        s.values.forEach((v, i) => { if (v == null) return; const y0 = y(0), y1 = y(v); path += `M${x(i) - bw / 2},${y0}V${Math.min(y0, y1)}h${bw}V${y0}Z`; });
        g += `<path d="${path}" fill="${s.color}"/>`;
      } else {
        let d = "", started = false;
        s.values.forEach((v, i) => { if (v == null || !isFinite(v)) { started = false; return; } d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); started = true; });
        if (area) g += `<path d="${d}L${x(n - 1)},${y(Math.min(0, hi))}L${x(0)},${y(Math.min(0, hi))}Z" fill="${s.color}" fill-opacity=".14"/>`;
        g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" stroke-opacity="${s.opacity || 1}" stroke-linejoin="round"/>`;
      }
    });
    g += `<line id="${id}-x" y1="${m.t}" y2="${m.t + ih}" stroke="${t3}" stroke-dasharray="3 3" visibility="hidden"/>`;
    series.forEach((s, k) => { g += `<circle id="${id}-d${k}" r="4" fill="${s.color}" stroke="${cssVar("--surface")}" stroke-width="2" visibility="hidden"/>`; });
    g += `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" id="${id}-hit"/></svg>`;
    el.insertAdjacentHTML("beforeend", g);
    const tip = document.createElement("div"); tip.className = "tip"; el.appendChild(tip);
    const hit = document.getElementById(id + "-hit"), xl = document.getElementById(id + "-x");
    const move = (ev) => {
      const r = el.getBoundingClientRect(), cx = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      let i = type === "bar" ? Math.floor((cx - m.l) / iw * n) : Math.round((cx - m.l) / iw * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      xl.setAttribute("x1", x(i)); xl.setAttribute("x2", x(i)); xl.setAttribute("visibility", "visible");
      series.forEach((s, k) => { const c = document.getElementById(`${id}-d${k}`), v = s.values[i];
        if (type === "bar" || v == null) { c.setAttribute("visibility", "hidden"); return; }
        c.setAttribute("cx", x(i)); c.setAttribute("cy", y(v)); c.setAttribute("visibility", "visible"); });
      tip.innerHTML = `<div class="tip-d">${dates[i]}</div>` + series.map((s) => `<div><i style="background:${s.color}"></i>${esc(s.name)}<b>${s.values[i] == null ? "—" : fmt(s.values[i], true)}</b></div>`).join("");
      tip.style.display = "block";
      const tw = tip.offsetWidth; tip.style.left = Math.min(W - tw - 4, Math.max(4, x(i) + 12 - (x(i) + 12 + tw > W ? tw + 24 : 0))) + "px"; tip.style.top = m.t + 4 + "px";
    };
    const leave = () => { tip.style.display = "none"; xl.setAttribute("visibility", "hidden"); series.forEach((s, k) => document.getElementById(`${id}-d${k}`).setAttribute("visibility", "hidden")); };
    hit.addEventListener("mousemove", move); hit.addEventListener("touchmove", move, { passive: true });
    hit.addEventListener("mouseleave", leave); hit.addEventListener("touchend", leave);
  }
  let rangesInit = {};
  function drawEquity() {
    const names = Object.keys(D.accounts), colors = [cssVar("--s1"), cssVar("--s2")];
    $("#eq-legend").innerHTML = names.map((n, i) => `<span><i style="background:${colors[i]}"></i>${esc(n)}</span>`).join("");
    if (!rangesInit.eq) { rangeBar("eq-chart", "all", drawEquity); rangesInit.eq = 1; }
    const all = D.accounts[names[0]].eq, k = Math.max(0, all.length - rangeN("eq-chart"));
    const dates = all.slice(k).map((x) => x[0]);
    const wan = (v, full) => full ? yuan(v) + " 元" : (v / 10000).toFixed(v >= 1e5 ? 0 : 1) + "万";
    svgChart("eq-chart", { dates, fmt: wan, series: names.map((n, i) => ({ name: n, color: colors[i], values: D.accounts[n].eq.slice(k).map((x) => x[1]), width: n === curAcct ? 2 : 1.5, opacity: n === curAcct ? 1 : 0.5 })) });
    const eq = D.accounts[curAcct].eq.map((x) => x[1]); let pk = -Infinity;
    const dd = eq.map((v) => { pk = Math.max(pk, v); return v / pk - 1; }).slice(k);
    svgChart("dd-chart", { dates, area: true, fmt: (v, full) => (v * 100).toFixed(full ? 1 : 0) + "%", series: [{ name: curAcct + " 回撤", color: colors[names.indexOf(curAcct)], values: dd, width: 1.5 }] });
  }
  function drawMarket() {
    const M = D.market;
    const mk = (id, idx, fmt, type, name) => {
      if (!rangesInit[id]) { rangeBar(id, "1y", () => mk(id, idx, fmt, type, name)); rangesInit[id] = 1; }
      const k = Math.max(0, M.rows.length - rangeN(id)), rows = M.rows.slice(k);
      svgChart(id, { dates: rows.map((r) => r[0]), type, fmt, zeroLine: idx === 3, series: [{ name, color: cssVar("--s1"), values: rows.map((r) => r[idx]), width: 1.5 }] });
    };
    mk("br-chart", 2, (v, f) => (v * 100).toFixed(f ? 1 : 0) + "%", "line", "市场宽度");
    mk("ldn-chart", 1, (v) => Math.round(v), "bar", "跌停家数");
    mk("eff-chart", 3, (v, f) => (v * 100).toFixed(f ? 2 : 1) + "%", "line", "赚钱效应MA3");
  }
  function redrawCharts() {
    if ($("#tab-account").classList.contains("on") && D.accounts) drawEquity();
    if ($("#tab-market").classList.contains("on") && D.market) drawMarket();
  }
  let rsz; window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(redrawCharts, 150); });

  // ---------- 数据质量
  function renderRuns(R) {
    const lv = { red: "数据异常", yellow: "提醒", info: "记录" };
    $("#runs").innerHTML = R.map((r) => {
      const reds = r.alerts.filter((a) => a.level === "red");
      const st = Object.entries(r.stats || {}).map(([d, s]) => `${d}：原始${s.rows}条，有效${s.clean}只，剔除${s.dropped}，前收变化${s.preclose_changed}${s.cross_check && s.cross_check.common ? `，与新浪核对${s.cross_check.common}只不一致${s.cross_check.mismatch ?? 0}` : ""}`).join("；");
      return `<div class="run"><div class="h"><b>${esc(r.date)}</b><span class="pill ${reds.length ? "warn" : "ok"}">${reds.length ? "有数据异常" : "正常"}</span><span class="muted">运行于 ${esc(r.run_at)}</span></div>
      <div class="src">来源：${esc((r.sources || []).join("；"))}${st ? "<br>" + esc(st) : ""}</div>
      ${r.alerts.length ? "<ul>" + r.alerts.map((a) => `<li>[${lv[a.level]}] ${esc(a.title)}${a.detail ? "：" + esc(a.detail) : ""}</li>`).join("") + "</ul>" : ""}</div>`;
    }).join("") || '<div class="empty">暂无记录</div>';
  }

  // ---------- 规则
  $("#rules").innerHTML = `<h2>F版·首次到达Top2 回撤买法（与回测代码一致）</h2><ol>
  <li><b>股票池</b>：沪深主板 600/601/603/605/000/001/002/003；剔除名称含 ST/退；上市满30个交易日（含当天第31个成交日起参与排名）。</li>
  <li><b>每日排名</b>：收盘后 20日涨幅 = 复权收盘 / 20个市场交易日前复权收盘 − 1（停牌沿用前值），降序。</li>
  <li><b>观察池</b>：某日收盘排名第1或第2，且之前20个交易日从未进过前2 → 进池，观察其后第1~10个交易日；每次进池只取第一个买点。</li>
  <li><b>主信号</b>：观察期内某日收盘跌5%~10%（收盘≤前收×0.95 且 ≥跌停价）、非一字跌停；当天流通市值≥80亿；当天全市场主板非ST收盘跌停家数&gt;3。</li>
  <li><b>补位信号</b>：前一日20日涨幅前20，当天跌5%~10%、非一字、市值≥80亿，且不在任何观察期内（不要求跌停家数）。</li>
  <li><b>买入</b>：次日开盘集合竞价；停牌、开盘价=涨停或跌停则放弃；股数四舍五入到100股（最少100股）。</li>
  <li><b>仓位</b>：稳健版主信号首笔=前一日收盘总资产×20%，补到40%、50%；进取版30%→60%→75%；补位首笔4%→8%→10%。最多同时4只；补位只在当天开盘（处理完开盘卖出后）一只都没持有时才买；同类信号按排名优先。</li>
  <li><b>止盈</b>：买入次日起挂 均价×1.05（向上取到分）；开盘≥该价按开盘价成交，否则盘中最高价高于该价至少1分才算成交。</li>
  <li><b>补仓</b>：收盘≤均价×0.97 且未满3笔 → 按收盘价补到下一档；买入当天、一字跌停、收盘在跌停价不补。</li>
  <li><b>时间止损</b>：最后一次买入后满3个该股交易日仍未止盈 → 第3天收盘卖出；封跌停卖不出则顺延。</li>
  <li><b>费用</b>：佣金0.0085%、过户费0.001%双边；印花税卖出 2023-08-28 前0.1%、之后0.05%；无滑点。</li></ol>
  <h2>市场指标</h2><ol>
  <li><b>市场宽度</b>：复权收盘价 &gt; 自身最近20个交易日复权收盘均值（MA20）的主板股票数 ÷ 有MA20的主板股票数。只报数值，不设过滤线。</li>
  <li><b>跌停家数</b>：主板非ST收盘价 ≤ 跌停价的股票数。</li>
  <li><b>赚钱效应</b>：前一日20日涨幅前10名今天的平均涨跌幅；MA3 为最近3日均值。</li></ol>
  <h2>自动更新</h2><ol>
  <li>每个交易日北京时间 15:40、16:10、17:10、19:40 由 GitHub Actions 运行；已更新过的交易日自动跳过，不会重复处理、重复发信。</li>
  <li>主数据源腾讯行情（逐只核对行情日期）；新浪全市场列表交叉核对、补缺并发现新股；东方财富兜底。任何补位/切换都会在页面和邮件里标注。</li>
  <li>有效股票数不足上一交易日的97%等关键问题 → 当天不更新、不推进回测，之后的定时任务重试；最后一次仍失败会发失败邮件。</li>
  <li>漏跑的交易日（例如 GitHub 当天没触发）会在下一次运行时用个股日K回补，并标注“历史重建”。</li></ol>`;

  // ---------- 启动
  const loaded = {};
  function lazy(t) {
    if (!D.latest) return;
    if (t === "signal" && !loaded.signal) { renderSignal(D.latest); loaded.signal = 1; }
    if (t === "account") {
      const go = () => { if (!loaded.account) { renderAccountTabs(); renderAccount(); loaded.account = 1; } else drawEquity(); };
      D.accounts ? go() : getJSON("data/accounts.json").then((x) => { D.accounts = x; go(); });
    }
    if (t === "market") { D.market ? drawMarket() : getJSON("data/market.json").then((x) => { D.market = x; drawMarket(); }); }
    if (t === "quality" && !loaded.quality) { getJSON("data/runs.json").then((x) => { renderRuns(x); loaded.quality = 1; }); }
  }
  window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if ($(`#tabs button[data-tab="${t}"]`)) showTab(t); });
  getJSON("data/latest.json").then((L) => {
    D.latest = L; renderToday(L);
    const t = (location.hash || "#today").slice(1);
    showTab($(`#tabs button[data-tab="${t}"]`) ? t : "today");
  }).catch((e) => {
    $("#asof").textContent = "数据加载失败：" + e.message;
  });
})();
