"""F版 研究用快速回测：一次性把行情做成矩阵，信号生成和账户模拟都可以调参数。
默认参数必须复现 engine/core.py 的结果（稳健版 166,241 / 进取版 216,578）。
"""
from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

MAIN_PREFIX = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
ST10 = "2026-07-06"
COMM, TRANS = 0.000085, 0.00001
FEE_B = 1 + COMM + TRANS


def r2a(x):
    return np.floor(x * 100 + 0.5 + 1e-7) / 100


def r2(x):
    return math.floor(x * 100 + 0.5 + 1e-7) / 100


def ceil2(x):
    return math.ceil(x * 100 - 1e-7) / 100


# ------------------------------------------------------------------ 数据矩阵
def build(all_pkl, out):
    df = pd.read_pickle(all_pkl)
    df = df[df.code.str[:5].isin(MAIN_PREFIX)].sort_values(["date", "code"])
    dates = np.array(sorted(df.date.dt.strftime("%Y-%m-%d").unique()))
    codes = np.array(sorted(df.code.unique()))
    T, N = len(dates), len(codes)
    di = {d: i for i, d in enumerate(dates)}; ci = {c: i for i, c in enumerate(codes)}
    t = df.date.dt.strftime("%Y-%m-%d").map(di).values; j = df.code.map(ci).values

    def M(col, fill=np.nan, dtype=float):
        a = np.full((T, N), fill, dtype=dtype); a[t, j] = df[col].values; return a
    TR = np.zeros((T, N), bool); TR[t, j] = True
    O, H, L, C, PC = M("open"), M("high"), M("low"), M("close"), M("preclose")
    FMV0 = M("fmv")
    BAD = np.ones((T, N), bool)
    BAD[t, j] = df.name.str.contains("ST|退", regex=True, na=True).values
    NAME = df.sort_values("date").drop_duplicates("code", keep="last").set_index("code").name.reindex(codes).values
    # 复权：每只股票自身交易日 cumprod(C/PC)，再按市场日前向填充
    ret = np.where(TR, C / PC, np.nan)
    AC = np.full((T, N), np.nan); ACM = np.full((T, N), np.nan)
    for jj in range(N):
        idx = np.where(TR[:, jj])[0]
        if len(idx) == 0:
            continue
        ac = np.cumprod(ret[idx, jj])
        AC[idx, jj] = ac
        s = pd.Series(AC[:, jj]).ffill().values
        ACM[:, jj] = s
    ACM[np.isnan(ACM)] = np.nan
    # 流通市值缺失 → 最近股本×收盘
    FMV = FMV0.copy()
    for jj in range(N):
        idx = np.where(TR[:, jj])[0]
        fsh = np.nan
        for tt in idx:
            v = FMV0[tt, jj]
            if np.isfinite(v) and v > 0:
                fsh = v / C[tt, jj]
            elif np.isfinite(fsh):
                FMV[tt, jj] = fsh * C[tt, jj]
    ndays = np.cumsum(TR, axis=0)
    ndays = ndays + np.where(TR[0], 10000, 0)[None, :]      # 数据首日已存在 → 老股
    lim = np.where(BAD & (dates[:, None] < ST10), 0.05, 0.10)
    LU = np.where(TR, r2a(PC * (1 + lim)), np.nan); LD = np.where(TR, r2a(PC * (1 - lim)), np.nan)
    last_t = np.where(TR, np.arange(T)[:, None], -1).max(0)
    first_t = np.where(TR, np.arange(T)[:, None], T).min(0)
    R20 = np.full((T, N), np.nan)
    R20[20:] = np.where(TR[20:] & np.isfinite(ACM[:-20]), ACM[20:] / ACM[:-20] - 1, np.nan)
    # 自身20日均线（市场宽度）
    MA20 = np.full((T, N), np.nan)
    for jj in range(N):
        idx = np.where(TR[:, jj])[0]
        if len(idx) >= 20:
            MA20[idx, jj] = pd.Series(AC[idx, jj]).rolling(20).mean().values
    g = dict(dates=dates, codes=codes, names=NAME, TR=TR, O=O, H=H, L=L, C=C, PC=PC, FMV=FMV, BAD=BAD, LU=LU, LD=LD,
             AC=AC, ACM=ACM, R20=R20, MA20=MA20, ndays=ndays, last_t=last_t, first_t=first_t)
    pickle.dump(g, open(out, "wb"), protocol=4)
    return g


