"""F版策略 每日增量引擎。

一套代码两种用法：
  * 历史回放：从 2018 起逐日喂入日线，得到与原回测一致的账户和信号（用来做种子 + 自检）
  * 每日增量：GitHub Actions 每天收盘后只喂入当天一行行情，推进状态

口径来自 H:\\AIspace\\首次到达Top2_回撤买法\\code (combo.py / sizing.py / engine2.py / final_F.py)，未改动。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict

import numpy as np

MAIN_PREFIX = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
COMM = 0.000085
TRANS = 0.00001
FEE_B = 1 + COMM + TRANS
STAMP_CUT = "2023-08-28"
CASH0 = 50000.0
HIST = 21          # 保留每只股票最近 21 个自身交易日的复权收盘（20日涨幅 / MA20）
LOOK = 20          # 首次进前二：之前 20 个交易日未进过前二
WIN = 10           # 观察期 10 个交易日
MCAP = 8e9         # 流通市值 ≥ 80 亿
FIRSTDAY_OLD = True
LIST_MIN = 31      # 上市满 30 个交易日：自身成交日数（含当天）≥31，与原回测排名逐笔核对一致

ACCOUNTS = {
    # name: (主信号首笔比例, 补位首笔比例, 每次进池最多买几次主信号)
    "稳健版": (0.20, 0.04, 1),            # F版原规则
    "进取版": (0.30, 0.04, 1),            # F版原规则
    "稳健版·二次买": (0.20, 0.04, 2),     # 对照：观察期内允许第2次主信号
    "进取版·二次买": (0.30, 0.04, 2),
}
MAX_FIRES = max(v[2] for v in ACCOUNTS.values())
ADDX = (2.0, 2.5)
MAX_POS = 4


def stamp(d: str) -> float:
    return 0.001 if d < STAMP_CUT else 0.0005


def r2(x: float) -> float:
    return math.floor(x * 100 + 0.5 + 1e-7) / 100


def ceil2(x: float) -> float:
    return math.ceil(x * 100 - 1e-7) / 100


ST_LIMIT_10_FROM = "2026-07-06"   # 主板风险警示股涨跌幅由5%调整为10%（数据核实：该日起ST股出现±10%）


def limit_pct(bad: bool, date: str) -> float:
    if bad and date < ST_LIMIT_10_FROM:
        return 0.05
    return 0.10


def is_bad_name(name: str) -> bool:
    return (not isinstance(name, str)) or ("ST" in name) or ("退" in name)


# ---------------------------------------------------------------- 当日行情视图
@dataclass
class Day:
    """一个交易日，所有数组按 State.codes 对齐。tr=当天有成交（含 09-21 仅收盘行）。"""
    date: str
    tr: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    pc: np.ndarray
    vol: np.ndarray
    amt: np.ndarray
    fmv: np.ndarray
    bad: np.ndarray
    lu: np.ndarray
    ld: np.ndarray
    names: list

    def can_buy(self, j: int) -> bool:
        return bool(self.tr[j]) and not (self.o[j] >= self.lu[j] - 1e-6 or self.o[j] <= self.ld[j] + 1e-6)


# ---------------------------------------------------------------- 持仓（与 engine2.Pos 逐行对应）
@dataclass
class Pos:
    code: str
    e: str                 # 买入日
    shares: float
    cost: float
    inv: float
    out: float
    lots: int
    cnt: int
    pend: bool
    lastc: float
    lot: float
    src: str
    sig: str               # 信号日
    skipped_add: int = 0
    exit: str | None = None
    reason: str | None = None
    exit_px: float | None = None
    entry_px: float = 0.0
    hold: int = 1          # 持有交易日（含买入日、仅计该股交易日）
    flows: list = field(default_factory=list)   # [(日期, 动作, 价格, 股数, 金额)]

    @classmethod
    def open(cls, code, d: str, px: float, lot: float, src: str, sig: str, close: float):
        sh = max(100, round(lot / px / 100) * 100)
        p = cls(code=code, e=d, shares=float(sh), cost=sh * px, inv=sh * px * FEE_B, out=0.0, lots=1, cnt=0,
                pend=False, lastc=close, lot=lot, src=src, sig=sig, entry_px=px)
        p.flows.append((d, "买入", px, sh, -p.inv))
        return p

    def _sell(self, d, p, why):
        amt = self.shares * p * (1 - COMM - TRANS - stamp(d))
        self.flows.append((d, "卖出", p, self.shares, amt))
        self.out += amt; self.exit = d; self.reason = why; self.exit_px = p; self.shares = 0.0
        return amt

    def tp_price(self):
        return ceil2(self.cost / self.shares * 1.05)

    def open_phase(self, D: Day, j: int) -> float:
        if not D.tr[j]:
            return 0.0
        f = self.lastc / D.pc[j]
        if abs(f - 1) > 1e-4:
            self.shares *= f
        self.lastc = float(D.c[j]); self.cnt += 1; self.hold += 1
        if self.pend:
            if D.o[j] > D.ld[j] + 1e-6:
                return self._sell(D.date, float(D.o[j]), "DEFER_open")
            return 0.0
        tpp = self.tp_price()
        if D.o[j] >= tpp - 1e-9:
            return self._sell(D.date, float(D.o[j]), "TP_open")
        return 0.0

    def rest_phase(self, D: Day, j: int, cash: float, delisted: bool) -> float:
        if not D.tr[j]:
            if delisted:
                return self._sell(D.date, self.lastc, "DELIST")
            return 0.0
        if self.pend:
            if D.h[j] > D.ld[j] + 1e-6:
                return self._sell(D.date, float(D.ld[j]), "DEFER")
            return 0.0
        avg = self.cost / self.shares
        tpp = ceil2(avg * 1.05)
        if D.h[j] >= tpp + 0.01 - 1e-9:
            return self._sell(D.date, tpp, "TP")
        if self.cnt >= 3:
            if D.c[j] <= D.ld[j] + 1e-6:
                self.pend = True; return 0.0
            return self._sell(D.date, float(D.c[j]), "TIME")
        c = float(D.c[j])
        if self.lots < 3 and c <= avg * 0.97 and c > D.ld[j] + 1e-6:
            tgt = ADDX[self.lots - 1] * self.lot
            add = round((tgt - self.shares * c) / c / 100) * 100
            if add > 0:
                amt = add * c * FEE_B
                if amt > cash:
                    add = math.floor(cash / (c * FEE_B) / 100) * 100; amt = add * c * FEE_B
                    if add < 100:
                        self.skipped_add += 1; return 0.0
                self.cost += add * c; self.shares += add; self.inv += amt; self.lots += 1; self.cnt = 0
                self.flows.append((D.date, "补仓", c, add, -amt))
                return -amt
        return 0.0

    def record(self):
        return dict(code=self.code, src=self.src, sig=self.sig, entry=self.e, entry_px=self.entry_px, lot=self.lot,
                    exit=self.exit, exit_px=self.exit_px, reason=self.reason, lots=self.lots, hold=self.hold,
                    invested=self.inv, pnl=self.out - self.inv, ret=self.out / self.inv - 1,
                    skipped_add=self.skipped_add, flows=self.flows)


@dataclass
class Account:
    name: str
    main_pct: float
    fill_pct: float
    max_fires: int = 1
    cash: float = CASH0
    prev_eq: float = CASH0
    pos: dict = field(default_factory=dict)       # code -> Pos
    done: list = field(default_factory=list)      # 已平仓记录
    eq_hist: list = field(default_factory=list)   # [(date, eq, mv, npos)]

    def step(self, D: Day, idx: dict, pending: list, delisted: set):
        """pending: 前一日收盘生成的信号 [(kind, prio, code, sig_date, src, nth)]，nth=该次进池第几次主信号"""
        events = []
        for code in list(self.pos):
            P = self.pos[code]; j = idx[code]
            self.cash += P.open_phase(D, j)
            if P.exit is not None:
                self.done.append(P.record()); events.append(("卖出", P.record())); del self.pos[code]
        n0 = len(self.pos); seen = set()
        for kind, prio, code, sig, src, nth in sorted(pending):
            if kind == 0 and nth > self.max_fires:
                continue
            if code in seen or code in self.pos:
                continue
            if len(self.pos) >= MAX_POS:
                continue
            if kind == 1 and n0 > 0:
                continue
            j = idx.get(code)
            if j is None or not D.can_buy(j):
                continue
            fp = self.main_pct if kind == 0 else self.fill_pct
            lot = fp * self.prev_eq
            px = float(D.o[j]); close = float(D.c[j])
            P = Pos.open(code, D.date, px, lot, src, sig, close)
            if P.inv > self.cash:
                lot2 = self.cash * 0.98
                if lot2 < px * 100 * FEE_B:
                    continue
                P = Pos.open(code, D.date, px, lot2, src, sig, close)
                if P.inv > self.cash:
                    continue
            self.cash -= P.inv; self.pos[code] = P; seen.add(code)
            events.append(("买入", P.record()))
        for code in list(self.pos):
            P = self.pos[code]
            if P.e == D.date:
                continue
            j = idx[code]
            self.cash += P.rest_phase(D, j, self.cash, code in delisted)
            if P.exit is not None:
                self.done.append(P.record()); events.append(("卖出", P.record())); del self.pos[code]
        mv = sum(P.shares * P.lastc for P in self.pos.values())
        eq = self.cash + mv
        self.eq_hist.append((D.date, round(eq, 2), round(mv, 2), len(self.pos)))
        self.prev_eq = eq
        return events

    def to_json(self):
        return dict(name=self.name, main_pct=self.main_pct, fill_pct=self.fill_pct, max_fires=self.max_fires, cash=self.cash, prev_eq=self.prev_eq,
                    pos={k: asdict(v) for k, v in self.pos.items()}, done=self.done, eq_hist=self.eq_hist)

    @classmethod
    def from_json(cls, d):
        a = cls(d["name"], d["main_pct"], d["fill_pct"], max_fires=d.get("max_fires", 1), cash=d["cash"], prev_eq=d["prev_eq"])
        a.pos = {k: Pos(**{**v, "flows": [tuple(x) for x in v["flows"]]}) for k, v in d["pos"].items()}
        a.done = d["done"]; a.eq_hist = [tuple(x) for x in d["eq_hist"]]
        return a


# ---------------------------------------------------------------- 全局状态
class State:
    def __init__(self):
        self.codes: list[str] = []
        self.idx: dict[str, int] = {}
        self.names: dict[str, str] = {}
        self.ac = np.zeros((0, HIST))        # 复权收盘（自身交易日滚动）
        self.raw = np.zeros((0, 3))          # 原始收盘最近3根（MA3 报告口径）
        self.acm = np.zeros((0, HIST))       # 复权收盘（按市场交易日滚动，停牌沿用前值）→ 20日涨幅
        self.ndays = np.zeros(0, int)        # 累计交易日数
        self.lastc = np.zeros(0)             # 上一个交易日原始收盘
        self.fsh = np.zeros(0)               # 最近已知流通股本（补市值缺失）
        self.last_date = np.array([], dtype=object)
        self.first_date = np.array([], dtype=object)
        self.dates: list[str] = []
        self.top2_hist: list[list[str]] = []   # 最近 LOOK 个交易日的前二
        self.arrivals: list[dict] = []          # 活跃观察池 {a, code, rank, r20, fired, fired_date}
        self.prev_rk30: dict[str, int] = {}
        self.prev_top10: list[str] = []
        self.eff_hist: list[float] = []
        self.pending: list = []                  # 明日开盘待执行信号
        self.accounts = {n: Account(n, m, f, k) for n, (m, f, k) in ACCOUNTS.items()}

    # ---------- 代码表扩展
    def _ensure(self, codes):
        new = [c for c in codes if c not in self.idx]
        if not new:
            return
        for c in new:
            self.idx[c] = len(self.codes); self.codes.append(c)
        k = len(new)
        self.ac = np.vstack([self.ac, np.full((k, HIST), np.nan)])
        self.raw = np.vstack([self.raw, np.full((k, 3), np.nan)])
        self.acm = np.vstack([self.acm, np.full((k, HIST), np.nan)])
        self.ndays = np.concatenate([self.ndays, np.zeros(k, int)])
        self.lastc = np.concatenate([self.lastc, np.full(k, np.nan)])
        self.fsh = np.concatenate([self.fsh, np.full(k, np.nan)])
        self.last_date = np.concatenate([self.last_date, np.array([None] * k, dtype=object)])
        self.first_date = np.concatenate([self.first_date, np.array([None] * k, dtype=object)])

    # ---------- 喂入一天
    def feed(self, date: str, bars, delisted: set | None = None):
        """bars: list of dict(code,name,open,high,low,close,preclose,vol,amt,fmv)，只含当天有成交的主板股票。
        返回当天的报告字典。"""
        assert not self.dates or date > self.dates[-1], f"日期倒退/重复: {date} <= {self.dates[-1]}"
        delisted = delisted or set()
        self._ensure([b["code"] for b in bars])
        N = len(self.codes)
        tr = np.zeros(N, bool); o = np.full(N, np.nan); h = o.copy(); l = o.copy(); c = o.copy(); pc = o.copy()
        vol = np.zeros(N); amt = np.zeros(N); fmv = np.full(N, np.nan); bad = np.ones(N, bool)
        names = [self.names.get(cd, "") for cd in self.codes]
        for b in bars:
            j = self.idx[b["code"]]
            tr[j] = True; o[j] = b["open"]; h[j] = b["high"]; l[j] = b["low"]; c[j] = b["close"]; pc[j] = b["preclose"]
            vol[j] = b.get("vol") or 0; amt[j] = b.get("amt") or 0
            fm = b.get("fmv")
            if fm is not None and np.isfinite(fm) and fm > 0:
                fmv[j] = fm; self.fsh[j] = fm / b["close"]
            elif np.isfinite(self.fsh[j]):
                fmv[j] = self.fsh[j] * b["close"]
            bad[j] = is_bad_name(b["name"]); self.names[b["code"]] = b["name"]; names[j] = b["name"]
            self.last_date[j] = date
            if self.first_date[j] is None:
                # 数据首日就存在的股票视为老股（上市日早于数据起点）
                self.first_date[j] = "2000-01-01" if (not self.dates and FIRSTDAY_OLD) else date
        lim = np.where(bad & (date < ST_LIMIT_10_FROM), 0.05, 0.10)
        lu = np.array([r2(p * (1 + x)) if t else np.nan for p, x, t in zip(pc, lim, tr)])
        ld = np.array([r2(p * (1 - x)) if t else np.nan for p, x, t in zip(pc, lim, tr)])
        D = Day(date, tr, o, h, l, c, pc, vol, amt, fmv, bad, lu, ld, names)

        # 复权收盘滚动
        m = tr
        prev_ac = self.ac[m, -1]
        new_ac = np.where(np.isfinite(prev_ac), prev_ac * (c[m] / pc[m]), c[m] / pc[m])   # 与 cumprod(C/PC) 逐位一致
        self.ac[m, :-1] = self.ac[m, 1:]; self.ac[m, -1] = new_ac
        self.raw[m, :-1] = self.raw[m, 1:]; self.raw[m, -1] = c[m]
        if not self.dates and FIRSTDAY_OLD:
            self.ndays[m] = 10000      # 数据首日已存在 → 视为老股
        self.ndays[m] += 1; self.lastc[m] = c[m]

        self.acm[:, :-1] = self.acm[:, 1:]
        self.acm[:, -1] = np.where(m, self.ac[:, -1], self.acm[:, -2])
        r20 = np.where(tr & np.isfinite(self.acm[:, 0]), self.acm[:, -1] / self.acm[:, 0] - 1, np.nan)
        full20 = tr & np.isfinite(self.ac[:, 1])
        ma20 = np.full(N, np.nan)
        ma20[full20] = self.ac[full20, 1:].mean(axis=1)
        dnum = np.datetime64(date)
        age = np.array([(dnum - np.datetime64(f)).astype(int) if f else -1 for f in self.first_date])
        elig = tr & (self.ndays >= LIST_MIN) & np.isfinite(r20) & ~bad
        R = np.where(elig, r20, -np.inf)
        order = np.argsort(-R)   # 与原回测相同的排序实现（并列时的先后也一致）
        rk30 = {}
        for k, j in enumerate(order[: min(30, int(elig.sum()))]):
            rk30[self.codes[j]] = k + 1
        top10 = [cd for cd, r in rk30.items() if r <= 10]
        top2 = [cd for cd, r in rk30.items() if r <= 2]

        yzd = tr & (np.abs(h - ld) < 0.005)
        ldn = int(((c <= ld + 1e-6) & tr & ~bad).sum())
        valid_b = tr & np.isfinite(ma20)
        above = int((valid_b & (self.ac[:, -1] > ma20)).sum())
        br = above / max(int(valid_b.sum()), 1)
        pct = np.where(tr, c / pc - 1, np.nan)
        prev_t10 = [self.idx[cd] for cd in self.prev_top10]
        v = pct[prev_t10] if prev_t10 else np.array([])
        v = v[np.isfinite(v)]
        eff = float(v.mean()) if len(v) else float("nan")
        self.eff_hist.append(eff)
        eff_ma3 = float(np.mean(self.eff_hist[-3:])) if len(self.eff_hist) >= 3 else float("nan")

        def is_drop(j):
            if not tr[j] or bad[j] or yzd[j]:
                return False
            return c[j] <= pc[j] * 0.95 + 1e-9 and c[j] >= ld[j] - 1e-6

        # ---- 1) 账户：执行昨天收盘生成的信号
        acct_events = {n: a.step(D, self.idx, self.pending, delisted) for n, a in self.accounts.items()}

        # ---- 2) 生成今日信号（明日开盘执行）
        n = len(self.dates)   # 今天的序号
        # 观察池到期
        self.arrivals = [a for a in self.arrivals if n - a["a"] <= WIN]
        main, covered = [], set()
        for a in self.arrivals:
            if a["a"] < n:
                covered.add(a["code"])
                nf = a.get("nf", 1 if a["fired"] else 0)
                if nf < MAX_FIRES:
                    j = self.idx[a["code"]]
                    if is_drop(j) and fmv[j] >= MCAP and ldn > 3:
                        nf += 1; a["nf"] = nf
                        a.setdefault("fire_dates", ([a["fired_date"]] if a["fired"] else []))
                        a["fire_dates"].append(date)
                        if not a["fired"]:
                            a["fired"] = True; a["fired_date"] = date
                        main.append((0, -a["r20"], a["code"], date, "主信号", nf))
        fill = []
        for cd, rk in self.prev_rk30.items():
            if rk <= 20 and cd not in covered:
                j = self.idx[cd]
                if is_drop(j) and fmv[j] >= MCAP:
                    fill.append((1, rk, cd, date, "补位", 0))
        # 新进观察池（今天首次进前二）
        recent = set(x for d in self.top2_hist[-LOOK:] for x in d)
        new_arr = []
        for cd in top2:
            if cd not in recent:
                j = self.idx[cd]
                a = dict(a=n, date=date, code=cd, rank=rk30[cd], r20=float(r20[j]), fired=False, fired_date=None)
                self.arrivals.append(a); new_arr.append(a)
        self.top2_hist.append(top2); self.top2_hist = self.top2_hist[-LOOK:]
        self.pending = main + fill
        self.prev_rk30 = rk30; self.prev_top10 = top10
        self.dates.append(date)

        rep = dict(date=date, n_traded=int(tr.sum()), ldn=ldn, breadth=br, breadth_above=above,
                   breadth_valid=int(valid_b.sum()), eff=eff, eff_ma3=eff_ma3,
                   main=[x[2] for x in main], fill=[x[2] for x in fill], new_arrivals=[x["code"] for x in new_arr],
                   acct_events={k: [(e, r["code"], r["src"]) for e, r in v] for k, v in acct_events.items()})
        self._last = dict(D=D, r20=r20, ma20=ma20, rk30=rk30, yzd=yzd)
        return rep

    # ---------- 序列化
    def save(self, path_npz, path_json):
        np.savez_compressed(path_npz, ac=self.ac, acm=self.acm, raw=self.raw, ndays=self.ndays, lastc=self.lastc, fsh=self.fsh)
        js = dict(codes=self.codes, names=self.names, last_date=list(self.last_date), first_date=list(self.first_date), dates=self.dates,
                  top2_hist=self.top2_hist, arrivals=self.arrivals, prev_rk30=self.prev_rk30,
                  prev_top10=self.prev_top10, eff_hist=self.eff_hist[-10:],
                  pending=[list(x) for x in self.pending],
                  accounts={k: a.to_json() for k, a in self.accounts.items()})
        with open(path_json, "w", encoding="utf-8") as f:
            json.dump(js, f, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def load(cls, path_npz, path_json):
        s = cls()
        z = np.load(path_npz)
        s.ac, s.acm, s.raw, s.ndays, s.lastc, s.fsh = z["ac"], z["acm"], z["raw"], z["ndays"], z["lastc"], z["fsh"]
        with open(path_json, encoding="utf-8") as f:
            js = json.load(f)
        s.codes = js["codes"]; s.idx = {c: i for i, c in enumerate(s.codes)}; s.names = js["names"]
        s.last_date = np.array(js["last_date"], dtype=object); s.first_date = np.array(js["first_date"], dtype=object); s.dates = js["dates"]
        s.top2_hist = js["top2_hist"]; s.arrivals = js["arrivals"]; s.prev_rk30 = js["prev_rk30"]
        s.prev_top10 = js["prev_top10"]; s.eff_hist = js["eff_hist"]
        s.pending = [tuple(x) if len(x) == 6 else tuple(x) + ((1 if x[0] == 0 else 0),) for x in js["pending"]]
        s.accounts = {k: Account.from_json(v) for k, v in js["accounts"].items()}
        return s
