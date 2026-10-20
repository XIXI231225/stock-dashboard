"""实盘账户：按 live/trades.csv 里的真实成交记账，用每日快照估值，并按 F版规则给出下一交易日该做什么。

不参与回测引擎；只读：live/config.json、live/trades.csv、数据目录 snapshots/、latest.json（信号）。

trades.csv 列：日期,代码,名称,操作,价格,股数,费用,来源,信号日,备注
  操作：买入 / 补仓 / 卖出 / 入金 / 出金（入金、出金只填 价格=金额，股数留空）
  费用：留空则按 F版费率估算（佣金0.0085%+过户费0.001%，卖出另加印花税0.05%）
  来源：主信号 / 补位 / 其他（不按策略的操作，照样记账，页面会标出来）
"""
from __future__ import annotations

import csv
import gzip
import json
import math
from pathlib import Path

from .core import COMM, TRANS, ceil2, r2, stamp, limit_pct, is_bad_name, MAX_POS, ADDX

SIZING = {"稳健版": (0.20, 0.04), "进取版": (0.30, 0.04)}
BUY_OPS = ("买入", "补仓")


def _f(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def norm_code(c: str) -> str:
    c = str(c).strip().lower()
    if c[:2] in ("sh", "sz"):
        return c
    return ("sh" if c[:1] == "6" else "sz") + c.zfill(6)


def load_trades(path: Path):
    rows, errs = [], []
    if not path.exists():
        return rows, errs
    with open(path, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            if not r.get("日期"):
                continue
            op = r.get("操作", "")
            px, sh = _f(r.get("价格")), _f(r.get("股数"))
            if op in ("入金", "出金"):
                if px is None:
                    errs.append(f"第{i}行 {op} 缺金额（填在 价格 列）"); continue
            elif op in BUY_OPS + ("卖出",):
                if px is None or not sh or sh <= 0:
                    errs.append(f"第{i}行 {op} 价格/股数不完整"); continue
                r["代码"] = norm_code(r.get("代码", ""))
            else:
                errs.append(f"第{i}行 操作“{op}”无法识别（应为 买入/补仓/卖出/入金/出金）"); continue
            rows.append(dict(i=i, date=r["日期"], code=r.get("代码", ""), name=r.get("名称", ""), op=op, px=px, sh=sh,
                             fee=_f(r.get("费用")), src=r.get("来源", "") or "其他", sig=r.get("信号日", ""),
                             note=r.get("备注", "")))
    rows.sort(key=lambda x: (x["date"], x["i"]))
    return rows, errs


def est_fee(op, date, amt):
    return amt * (COMM + TRANS + (stamp(date) if op == "卖出" else 0))


class Bars:
    """按需读快照（只保留关心的代码）。"""
    def __init__(self, data: Path, since: str, codes: set):
        self.dates, self.bar = [], {}
        for p in sorted((data / "snapshots").glob("*.csv.gz")):
            d = p.name[:10]
            if d < since:
                continue
            self.dates.append(d)
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["code"] in codes:
                        self.bar[(d, r["code"])] = {k: (_f(v) if k not in ("code", "name", "src") else v) for k, v in r.items()}

    def get(self, d, code):
        return self.bar.get((d, code))

    def last_close(self, d, code):
        """d 当天或之前最近的收盘。"""
        for x in reversed([x for x in self.dates if x <= d]):
            b = self.get(x, code)
            if b and b.get("close"):
                return b["close"], x
        return None, None


def compute(root: Path, data: Path, latest: dict):
    cfg_p = root / "live/config.json"
    if not cfg_p.exists():
        return None
    cfg = json.load(open(cfg_p, encoding="utf-8"))
    rows, errs = load_trades(root / "live/trades.csv")
    start = cfg.get("start_date") or (rows[0]["date"] if rows else latest["date"])
    capital = _f(cfg.get("capital"))
    sizing = cfg.get("sizing", "稳健版")
    main_pct, fill_pct = SIZING.get(sizing, SIZING["稳健版"])
    max_fires = int(cfg.get("max_fires", 1) or 1)
    asof = latest["date"]

    codes = {r["code"] for r in rows if r["code"]} | {s["code"] for s in latest.get("signals", [])}
    B = Bars(data, "", codes)   # 全部快照都读（只取相关代码），开始日前一天的收盘用于算首笔比例
    names = {}
    for (d, c), b in B.bar.items():
        names[c] = b.get("name") or names.get(c, "")

    # ---------------- 逐笔记账
    cash = capital if capital is not None else 0.0
    pos, closed, flows, warns = {}, [], [], list(errs)
    applied = []          # 实际记账的行（被忽略的错误行不进资金曲线）

    def equity_before(d):
        """d 之前最近一个交易日收盘的总资产（用于按比例算首笔金额）。"""
        prev = [x for x in B.dates if x < d]
        if not prev or capital is None:
            return None
        pd_ = prev[-1]
        c0 = (capital or 0.0)
        mv = 0.0; shares = {}
        for r in applied:
            if r["date"] > pd_:
                break
            if r["op"] == "入金":
                c0 += r["px"]
            elif r["op"] == "出金":
                c0 -= r["px"]
            else:
                amt = r["px"] * r["sh"]; fee = r["fee"] if r["fee"] is not None else est_fee(r["op"], r["date"], amt)
                if r["op"] in BUY_OPS:
                    c0 -= amt + fee; shares[r["code"]] = shares.get(r["code"], 0) + r["sh"]
                else:
                    c0 += amt - fee; shares[r["code"]] = shares.get(r["code"], 0) - r["sh"]
        for c, s in shares.items():
            if s > 0:
                px, _ = B.last_close(pd_, c)
                mv += s * (px or 0)
        return c0 + mv

    for r in rows:
        if r["op"] in ("入金", "出金"):
            cash += r["px"] if r["op"] == "入金" else -r["px"]
            applied.append(r)
            flows.append(dict(date=r["date"], op=r["op"], code="", name="", px=None, shares=None, amt=r["px"] if r["op"] == "入金" else -r["px"], fee=0, src="", note=r["note"]))
            continue
        c = r["code"]; amt = r["px"] * r["sh"]
        fee = r["fee"] if r["fee"] is not None else est_fee(r["op"], r["date"], amt)
        nm = r["name"] or names.get(c, "")
        names[c] = nm
        if r["op"] in BUY_OPS:
            P = pos.get(c)
            if P is None:
                if r["op"] == "补仓":
                    warns.append(f"第{r['i']}行 {nm} 记为补仓，但之前没有持仓，按首笔买入处理")
                eqb = equity_before(r["date"])
                pct = main_pct if r["src"] == "主信号" else fill_pct if r["src"] == "补位" else None
                lot = (pct * eqb) if (pct and eqb) else amt     # 首档金额：按规则比例 × 前一日总资产；算不出就用实际首笔金额
                P = pos[c] = dict(code=c, name=nm, src=r["src"], sig=r["sig"], entry=r["date"], entry_px=r["px"], shares=0.0,
                                  cost=0.0, inv=0.0, out=0.0, lots=0, last_buy=r["date"], lot=lot, lot_basis="规则比例" if (pct and eqb) else "实际首笔",
                                  flows=[])
            P["shares"] += r["sh"]; P["cost"] += amt; P["inv"] += amt + fee; P["lots"] += 1; P["last_buy"] = r["date"]
            cash -= amt + fee
            applied.append(r)
        else:
            P = pos.get(c)
            if P is None or r["sh"] > P["shares"] + 1e-6:
                warns.append(f"第{r['i']}行 卖出 {nm} {int(r['sh'])} 股，超过当前持仓，已忽略"); continue
            avg = P["cost"] / P["shares"]
            P["cost"] -= avg * r["sh"]; P["shares"] -= r["sh"]; P["out"] += amt - fee
            cash += amt - fee
            applied.append(r)
            if P["shares"] <= 1e-6:
                closed.append(dict(code=c, name=nm, src=P["src"], sig=P["sig"], entry=P["entry"], entry_px=P["entry_px"],
                                   exit=r["date"], exit_px=r["px"], lots=P["lots"], invested=round(P["inv"], 2),
                                   pnl=round(P["out"] - P["inv"], 2), ret=(P["out"] / P["inv"] - 1) if P["inv"] else None,
                                   note=r["note"]))
                del pos[c]
        P_ = pos.get(c)
        flows.append(dict(date=r["date"], op=r["op"], code=c, name=nm, px=r["px"], shares=int(r["sh"]),
                          amt=round(-(amt + fee) if r["op"] in BUY_OPS else amt - fee, 2), fee=round(fee, 2),
                          src=r["src"], note=r["note"], est_fee=r["fee"] is None))
        if P_ is not None:
            P_["flows"].append(flows[-1])

    # ---------------- 资金曲线（每个快照日）
    eq_hist = []
    if capital is not None:
        for d in B.dates:
            if d < start:
                continue
            c0 = capital; sh = {}
            for r in applied:
                if r["date"] > d:
                    break
                if r["op"] == "入金":
                    c0 += r["px"]; continue
                if r["op"] == "出金":
                    c0 -= r["px"]; continue
                amt = r["px"] * r["sh"]; fee = r["fee"] if r["fee"] is not None else est_fee(r["op"], r["date"], amt)
                if r["op"] in BUY_OPS:
                    c0 -= amt + fee; sh[r["code"]] = sh.get(r["code"], 0) + r["sh"]
                else:
                    c0 += amt - fee; sh[r["code"]] = sh.get(r["code"], 0) - r["sh"]
            mv = sum(s * (B.last_close(d, c)[0] or 0) for c, s in sh.items() if s > 0)
            eq_hist.append([d, round(c0 + mv, 2), round(mv, 2), sum(1 for s in sh.values() if s > 0)])

    # ---------------- 持仓与下一交易日计划
    hold, plan = [], []
    for c, P in pos.items():
        close, cd = B.last_close(asof, c)
        b = B.get(asof, c)
        nm = P["name"] or names.get(c, "")
        avg = P["cost"] / P["shares"]
        # 时间止损计数：最后一次买入之后，该股实际有成交的交易日数（截至 asof）
        cnt = sum(1 for d in B.dates if P["last_buy"] < d <= asof and B.get(d, c))
        tp = ceil2(avg * 1.05)
        bad = is_bad_name(nm)
        nlu = r2(close * (1 + limit_pct(bad, asof))) if close else None
        nld = r2(close * (1 - limit_pct(bad, asof))) if close else None
        today_ld = b and b.get("preclose") and b["close"] <= r2(b["preclose"] * (1 - limit_pct(bad, asof))) + 1e-6
        nxt_add = None
        if P["lots"] < 3:
            tgt = ADDX[P["lots"] - 1] * P["lot"]
            trig = avg * 0.97
            add_sh = max(0, round((tgt - P["shares"] * trig) / trig / 100) * 100)
            nxt_add = dict(trigger=round(trig, 3), target_amt=round(tgt, 0), approx_shares=int(add_sh))
        mv = P["shares"] * (close or 0)
        h = dict(code=c, name=nm, src=P["src"], sig=P["sig"], entry=P["entry"], last_buy=P["last_buy"], shares=int(P["shares"]),
                 avg=round(avg, 3), close=close, close_date=cd, mv=round(mv, 0), pnl=round(mv + P["out"] - P["inv"], 0),
                 ret=(close / avg - 1) if close else None, tp=tp, lots=P["lots"], cnt=cnt, time_stop_in=max(0, 3 - cnt),
                 next_lu=nlu, next_ld=nld, tp_reachable=(nlu is not None and nlu >= tp + 0.01), next_add=nxt_add,
                 lot=round(P["lot"], 0), lot_basis=P["lot_basis"], flows=P["flows"])
        hold.append(h)

        steps = []
        if cnt >= 3:
            steps.append(("卖出", f"已满3个交易日未止盈（时间止损）：{'今天收盘封跌停卖不出 → ' if today_ld else ''}"
                                  f"开盘价高于跌停价 {nld} 就按开盘卖；开盘跌停但盘中打开按跌停价卖"))
        else:
            steps.append(("止盈", f"开盘前挂限价卖 {tp:.2f}（均价{avg:.3f}×1.05）" +
                          ("" if h["tp_reachable"] else f"；明天涨停 {nlu}，到不了")))
            if cnt == 2:
                steps.append(("时间止损", "明天收盘仍未止盈 → 收盘卖出（收盘封跌停则顺延）"))
            else:
                steps.append(("时间止损", f"还剩 {3 - cnt} 个交易日；不止盈也不补仓的话第 {3 - cnt} 天收盘卖"))
                if nxt_add:
                    steps.append(("补仓", f"收盘 ≤ {nxt_add['trigger']:.2f} 且不是跌停收盘 → 按收盘价补到约 {nxt_add['target_amt']:,.0f} 元"
                                         f"（在触发价约补 {nxt_add['approx_shares']} 股，第{P['lots'] + 1}笔）"))
            if cnt < 2 and not nxt_add:
                steps.append(("补仓", "已满3笔，不再补"))
        plan.append(dict(code=c, name=nm, steps=[dict(kind=k, text=t) for k, t in steps]))

    mv_all = sum(h["mv"] for h in hold)
    eq = cash + mv_all if capital is not None else None

    sig_plans = []
    n_hold = len(pos)
    for s in latest.get("signals", []):
        main = s["kind"] == "主信号"
        if main and (s.get("nth") or 1) > max_fires:
            txt, act = "本次进池已买过1次主信号，按F版原规则不买", False
        elif s["code"] in pos:
            txt, act = "已持有，不重复开仓", False
        elif main and n_hold >= MAX_POS:
            txt, act = f"已持有 {n_hold} 只（上限4只），不买", False
        elif (not main) and n_hold > 0:
            txt, act = (f"持有 {'、'.join(h['name'] for h in hold)}，按规则不买；只有开盘时持仓全部止盈卖出、账户空仓才买", False)
        else:
            pct = main_pct if main else fill_pct
            if eq:
                amt = pct * eq
                shs = max(100, round(amt / s["close"] / 100) * 100) if s.get("close") else None
                txt = f"开盘集合竞价买：总资产×{pct:.0%} ≈ {amt:,.0f} 元，按今收估约 {shs} 股（按开盘价重新取整百）"
            else:
                txt = f"开盘集合竞价买：总资产×{pct:.0%}（未设置总资产，无法算金额）"
            act = True
        sig_plans.append(dict(kind=s["kind"], nth=s.get("nth"), code=s["code"], name=s["name"], close=s.get("close"), pct=s.get("pct"),
                              next_lu=s.get("next_lu"), next_ld=s.get("next_ld"), buy=act, text=txt))

    if capital is None:
        warns.append("未设置开始时的账户总资产（live/config.json 的 capital），总资产、仓位比例、补仓目标金额暂按实际首笔金额估算")
    for r in rows:
        if "占位" in r["note"] or "待确认" in r["note"]:
            warns.append(f"{r['date']} {r['op']} {r['name']} {r['px']}×{int(r['sh'] or 0)}：{r['note']}")
    off = [f"{r['date']} {r['op']} {r['name'] or r['code']}" for r in rows if r["op"] in BUY_OPS + ("卖出",) and r["src"] not in ("主信号", "补位")]
    if off:
        warns.append("非策略信号的操作：" + "；".join(off))

    pnl_closed = sum(t["pnl"] for t in closed)
    return dict(name=cfg.get("name", "实盘"), asof=asof, start=start, capital=capital, sizing=sizing,
                main_pct=main_pct, fill_pct=fill_pct, max_fires=max_fires,
                cash=round(cash, 2) if capital is not None else None, mv=round(mv_all, 0), eq=round(eq, 2) if eq is not None else None,
                total_ret=(eq / capital - 1) if (eq is not None and capital) else None,
                day_pnl=(eq_hist[-1][1] - eq_hist[-2][1]) if len(eq_hist) >= 2 else None,
                pnl_closed=round(pnl_closed, 0), n_closed=len(closed),
                win=(sum(t["pnl"] > 0 for t in closed) / len(closed)) if closed else None,
                holdings=hold, plan=plan, signals=sig_plans, closed=closed[::-1], flows=flows[::-1], eq_hist=eq_hist,
                warnings=warns)


def alerts(L: dict):
    """给邮件 / Issue 用的实盘提醒。"""
    from .checks import alert
    if not L:
        return []
    out = []
    lines = []
    for p in L["plan"]:
        lines.append(f"{p['name']}：" + "；".join(f"[{s['kind']}] {s['text']}" for s in p["steps"]))
    for s in L["signals"]:
        lines.append(f"{s['kind']} {s['name']}：{s['text']}")
    if lines:
        out.append(alert("yellow", f"实盘·下一交易日操作（{L['name']}）", "；".join(lines)))
    return out
