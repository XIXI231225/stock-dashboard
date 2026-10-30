// 在网页上直接记录实盘操作、触发更新（通过 GitHub API）。
// 令牌只存在本机浏览器（localStorage），不会写进仓库；没有令牌的访客只能看、不能改。
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x, d = 2) => (x == null || x === "" ? "" : Number(x).toFixed(d));

  // ---------- 仓库（从网址推出来：<用户>.github.io/<仓库>/）
  const REPO = (() => {
    const h = location.hostname.match(/^([^.]+)\.github\.io$/i);
    const p = location.pathname.split("/").filter(Boolean)[0];
    return h && p ? `${h[1]}/${p}` : "XIXI231225/stock-dashboard";
  })();
  const API = "https://api.github.com/repos/" + REPO;
  const TRADES = "live/trades.csv", CONFIG = "live/config.json", WORKFLOW = "daily.yml";
  const HEAD = ["日期", "代码", "名称", "操作", "价格", "股数", "费用", "来源", "信号日", "备注"];

  const KEY = "dash-gh-token";
  const token = () => { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } };
  const setToken = (t) => { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) {} };

  async function gh(path, opt = {}) {
    const r = await fetch(path.startsWith("http") ? path : API + path, {
      ...opt,
      headers: { Accept: "application/vnd.github+json", Authorization: "Bearer " + token(), "X-GitHub-Api-Version": "2022-11-28",
        ...(opt.body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store",
    });
    if (r.status === 204) return null;
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const m = j.message || r.statusText;
      if (r.status === 401) throw new Error("令牌无效或已过期，请在“网页操作设置”里重新填写");
      if (r.status === 403 || r.status === 404) throw new Error(`没有权限（${m}）：令牌需要本仓库的 Contents 和 Actions 读写权限`);
      if (r.status === 409) throw new Error("文件刚被别处改过，请重试");
      throw new Error(`${r.status} ${m}`);
    }
    return j;
  }
  const b64dec = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\s/g, "")), (c) => c.charCodeAt(0)));
  const b64enc = (s) => { const b = new TextEncoder().encode(s); let x = ""; for (let i = 0; i < b.length; i += 0x8000) x += String.fromCharCode(...b.subarray(i, i + 0x8000)); return btoa(x); };
  async function getFile(p) { const j = await gh(`/contents/${p}?ref=main`); return { text: b64dec(j.content), sha: j.sha }; }
  async function putFile(p, text, sha, msg) {
    const j = await gh(`/contents/${p}`, { method: "PUT", body: JSON.stringify({ message: msg, content: b64enc(text), sha, branch: "main" }) });
    return j.commit.sha;
  }

  // ---------- CSV
  function parseCSV(text) {
    const rows = []; let row = [], f = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
      else if (c === '"') q = true;
      else if (c === ",") { row.push(f); f = ""; }
      else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(f); rows.push(row); row = []; f = ""; }
      else f += c;
    }
    if (f !== "" || row.length) { row.push(f); rows.push(row); }
    return rows.filter((r) => r.some((x) => x.trim() !== ""));
  }
  const cell = (v) => { v = String(v ?? ""); return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const toLine = (o) => HEAD.map((h) => cell(o[h])).join(",");
  function splitTrades(text) {
    const bom = text.startsWith("﻿") ? "﻿" : "";
    const rows = parseCSV(text.replace(/^﻿/, ""));
    const head = rows.shift() || HEAD;
    return { bom, head, recs: rows.map((r) => Object.fromEntries(head.map((h, i) => [h.trim(), (r[i] ?? "").trim()]))) };
  }
  const joinTrades = ({ bom, recs }) => bom + HEAD.join(",") + "\n" + recs.map(toLine).join("\n") + "\n";
  const sameRec = (a, b) => HEAD.every((h) => (a[h] ?? "") === (b[h] ?? ""));

  // ---------- 进度提示（右下角）
  const toast = document.createElement("div"); toast.id = "ops-toast"; document.body.appendChild(toast);
  let toastTimer;
  function say(html, kind = "", stay = true) {
    clearTimeout(toastTimer);
    toast.className = "on " + kind; toast.innerHTML = html + `<button class="x" title="关闭">×</button>`;
    toast.querySelector(".x").onclick = () => (toast.className = "");
    if (!stay) toastTimer = setTimeout(() => (toast.className = ""), 6000);
  }

  // 等 Actions 跑完 → 刷新页面数据
  async function waitRun(filter, label) {
    const t0 = Date.now(); let run = null;
    while (Date.now() - t0 < 12 * 60e3) {
      await new Promise((r) => setTimeout(r, run ? 8000 : 5000));
      try {
        const j = await gh(`/actions/workflows/${WORKFLOW}/runs?per_page=10&${filter.q}`);
        run = (j.workflow_runs || []).find(filter.match) || run;
      } catch (e) { continue; }
      const sec = Math.round((Date.now() - t0) / 1000);
      if (!run) { say(`<b>${label}</b><div>等待 GitHub 开始运行…（${sec} 秒）</div>`); continue; }
      const link = `<a href="${run.html_url}" target="_blank" rel="noopener">运行详情</a>`;
      if (run.status !== "completed") {
        say(`<b>${label}</b><div>${run.status === "queued" ? "排队中" : "运行中"}…（${sec} 秒，通常 1–3 分钟）· ${link}</div>`);
        continue;
      }
      if (run.conclusion === "success") {
        say(`<b>${label}：完成</b><div>正在刷新页面…</div>`, "ok");
        setTimeout(() => location.reload(), 4000);   // 给 Pages 一点生效时间
      } else {
        say(`<b>${label}：失败（${esc(run.conclusion)}）</b><div>${link}</div>`, "bad");
      }
      return;
    }
    say(`<b>${label}</b><div>等了很久还没结束，稍后手动刷新页面看看。</div>`, "bad");
  }

  async function triggerUpdate() {
    if (!token()) { openSetup(); say("先在 <b>实盘 → 网页操作设置</b> 填一次 GitHub 令牌", "", false); return; }
    const since = new Date(Date.now() - 60e3).toISOString();
    say("<b>更新行情</b><div>正在通知 GitHub…</div>");
    try {
      await gh(`/actions/workflows/${WORKFLOW}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main", inputs: { mode: "daily" } }) });
    } catch (e) { say(`<b>更新失败</b><div>${esc(e.message)}</div>`, "bad"); return; }
    waitRun({ q: "event=workflow_dispatch", match: (r) => r.created_at >= since }, "更新行情（收盘后才有当天数据；已是最新会直接跳过）");
  }

  // ---------- 记录表单
  let D = null, names = null, editing = null;   // editing = 正在修改的原记录
  const queue = [];

  const today = () => { const d = new Date(Date.now() + 8 * 3600e3); return d.toISOString().slice(0, 10); };   // 北京日期
  const normCode = (c) => { c = String(c || "").trim().toLowerCase(); if (!c) return ""; if (/^(sh|sz)\d{6}$/.test(c)) return c; c = c.replace(/\D/g, "").padStart(6, "0"); return (c[0] === "6" ? "sh" : "sz") + c; };
  async function nameOf(code) {
    if (!names) {
      names = {};
      try {
        const t = await fetch("data/snapshot.csv").then((r) => r.text());
        for (const ln of t.replace(/^﻿/, "").split("\n").slice(1)) { const [c, n] = ln.split(","); if (c) names[c] = n; }
      } catch (e) {}
      (D?.live?.holdings || []).forEach((h) => (names[h.code] = h.name));
      (D?.latest?.signals || []).forEach((s) => (names[s.code] = s.name));
    }
    return names[code] || "";
  }

  function picks() {
    const out = [];
    (D?.live?.holdings || []).forEach((h) => out.push({ v: "h:" + h.code, t: `持仓 · ${h.name} ${h.code}（${h.shares}股）`, h }));
    (D?.latest?.signals || []).forEach((s) => { if (!out.some((o) => o.v.endsWith(s.code))) out.push({ v: "s:" + s.code, t: `明日${s.kind} · ${s.name} ${s.code}`, s }); });
    return out;
  }

  function formHTML() {
    const opts = picks().map((o) => `<option value="${o.v}">${esc(o.t)}</option>`).join("");
    return `<div class="opsf">
      <label>日期<input type="date" id="f-date"></label>
      <label>操作<select id="f-op">${["买入", "补仓", "卖出", "入金", "出金"].map((x) => `<option>${x}</option>`).join("")}</select></label>
      <label class="w2 stk">股票<select id="f-pick">${opts}<option value="other">其他（手填代码）</option></select></label>
      <label class="stk other">代码<input id="f-code" placeholder="如 600825" inputmode="numeric"></label>
      <label class="stk other">名称<input id="f-name" placeholder="自动识别"></label>
      <label><span id="f-px-l">成交价</span><input id="f-px" type="number" step="0.001" min="0" inputmode="decimal"></label>
      <label class="stk">股数<input id="f-sh" type="number" step="100" min="0" inputmode="numeric"></label>
      <label class="stk">费用<input id="f-fee" type="number" step="0.01" min="0" placeholder="不填按F版费率估算" inputmode="decimal"></label>
      <label class="stk">来源<select id="f-src"><option>主信号</option><option>补位</option><option>其他</option></select></label>
      <label class="stk">信号日<input type="date" id="f-sig"></label>
      <label class="w3">备注<input id="f-note" placeholder="可不填，如：开盘竞价 / 盘中止盈"></label>
    </div>
    <div class="opsb">
      <button class="primary" id="f-save">保存并更新网页</button>
      <button class="sec" id="f-add">再记一条</button>
      <button class="sec" id="f-cancel" hidden>取消修改</button>
      <span class="hint" id="f-msg"></span>
    </div>
    <div id="f-queue"></div>`;
  }

  function fillFromPick() {
    const v = $("#f-pick").value, op = $("#f-op").value;
    $("#ops-card").classList.toggle("is-other", v === "other");
    const o = picks().find((x) => x.v === v);
    if (!o) { $("#f-src").value = "其他"; return; }
    if (o.h) {
      const h = o.h;
      $("#f-src").value = ["主信号", "补位"].includes(h.src) ? h.src : "其他";
      $("#f-sig").value = h.sig || "";
      if (op === "卖出") { $("#f-sh").value = h.shares; $("#f-px").value = h.tp ?? ""; }
      else if (op === "补仓") { $("#f-sh").value = h.next_add?.approx_shares || ""; $("#f-px").value = ""; }
    } else if (o.s) {
      $("#f-src").value = o.s.kind === "主信号" ? "主信号" : "补位";
      $("#f-sig").value = o.s.sig_date || D.latest.date;
    }
  }
  function onOp(auto = true) {
    const op = $("#f-op").value, cash = op === "入金" || op === "出金";
    $("#ops-card").classList.toggle("is-cash", cash);
    $("#f-px-l").textContent = cash ? "金额（元）" : "成交价";
    if (!auto || cash) return;
    // 卖出/补仓默认选持仓；买入默认选明日信号
    const P = picks(), cur = $("#f-pick").value;
    const want = op === "买入" ? P.find((x) => x.s) : P.find((x) => x.h);
    if (want && !(op === "买入" ? cur.startsWith("s:") : cur.startsWith("h:"))) $("#f-pick").value = want.v;
    fillFromPick();
  }

  async function readForm() {
    const op = $("#f-op").value, cash = op === "入金" || op === "出金";
    const r = { 日期: $("#f-date").value, 操作: op, 价格: $("#f-px").value.trim(), 备注: $("#f-note").value.trim().replace(/[\r\n]+/g, " ") };
    if (!/^\d{4}-\d{2}-\d{2}$/.test(r.日期)) throw new Error("请选日期");
    if (!(+r.价格 > 0)) throw new Error(cash ? "请填金额" : "请填成交价");
    if (cash) return { ...r, 代码: "", 名称: "", 股数: "", 费用: "", 来源: "", 信号日: "" };
    let code, name;
    const v = $("#f-pick").value;
    if (v === "other") {
      code = normCode($("#f-code").value);
      if (!/^(sh|sz)\d{6}$/.test(code)) throw new Error("代码不对，填6位数字，如 600825");
      name = $("#f-name").value.trim() || (await nameOf(code));
    } else {
      const o = picks().find((x) => x.v === v) || {};
      code = v.slice(2); name = (o.h || o.s || {}).name || (await nameOf(code));
    }
    const sh = $("#f-sh").value.trim();
    if (!(+sh > 0)) throw new Error("请填股数");
    const fee = $("#f-fee").value.trim();
    if (fee && !(+fee >= 0)) throw new Error("费用不对");
    return { ...r, 代码: code, 名称: name, 股数: String(+sh), 费用: fee, 来源: $("#f-src").value, 信号日: $("#f-sig").value };
  }

  function resetForm(keepDate = true) {
    const d = keepDate ? $("#f-date").value : today();
    ["#f-px", "#f-sh", "#f-fee", "#f-note", "#f-code", "#f-name"].forEach((s) => ($(s).value = ""));
    $("#f-date").value = d || today();
    editing = null; $("#f-cancel").hidden = true; $("#f-save").textContent = "保存并更新网页";
    onOp();
  }
  const recText = (r) => `${r.日期} ${r.操作} ${r.名称 || ""}${r.代码 ? " " + r.代码 : ""} ${r.价格}${r.股数 ? " × " + r.股数 + "股" : "元"}${r.备注 ? "（" + r.备注 + "）" : ""}`;
  function drawQueue() {
    $("#f-queue").innerHTML = queue.length ? `<div class="hint" style="margin-bottom:4px">待提交：</div>` + queue.map((r, i) =>
      `<div class="qrow"><span>${esc(recText(r))}</span><button class="link" data-q="${i}">移除</button></div>`).join("") : "";
    $("#f-queue").querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => { queue.splice(+b.dataset.q, 1); drawQueue(); }));
    const n = queue.length + 1;
    if (!editing) $("#f-save").textContent = queue.length ? `保存这 ${n} 条并更新网页` : "保存并更新网页";
  }

  let busy = false;
  async function commit(mutate, msg) {
    if (busy) return; busy = true;
    $("#ops-card").classList.add("busy");
    try {
      const f = await getFile(TRADES);
      const T = splitTrades(f.text);
      const desc = mutate(T.recs);
      const sha = await putFile(TRADES, joinTrades(T), f.sha, msg || "实盘：" + desc);
      say(`<b>已保存</b><div>${esc(desc)}</div><div>网页重建中…</div>`, "ok");
      raw = T.recs; drawRecent();
      waitRun({ q: "event=push", match: (r) => r.head_sha === sha }, "已保存，网页重建中");
      return true;
    } catch (e) {
      say(`<b>保存失败</b><div>${esc(e.message)}</div>`, "bad");
      return false;
    } finally { busy = false; $("#ops-card").classList.remove("busy"); }
  }

  async function onSave() {
    if (!token()) { openSetup(); $("#f-msg").textContent = "先填一次 GitHub 令牌（下面“网页操作设置”）"; return; }
    let cur = null;
    try { if ($("#f-px").value.trim() || !queue.length) cur = await readForm(); } catch (e) { $("#f-msg").textContent = e.message; return; }
    $("#f-msg").textContent = "";
    const add = [...queue, ...(cur ? [cur] : [])];
    if (editing) {
      const old = editing;
      const ok = await commit((recs) => {
        const i = recs.findIndex((r) => sameRec(r, old));
        if (i < 0) throw new Error("原记录已经变了，请刷新页面后再改");
        recs.splice(i, 1, ...add);
        return "修改 " + recText(add[add.length - 1]);
      });
      if (ok) { queue.length = 0; drawQueue(); resetForm(); }
      return;
    }
    const ok = await commit((recs) => {
      const dup = add.find((a) => recs.some((r) => sameRec(r, a)));
      if (dup && !confirm("已经有一条一模一样的记录：\n" + recText(dup) + "\n\n还要再记一次吗？")) throw new Error("已取消");
      recs.push(...add);
      return add.map(recText).join("；");
    });
    if (ok) { queue.length = 0; drawQueue(); resetForm(); }
  }

  async function onAdd() {
    try { queue.push(await readForm()); } catch (e) { $("#f-msg").textContent = e.message; return; }
    $("#f-msg").textContent = ""; resetForm(); drawQueue();
  }

  // ---------- 最近记录（可修改 / 删除）
  let raw = null;
  async function loadRaw() {
    if (!token()) { raw = null; drawRecent(); return; }
    try { raw = splitTrades((await getFile(TRADES)).text).recs; } catch (e) { raw = null; $("#ops-recent").innerHTML = `<div class="hint">读取记录失败：${esc(e.message)}</div>`; return; }
    drawRecent();
  }
  function drawRecent() {
    const el = $("#ops-recent"); if (!el) return;
    if (!raw) { el.innerHTML = ""; return; }
    const list = raw.map((r, i) => ({ r, i })).slice(-8).reverse();
    el.innerHTML = `<div class="hint" style="margin:12px 0 4px">最近记录（可改 / 删）：</div>` + list.map(({ r, i }) =>
      `<div class="qrow${/占位|待确认/.test(r.备注) ? " todo" : ""}"><span>${esc(recText(r))}</span>
       <span><button class="link" data-e="${i}">修改</button><button class="link danger" data-d="${i}">删除</button></span></div>`).join("");
    el.querySelectorAll("[data-e]").forEach((b) => (b.onclick = () => startEdit(raw[+b.dataset.e])));
    el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => {
      const old = raw[+b.dataset.d];
      if (!confirm("删除这条记录？\n" + recText(old))) return;
      commit((recs) => {
        const i = recs.findIndex((r) => sameRec(r, old));
        if (i < 0) throw new Error("记录已经变了，请刷新页面");
        recs.splice(i, 1); return "删除 " + recText(old);
      });
    }));
  }
  function startEdit(r) {
    editing = r;
    $("#f-date").value = r.日期; $("#f-op").value = r.操作; onOp(false);
    const v = picks().find((x) => x.v.slice(2) === r.代码);
    if (r.代码) {
      $("#f-pick").value = v ? v.v : "other";
      $("#ops-card").classList.toggle("is-other", !v);
      $("#f-code").value = r.代码; $("#f-name").value = r.名称;
    }
    $("#f-px").value = r.价格; $("#f-sh").value = r.股数; $("#f-fee").value = r.费用;
    $("#f-src").value = ["主信号", "补位", "其他"].includes(r.来源) ? r.来源 : "其他";
    $("#f-sig").value = r.信号日; $("#f-note").value = r.备注.replace(/；?(占位[^；]*|股数待确认[^；]*)/g, "").replace(/^；/, "");
    $("#f-save").textContent = "保存修改并更新网页"; $("#f-cancel").hidden = false;
    $("#ops-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- 设置（令牌、开始资金）
  function openSetup() {
    try { location.hash = "live"; } catch (e) {}
    const d = $("#ops-setup"); if (d) { d.open = true; setTimeout(() => d.scrollIntoView({ behavior: "smooth", block: "center" }), 50); }
  }
  function setupHTML() {
    const has = !!token();
    return `<details id="ops-setup"${has ? "" : " open"}><summary>网页操作设置${has ? "（已设置令牌）" : "（第一次用先设置）"}</summary>
      <ol class="steps">
        <li>打开 <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub → 新建 Fine-grained 令牌</a>（要先登录 GitHub）。</li>
        <li><b>Token name</b> 随便填（如 dashboard）；<b>Expiration</b> 选 1 年或 Custom。</li>
        <li><b>Repository access</b> 选 <b>Only select repositories</b> → 只选 <b>${esc(REPO.split("/")[1])}</b>。</li>
        <li><b>Permissions → Repository permissions</b>：<b>Contents</b> 选 Read and write，<b>Actions</b> 选 Read and write（其他不用动）。</li>
        <li>点 <b>Generate token</b>，复制 <code>github_pat_</code> 开头那串，粘到下面保存。</li>
      </ol>
      <div class="opsb">
        <input id="tok" type="password" placeholder="github_pat_…" autocomplete="off" style="flex:1;min-width:200px">
        <button class="primary" id="tok-save">保存令牌</button>
        ${has ? `<button class="sec" id="tok-del">清除</button>` : ""}
      </div>
      <p class="hint">令牌只保存在这台设备的浏览器里，不会上传到仓库；换手机/电脑要再填一次。网页是公开的，但别人没有令牌只能看，改不了。</p>
      ${has ? `<div class="opsb"><label style="flex:1">开始资金（元，开始记录实盘时账户总资产）<input id="cap" type="number" min="0" step="1" value="${D?.live?.capital ?? ""}"></label>
        <button class="sec" id="cap-save" style="align-self:flex-end">保存开始资金</button></div>` : ""}
    </details>`;
  }
  async function saveToken() {
    const t = $("#tok").value.trim();
    if (!t) return;
    setToken(t);
    try {
      const j = await gh("");
      if (!j.permissions?.push) throw new Error("这个令牌没有写入权限（Contents 要选 Read and write）");
      say("<b>令牌可用</b><div>以后在这台设备上可以直接记录操作、点更新。</div>", "ok", false);
      mount();
    } catch (e) { setToken(""); say(`<b>令牌不可用</b><div>${esc(e.message)}</div>`, "bad"); }
  }
  async function saveCap() {
    const v = $("#cap").value.trim();
    if (v && !(+v > 0)) { say("开始资金不对", "bad", false); return; }
    try {
      const f = await getFile(CONFIG); const c = JSON.parse(f.text);
      c.capital = v ? +v : null;
      const sha = await putFile(CONFIG, JSON.stringify(c, null, 2) + "\n", f.sha, `实盘：开始资金 ${v || "清空"}`);
      say("<b>开始资金已保存</b><div>网页重建中…</div>", "ok");
      waitRun({ q: "event=push", match: (r) => r.head_sha === sha }, "已保存，网页重建中");
    } catch (e) { say(`<b>保存失败</b><div>${esc(e.message)}</div>`, "bad"); }
  }

  // ---------- 挂到页面
  function mount() {
    const card = $("#ops-card"); if (!card) return;
    card.innerHTML = `<div class="card-h"><h2>记录今天的实盘操作</h2><button class="sec" id="ops-update" title="让 GitHub 现在就抓今天的收盘数据">↻ 更新行情</button></div>
      <p class="hint">填好点保存：自动写进成交记录并重建网页（1–3 分钟后刷新出新的持仓和明日操作）。选持仓/明日信号会自动带出来源、信号日、卖出股数和止盈价，改成实际成交价即可。</p>
      ${formHTML()}<div id="ops-recent"></div>${setupHTML()}`;
    $("#f-date").value = today();
    $("#f-op").onchange = () => onOp();
    $("#f-pick").onchange = fillFromPick;
    $("#f-code").onchange = async () => { const c = normCode($("#f-code").value); if (c) { $("#f-code").value = c; if (!$("#f-name").value) $("#f-name").value = await nameOf(c); } };
    $("#f-save").onclick = onSave;
    $("#f-add").onclick = onAdd;
    $("#f-cancel").onclick = () => { queue.length = 0; drawQueue(); resetForm(); };
    $("#tok-save").onclick = saveToken;
    if ($("#tok-del")) $("#tok-del").onclick = () => { if (confirm("清除这台设备上保存的令牌？")) { setToken(""); mount(); } };
    if ($("#cap-save")) $("#cap-save").onclick = saveCap;
    $("#ops-update").onclick = triggerUpdate;
    // 默认：有持仓 → 卖出；否则有信号 → 买入
    $("#f-op").value = D?.live?.holdings?.length ? "卖出" : "买入";
    onOp();
    drawQueue();
    loadRaw();
  }

  window.OPS = {
    init(d) { D = d; mount(); },
    update: triggerUpdate,
  };
  const hb = $("#update-btn"); if (hb) hb.onclick = triggerUpdate;
})();
