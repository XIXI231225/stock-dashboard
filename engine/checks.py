"""数据质量检查。每条问题 = dict(level, title, detail)。level: red=数据异常, yellow=提醒, info=记录。"""
from __future__ import annotations

import numpy as np

from .core import r2, is_bad_name


def alert(level, title, detail=""):
    return dict(level=level, title=title, detail=detail)


def validate_bars(bars: dict, state, date: str, prev_n: int):
    """bars: {code: bar}。返回 (clean_bars_list, alerts, stats)。不合格行剔除并报警。"""
    al, clean, dropped = [], [], []
    pre_mis = 0
    for code, b in bars.items():
        o, h, l, c, pc = b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("preclose")
        if None in (o, h, l, c, pc) or min(o, h, l, c, pc) <= 0:
            dropped.append((code, "字段缺失/非正")); continue
        if not (l - 1e-6 <= min(o, c) and max(o, c) <= h + 1e-6):
            dropped.append((code, f"高低开收矛盾 O{o} H{h} L{l} C{c}")); continue
        j = state.idx.get(code)
        nd = int(state.ndays[j]) if j is not None else 0
        lim = 0.05 if is_bad_name(b.get("name", "")) else 0.10
        if nd >= 5:   # 新股前5日不设涨跌幅
            lu, ld = r2(pc * (1 + lim)), r2(pc * (1 - lim))
            if c > lu + 0.011 or c < ld - 0.011:
                dropped.append((code, f"收盘{c}超出涨跌停[{ld},{lu}]（前收{pc}）")); continue
        if j is not None and state.lastc[j] == state.lastc[j] and abs(state.lastc[j] - pc) > 0.0051:
            pre_mis += 1   # 除权除息日正常会出现，少量不算异常
        clean.append(b)
    n = len(clean)
    stats = dict(rows=len(bars), clean=n, dropped=len(dropped), preclose_changed=pre_mis, prev_traded=prev_n)
    if dropped:
        lvl = "red" if len(dropped) > 5 else "yellow"
        al.append(alert(lvl, f"剔除 {len(dropped)} 条不合理行情", "；".join(f"{c}:{w}" for c, w in dropped[:12])))
    if prev_n and n < prev_n * 0.97:
        al.append(alert("red", "当日股票数偏少", f"{date} 有效 {n} 只，上一交易日 {prev_n} 只（<97%）"))
    if n and pre_mis > n * 0.15:
        al.append(alert("red", "前收盘与上一日收盘大面积不一致",
                        f"{pre_mis}/{n} 只前收盘≠我们记录的上日收盘，可能数据源日期错位"))
    elif pre_mis:
        al.append(alert("info", "前收盘变化（除权除息）", f"{pre_mis} 只"))
    return clean, al, stats


def cross_check(a: dict, b: dict, name_a: str, name_b: str):
    """两个数据源收盘价比对。"""
    common = set(a) & set(b)
    if len(common) < 500:
        return [alert("info", f"{name_a}/{name_b} 交叉核对样本不足", f"共同 {len(common)} 只")], dict(common=len(common))
    mis = [c for c in common if abs(a[c]["close"] - b[c]["close"]) > 0.0051]
    st = dict(common=len(common), mismatch=len(mis))
    if len(mis) > len(common) * 0.01:
        return [alert("red", f"{name_a}与{name_b}收盘价不一致",
                      f"{len(mis)}/{len(common)} 只不一致，如 " + "，".join(
                          f"{c}:{a[c]['close']}/{b[c]['close']}" for c in mis[:8]))], st
    return [], st


def market_alerts(rep):
    al = []
    if rep["ldn"] >= 50:
        al.append(alert("yellow", "跌停家数激增", f"主板非ST收盘跌停 {rep['ldn']} 家"))
    if rep["breadth"] <= 0.15:
        al.append(alert("yellow", "市场宽度极低", f"{rep['breadth']:.1%}（{rep['breadth_above']}/{rep['breadth_valid']}）"))
    if rep["breadth"] >= 0.85:
        al.append(alert("yellow", "市场宽度极高", f"{rep['breadth']:.1%}（{rep['breadth_above']}/{rep['breadth_valid']}）"))
    return al


def holding_alerts(state, D_date: str):
    """持仓股长期无行情（可能停牌/退市，回测会一直占着仓位）。"""
    al = []
    for acc in state.accounts.values():
        for code, P in acc.pos.items():
            j = state.idx[code]
            ld = state.last_date[j]
            if ld and ld < D_date:
                gap = sum(1 for d in state.dates if d > ld)
                if gap >= 5:
                    al.append(alert("red", f"{acc.name}持仓 {code} 已 {gap} 个交易日无行情", "可能停牌或退市，请人工确认"))
    return al
