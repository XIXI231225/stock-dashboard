"""把引擎状态整理成网页用的 JSON。"""
from __future__ import annotations

import math

import numpy as np

from .core import ceil2, r2, CASH0, WIN, MCAP, limit_pct

REASON = {"TP": "盘中止盈5%", "TP_open": "开盘止盈", "TIME": "时间止损(3日)", "DEFER": "封跌停顺延卖出",
          "DEFER_open": "封跌停顺延(次日开盘卖)", "DELIST": "停牌/退市强平", "END": "期末未平仓"}


def _n(x, k=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(x) else round(x, k)


def make_latest(S, rep, alerts, quality, generated_at):
    L = S._last; D = L["D"]; r20 = L["r20"]; rk30 = L["rk30"]
    idx = S.idx; names = S.names
    pct = np.where(D.tr, D.c / D.pc - 1, np.nan)
    n_up = int(np.nansum(pct > 0)); n_dn = int(np.nansum(pct < 0))
    n_lu = int(((D.c >= D.lu - 1e-6) & D.tr & ~D.bad).sum())
    pool_codes = {a["code"] for a in S.arrivals if len(S.dates) - 1 - a["a"] < WIN}

    top = []
    for code, rk in rk30.items():
        j = idx[code]
        ma3 = float(np.mean(S.raw[j])) if np.isfinite(S.raw[j]).all() else None
        top.append(dict(rank=rk, code=code, name=names.get(code, ""), r20=_n(r20[j]), close=_n(D.c[j], 3),
                        pct=_n(pct[j]), ma3=_n(ma3, 3), fmv=_n(D.fmv[j] / 1e8, 1), in_pool=code in pool_codes))

    n_today = len(S.dates) - 1
    pool = []
    for a in sorted(S.arrivals, key=lambda a: -a["a"]):
        left = WIN - (n_today - a["a"])
        if left <= 0:
            continue
        j = idx[a["code"]]
        c = D.c[j] if D.tr[j] else S.lastc[j]
        fmv = D.fmv[j] if D.tr[j] else np.nan
        if a["fired"]:
            status = f"已触发（{a['fired_date']}）"
        elif np.isfinite(fmv) and fmv < MCAP:
            status = "市值<80亿，暂不满足"
        else:
            status = "有效，继续观察"
        bad = D.bad[j]
        pool.append(dict(code=a["code"], name=names.get(a["code"], ""), first_date=a["date"], rank_at=a["rank"],
                         r20_at=_n(a["r20"]), rank_now=rk30.get(a["code"]), r20_now=_n(r20[j]), close=_n(c, 3),
                         pct=_n(pct[j]), fmv=_n(fmv / 1e8, 1), days_left=left, fired=a["fired"], status=status,
                         trig_hi=math.floor(c * 0.95 * 100 + 1e-7) / 100, trig_lo=r2(c * (1 - limit_pct(bool(bad), D.date))),
                         traded=bool(D.tr[j])))

    sig = []
    for kind, prio, code, sd, src in sorted(S.pending):
        j = idx[code]; c = float(D.c[j])
        sig.append(dict(kind=src, code=code, name=names.get(code, ""), sig_date=sd, close=_n(c, 3), pct=_n(pct[j]),
                        fmv=_n(D.fmv[j] / 1e8, 1), prev_rank=(prio if kind == 1 else None),
                        r20_at=(_n(-prio) if kind == 0 else None),
                        next_lu=r2(c * (1 + limit_pct(bool(D.bad[j]), D.date))), next_ld=r2(c * (1 - limit_pct(bool(D.bad[j]), D.date)))))

    accts = {}
    for name, A in S.accounts.items():
        eq = np.array([x[1] for x in A.eq_hist])
        peak = np.maximum.accumulate(eq)
        years = (np.datetime64(A.eq_hist[-1][0]) - np.datetime64(A.eq_hist[0][0])).astype(int) / 365.25
        hold = []
        for code, P in A.pos.items():
            j = idx[code]; avg = P.cost / P.shares; c = P.lastc
            tp = ceil2(avg * 1.05)
            nlu = r2(c * (1 + limit_pct(bool(D.bad[j]), D.date)))
            nxt_add = None
            if P.lots < 3:
                nxt_add = dict(trigger=round(avg * 0.97, 3), target_amt=round((2.0, 2.5)[P.lots - 1] * P.lot, 0))
            hold.append(dict(code=code, name=names.get(code, ""), src=P.src, sig=P.sig, entry=P.e, shares=round(P.shares),
                             avg=_n(avg, 3), close=_n(c, 3), mv=_n(P.shares * c, 0), pnl=_n(P.shares * c + P.out - P.inv, 0),
                             ret=_n((P.shares * c) / (P.cost) - 1), tp=tp, lots=P.lots, cnt=P.cnt,
                             time_stop_in=max(0, 3 - P.cnt), pend=P.pend, next_lu=nlu, tp_reachable=nlu >= tp + 0.01,
                             next_add=nxt_add))
        today_ev = []
        recs = [(t["code"], t["src"], t["flows"], t.get("reason")) for t in A.done] + \
               [(P.code, P.src, P.flows, None) for P in A.pos.values()]
        for code, src, flows, reason in recs:
            for f in flows:
                if f[0] == D.date:
                    today_ev.append(dict(action=f[1], code=code, name=names.get(code, ""), src=src, px=_n(f[2], 3),
                                         shares=round(f[3]), amt=_n(f[4], 0),
                                         reason=REASON.get(reason, reason) if f[1] == "卖出" else None))
        accts[name] = dict(eq=_n(eq[-1], 2), cash=_n(A.cash, 2), mv=_n(eq[-1] - A.cash, 2), npos=len(A.pos),
                           total_ret=_n(eq[-1] / CASH0 - 1), cagr=_n((eq[-1] / CASH0) ** (1 / years) - 1 if years > 0 else None),
                           mdd=_n((eq / peak - 1).min()), dd_now=_n(eq[-1] / peak[-1] - 1),
                           day_pnl=_n(eq[-1] - (eq[-2] if len(eq) > 1 else CASH0), 2),
                           main_pct=A.main_pct, fill_pct=A.fill_pct, holdings=hold, today=today_ev)

    return dict(date=D.date, generated_at=generated_at,
                market=dict(n_traded=rep["n_traded"], ldn=rep["ldn"], breadth=_n(rep["breadth"]),
                            breadth_above=rep["breadth_above"], breadth_valid=rep["breadth_valid"], eff=_n(rep["eff"]),
                            eff_ma3=_n(rep["eff_ma3"]), up=n_up, down=n_dn, limit_up=n_lu,
                            main_ok=rep["ldn"] > 3),
                top30=top, pool=pool, signals=sig, accounts=accts, alerts=alerts, quality=quality,
                new_arrivals=rep["new_arrivals"])


def make_accounts(S):
    out = {}
    for name, A in S.accounts.items():
        trades = []
        for t in sorted(A.done, key=lambda x: x["entry"]):
            trades.append(dict(code=t["code"], name=S.names.get(t["code"], ""), src=t["src"], sig=t["sig"], entry=t["entry"],
                               entry_px=_n(t["entry_px"], 3), exit=t["exit"], exit_px=_n(t["exit_px"], 3),
                               reason=REASON.get(t["reason"], t["reason"]), lots=t["lots"], hold=t["hold"],
                               invested=_n(t["invested"], 0), pnl=_n(t["pnl"], 0), ret=_n(t["ret"])))
        eq = [[d, e, m, n] for d, e, m, n in A.eq_hist]
        # 年度
        yr = {}
        for d, e, m, n in A.eq_hist:
            yr[d[:4]] = e
        ys, prev, yearly = sorted(yr), CASH0, []
        for y in ys:
            p = [t for t in trades if t["entry"][:4] == y]
            path = [x[1] for x in A.eq_hist if x[0][:4] == y]
            pk = np.maximum.accumulate(np.array([prev] + path))
            yearly.append(dict(year=y, start=_n(prev, 0), end=_n(yr[y], 0), ret=_n(yr[y] / prev - 1),
                               mdd=_n((np.array([prev] + path) / pk - 1).min()), n=len(p),
                               win=_n(np.mean([t["pnl"] > 0 for t in p]) if p else None),
                               pnl_main=_n(sum(t["pnl"] for t in p if t["src"] == "主信号"), 0),
                               pnl_fill=_n(sum(t["pnl"] for t in p if t["src"] == "补位"), 0)))
            prev = yr[y]
        pn = np.array([t["pnl"] for t in trades]) if trades else np.zeros(0)
        stats = dict(n=len(trades), win=_n((pn > 0).mean() if len(pn) else None),
                     pf=_n(pn[pn > 0].sum() / -pn[pn < 0].sum() if (pn < 0).any() else None, 3),
                     n_main=sum(t["src"] == "主信号" for t in trades), n_fill=sum(t["src"] == "补位" for t in trades),
                     pnl_main=_n(sum(t["pnl"] for t in trades if t["src"] == "主信号"), 0),
                     pnl_fill=_n(sum(t["pnl"] for t in trades if t["src"] == "补位"), 0),
                     worst=_n(pn.min() if len(pn) else None, 0), best=_n(pn.max() if len(pn) else None, 0),
                     empty_ratio=_n(np.mean([x[3] == 0 for x in A.eq_hist])))
        out[name] = dict(eq=eq, yearly=yearly, trades=trades, stats=stats)
    return out