# ------------------------------------------------------------------ 参数
@dataclass(frozen=True)
class P:
    list_min: int = 31        # 上市天数（含当天）
    top_k: int = 2            # 进池：排名前K
    look: int = 20            # 之前多少日没进过前K
    win: int = 10             # 观察期
    drop: float = 0.05        # 跌幅下限（收盘≤前收×(1-drop)）
    mcap: float = 8e9         # 流通市值门槛
    ldn_min: int = 4          # 主信号：跌停家数 ≥ ldn_min（即 >3）
    max_fires: int = 1        # 每次进池最多几次主信号
    fill_rank: int = 20       # 补位：前一日排名 ≤
    fill_mcap: float = 8e9
    use_fill: bool = True
    tp: float = 0.05          # 止盈
    add_trig: float = 0.03    # 补仓触发（收盘 ≤ 均价×(1-add_trig)）
    addx: tuple = (2.0, 2.5)  # 补仓目标倍数
    tstop: int = 3            # 时间止损天数
    main_pct: float = 0.20
    fill_pct: float = 0.04
    max_pos: int = 4
    slip: float = 0.0         # 单边滑点（买入价×(1+slip)，卖出价×(1-slip)）
    min_comm: float = 0.0     # 最低佣金（元/笔）
    start: str = "2018-01-02"
    end: str = "2099-12-31"


# ------------------------------------------------------------------ 每日排名（与参数 list_min 有关，缓存）
_rank_cache = {}


def ranks(g, list_min=31, top_n=30):
    key = (id(g), list_min, top_n)
    if key in _rank_cache:
        return _rank_cache[key]
    TR, R20, BAD, nd = g["TR"], g["R20"], g["BAD"], g["ndays"]
    T, N = TR.shape
    elig = TR & (nd >= list_min) & np.isfinite(R20) & ~BAD
    R = np.where(elig, R20, -np.inf)
    RK = np.full((T, N), 999, np.int16)
    for t in range(T):
        k = min(top_n, int(elig[t].sum()))
        if k:
            o = np.argsort(-R[t])[:k]
            RK[t, o] = np.arange(1, k + 1)
    _rank_cache[key] = RK
    return RK


def market(g):
    if "LDN" not in g:
        TR, C, LD, BAD, H = g["TR"], g["C"], g["LD"], g["BAD"], g["H"]
        g["LDN"] = ((C <= LD + 1e-6) & TR & ~BAD).sum(1)
        g["YZD"] = TR & (np.abs(H - LD) < 0.005)
    return g["LDN"], g["YZD"]


# ------------------------------------------------------------------ 信号
def signals(g, p: P):
    """返回 [(t, j, kind, prio, nth)]：t=信号日，kind 0=主信号 1=补位"""
    TR, C, PC, LD, BAD, FMV = g["TR"], g["C"], g["PC"], g["LD"], g["BAD"], g["FMV"]
    LDN, YZD = market(g)
    RK = ranks(g, p.list_min)
    T, N = TR.shape
    drop_ok = TR & ~BAD & ~YZD & (C <= PC * (1 - p.drop) + 1e-9) & (C >= LD - 1e-6)
    inK = RK <= p.top_k
    # 首次进前K：之前 look 日都不在前K
    cs = np.cumsum(inK, axis=0)
    out = []
    covered = np.zeros((T, N), bool)
    ts, js = np.where(inK)
    arr = []
    for t, j in zip(ts, js):
        lo = t - p.look
        prev = (cs[t - 1, j] - (cs[lo - 1, j] if lo > 0 else 0)) if t > 0 else 0
        if prev == 0:
            arr.append((t, j))
    for a, j in arr:
        nf = 0
        for t in range(a + 1, min(a + p.win, T - 1) + 1):
            covered[t, j] = True
            if nf < p.max_fires and drop_ok[t, j] and FMV[t, j] >= p.mcap and LDN[t] >= p.ldn_min:
                nf += 1
                out.append((t, j, 0, -g["R20"][a, j], nf))
    if p.use_fill:
        for t in range(1, T):
            for j in np.where(RK[t - 1] <= p.fill_rank)[0]:
                if not covered[t, j] and drop_ok[t, j] and FMV[t, j] >= p.fill_mcap:
                    out.append((t, j, 1, int(RK[t - 1, j]), 0))
    return out, arr


# ------------------------------------------------------------------ 持仓与账户（与 engine/core.py 逐行对应，参数化）
def stamp(d):
    return 0.001 if d < "2023-08-28" else 0.0005


class Pos:
    __slots__ = ("j", "e", "shares", "cost", "inv", "out", "lots", "cnt", "pend", "lastc", "lot", "kind", "sig",
                 "exit", "reason", "exit_px", "p", "nbuy", "nsell")

    def __init__(s, g, p, j, e, px, lot, kind, sig):
        s.p = p; s.j = j; s.e = e; s.kind = kind; s.sig = sig; s.lot = lot
        bpx = px * (1 + p.slip)
        sh = max(100, round(lot / px / 100) * 100)
        s.shares = float(sh); s.cost = sh * px; s.inv = sh * bpx * FEE_B + _minc(p, sh * bpx)
        s.out = 0.0; s.lots = 1; s.cnt = 0; s.pend = False; s.lastc = g["C"][e, j]
        s.exit = None; s.reason = None; s.exit_px = None

    def sell(s, g, t, px, why):
        p = s.p; spx = px * (1 - p.slip)
        amt = s.shares * spx * (1 - COMM - TRANS - stamp(g["dates"][t])) - _minc(p, s.shares * spx)
        s.out += amt; s.exit = t; s.reason = why; s.exit_px = px; s.shares = 0.0
        return amt

    def open_phase(s, g, t):
        j = s.j
        if not g["TR"][t, j]:
            return 0.0
        f = s.lastc / g["PC"][t, j]
        if abs(f - 1) > 1e-4:
            s.shares *= f
        s.lastc = g["C"][t, j]; s.cnt += 1
        O, LD = g["O"][t, j], g["LD"][t, j]
        if s.pend:
            return s.sell(g, t, O, "DEFER_open") if O > LD + 1e-6 else 0.0
        tpp = ceil2(s.cost / s.shares * (1 + s.p.tp))
        if O >= tpp - 1e-9:
            return s.sell(g, t, O, "TP_open")
        return 0.0

    def rest_phase(s, g, t, cash):
        j = s.j; p = s.p
        if not g["TR"][t, j]:
            if t > g["last_t"][j]:
                return s.sell(g, t, s.lastc, "DELIST")
            return 0.0
        H, C, LD = g["H"][t, j], g["C"][t, j], g["LD"][t, j]
        if s.pend:
            return s.sell(g, t, LD, "DEFER") if H > LD + 1e-6 else 0.0
        avg = s.cost / s.shares; tpp = ceil2(avg * (1 + p.tp))
        if H >= tpp + 0.01 - 1e-9:
            return s.sell(g, t, tpp, "TP")
        if s.cnt >= p.tstop:
            if C <= LD + 1e-6:
                s.pend = True; return 0.0
            return s.sell(g, t, C, "TIME")
        if s.lots < 1 + len(p.addx) and C <= avg * (1 - p.add_trig) and C > LD + 1e-6:
            tgt = p.addx[s.lots - 1] * s.lot
            add = round((tgt - s.shares * C) / C / 100) * 100
            if add > 0:
                bpx = C * (1 + p.slip)
                amt = add * bpx * FEE_B + _minc(p, add * bpx)
                if amt > cash:
                    add = math.floor(cash / (bpx * FEE_B) / 100) * 100; amt = add * bpx * FEE_B + _minc(p, add * bpx)
                    if add < 100:
                        return 0.0
                s.cost += add * C; s.shares += add; s.inv += amt; s.lots += 1; s.cnt = 0
                return -amt
        return 0.0

    def rec(s):
        return dict(j=s.j, e=s.e, x=s.exit, sig=s.sig, kind=s.kind, lots=s.lots, inv=s.inv, pnl=s.out - s.inv,
                    ret=s.out / s.inv - 1, reason=s.reason)


def _minc(p, amt):
    if p.min_comm <= 0:
        return 0.0
    return max(0.0, p.min_comm - amt * COMM)


def can_buy(g, t, j):
    return g["TR"][t, j] and not (g["O"][t, j] >= g["LU"][t, j] - 1e-6 or g["O"][t, j] <= g["LD"][t, j] + 1e-6)


def account(g, p: P, sig, cash0=50000.0):
    T = len(g["dates"]); codes = g["codes"]
    t0 = int(np.searchsorted(g["dates"], p.start)); t1 = int(np.searchsorted(g["dates"], p.end, side="right"))
    by = {}
    for t, j, kind, prio, nth in sig:
        if kind == 0 and nth > p.max_fires:
            continue
        by.setdefault(t + 1, []).append((kind, prio, codes[j], j, t))
    cash = cash0; prev_eq = cash0; pos = {}; done = []
    eq = np.full(T, np.nan); npos = np.zeros(T, int)
    for t in range(t0, t1):
        for j in list(pos):
            cash += pos[j].open_phase(g, t)
            if pos[j].exit is not None:
                done.append(pos[j].rec()); del pos[j]
        n0 = len(pos); seen = set()
        for kind, prio, code, j, s in sorted(by.get(t, [])):
            if j in seen or j in pos or len(pos) >= p.max_pos or (kind == 1 and n0 > 0):
                continue
            if not can_buy(g, t, j):
                continue
            lot = (p.main_pct if kind == 0 else p.fill_pct) * prev_eq
            px = g["O"][t, j]
            P_ = Pos(g, p, j, t, px, lot, kind, s)
            if P_.inv > cash:
                lot2 = cash * 0.98
                if lot2 < px * 100 * FEE_B:
                    continue
                P_ = Pos(g, p, j, t, px, lot2, kind, s)
                if P_.inv > cash:
                    continue
            cash -= P_.inv; pos[j] = P_; seen.add(j)
        for j in list(pos):
            if pos[j].e == t:
                continue
            cash += pos[j].rest_phase(g, t, cash)
            if pos[j].exit is not None:
                done.append(pos[j].rec()); del pos[j]
        mv = sum(P_.shares * P_.lastc for P_ in pos.values())
        eq[t] = cash + mv; npos[t] = len(pos); prev_eq = eq[t]
    return dict(eq=eq[t0:t1], dates=g["dates"][t0:t1], npos=npos[t0:t1], done=done, open=len(pos))


def stats(res, cash0=50000.0):
    eq = res["eq"]; d = pd.to_datetime(res["dates"])
    yrs = (d[-1] - d[0]).days / 365.25
    s = pd.Series(eq, d); ye = s.resample("YE").last(); yr = ye.pct_change(); yr.iloc[0] = ye.iloc[0] / cash0 - 1
    pn = np.array([x["pnl"] for x in res["done"]]) if res["done"] else np.zeros(0)
    return dict(期末=eq[-1], 年化=(eq[-1] / cash0) ** (1 / yrs) - 1, 最大回撤=(eq / np.maximum.accumulate(eq) - 1).min(),
                最差年=yr.min(), 盈利年=int((yr > 0).sum()), 年数=len(yr), 笔数=len(pn),
                胜率=(pn > 0).mean() if len(pn) else np.nan,
                盈亏比=pn[pn > 0].sum() / -pn[pn < 0].sum() if (pn < 0).any() else np.nan)


# ------------------------------------------------------------------ 信号级（每笔独立、固定金额，排除账户路径影响）
def trade_alone(g, p: P, t_sig, j, lot=10000.0):
    """信号日 t_sig 收盘 → t_sig+1 开盘买，按止盈/补仓/时间止损独立模拟（资金无限）。返回收益率或 None（买不了）。"""
    T = len(g["dates"]); e = t_sig + 1
    if e >= T or not can_buy(g, e, j):
        return None
    P_ = Pos(g, p, j, e, g["O"][e, j], lot, 0, t_sig)
    for t in range(e + 1, T):
        P_.open_phase(g, t)
        if P_.exit is None:
            amt = P_.rest_phase(g, t, 1e18)
        if P_.exit is not None:
            return P_.out / P_.inv - 1
    return None


def signal_level(g, p: P, sig, kinds=(0,), first_only=True):
    rows = []
    for t, j, kind, prio, nth in sig:
        if kind not in kinds or (kind == 0 and nth > p.max_fires):
            continue
        r = trade_alone(g, p, t, j)
        if r is not None:
            rows.append((g["dates"][t], kind, r))
    return pd.DataFrame(rows, columns=["date", "kind", "ret"])
