"""F版 过拟合检验。python research/overfit.py <mat.pkl> <输出pkl>"""
import sys
import pickle
import time
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, __import__("os").path.dirname(__file__))
import fast  # noqa: E402

g = pickle.load(open(sys.argv[1], "rb"))
OUT = sys.argv[2]
BASE = fast.P()
ACC = {"稳健版": BASE, "进取版": replace(BASE, main_pct=0.30)}
R = {}
t00 = time.time()


def run(p):
    sig, _ = fast.signals(g, p)
    return sig, fast.account(g, p, sig)


def sl_stats(df):
    if len(df) == 0:
        return dict(信号笔数=0, 每笔平均=np.nan, 每笔中位=np.nan, 胜率_信号=np.nan)
    r = df.ret.values
    return dict(信号笔数=len(r), 每笔平均=r.mean(), 每笔中位=np.median(r), 胜率_信号=(r > 0).mean())


def halves(df):
    a = df[df.date < "2022-01-01"].ret; b = df[df.date >= "2022-01-01"].ret
    return dict(前半段每笔=a.mean() if len(a) else np.nan, 前半段笔数=len(a), 后半段每笔=b.mean() if len(b) else np.nan, 后半段笔数=len(b))


# ---------------- A+B 参数邻域 + 前后半段
GRID = [
    ("进池名次 top_k", "top_k", [1, 2, 3, 5]),
    ("回看天数 look", "look", [10, 15, 20, 30, 40]),
    ("观察期 win", "win", [5, 7, 10, 15, 20]),
    ("跌幅下限 drop", "drop", [0.03, 0.04, 0.05, 0.06, 0.07]),
    ("流通市值门槛", "mcap", [0, 3e9, 5e9, 8e9, 1.2e10, 2e10]),
    ("跌停家数≥", "ldn_min", [0, 2, 4, 6, 11, 21]),
    ("补位排名≤", "fill_rank", [10, 15, 20, 30, 50]),
    ("止盈", "tp", [0.03, 0.04, 0.05, 0.06, 0.08]),
    ("补仓触发", "add_trig", [0.02, 0.03, 0.04, 0.05]),
    ("时间止损天数", "tstop", [2, 3, 4, 5, 7]),
    ("上市天数≥", "list_min", [21, 31, 61, 121]),
    ("每次进池最多买", "max_fires", [1, 2, 3]),
]
rows = []
for label, k, vals in GRID:
    for v in vals:
        p0 = replace(BASE, **{k: v})
        sig, _ = fast.signals(g, p0)
        sl = fast.signal_level(g, p0, sig, kinds=(0,))
        row = dict(参数=label, 取值=v, 是否F版=(getattr(BASE, k) == v), **sl_stats(sl), **halves(sl))
        for an, pa in ACC.items():
            pp = replace(pa, **{k: v})
            st = fast.stats(fast.account(g, pp, sig))
            h1 = fast.stats(fast.account(g, replace(pp, end="2021-12-31"), sig))
            h2 = fast.stats(fast.account(g, replace(pp, start="2022-01-04"), sig))
            row.update({f"{an}_期末": st["期末"], f"{an}_年化": st["年化"], f"{an}_最大回撤": st["最大回撤"],
                        f"{an}_最差年": st["最差年"], f"{an}_前半段年化": h1["年化"], f"{an}_后半段年化": h2["年化"]})
        rows.append(row)
        print(label, v, round(row["稳健版_年化"], 4), round(row["每笔平均"], 4), f"{time.time()-t00:.0f}s", flush=True)
# 补位关闭
p0 = replace(BASE, use_fill=False); sig, _ = fast.signals(g, p0)
row = dict(参数="补位信号", 取值="关闭", 是否F版=False)
for an, pa in ACC.items():
    st = fast.stats(fast.account(g, replace(pa, use_fill=False), sig))
    row.update({f"{an}_期末": st["期末"], f"{an}_年化": st["年化"], f"{an}_最大回撤": st["最大回撤"], f"{an}_最差年": st["最差年"]})
rows.append(row)
# 不补仓
p0 = replace(BASE, addx=()); sig, _ = fast.signals(g, p0)
sl = fast.signal_level(g, p0, sig)
row = dict(参数="补仓触发", 取值="不补仓", 是否F版=False, **sl_stats(sl), **halves(sl))
for an, pa in ACC.items():
    pp = replace(pa, addx=())
    st = fast.stats(fast.account(g, pp, sig))
    row.update({f"{an}_期末": st["期末"], f"{an}_年化": st["年化"], f"{an}_最大回撤": st["最大回撤"], f"{an}_最差年": st["最差年"]})
rows.append(row)
R["邻域"] = pd.DataFrame(rows)

# ---------------- C 滚动样本外（walk-forward）
WF_GRID = dict(win=[5, 10, 15], drop=[0.04, 0.05, 0.06], mcap=[5e9, 8e9, 1.2e10], tp=[0.04, 0.05, 0.06], tstop=[2, 3, 4])
combos = list(itertools.product(*WF_GRID.values()))
yr_ret = {}
sig_cache = {}
for an, pa in ACC.items():
    yr_ret[an] = {}
    for c in combos:
        kw = dict(zip(WF_GRID, c))
        sk = (kw["win"], kw["drop"], kw["mcap"])
        if sk not in sig_cache:
            sig_cache[sk] = fast.signals(g, replace(BASE, win=kw["win"], drop=kw["drop"], mcap=kw["mcap"], fill_mcap=kw["mcap"]))[0]
        pp = replace(pa, **kw, fill_mcap=kw["mcap"])
        res = fast.account(g, pp, sig_cache[sk])
        s = pd.Series(res["eq"], pd.to_datetime(res["dates"])); ye = s.resample("YE").last()
        yr = ye.pct_change(); yr.iloc[0] = ye.iloc[0] / 50000 - 1
        yr_ret[an][c] = yr.values
    print("walk-forward grid", an, f"{time.time()-t00:.0f}s", flush=True)
years = list(range(2018, 2027))
base_c = (10, 0.05, 8e9, 0.05, 3)
wf_rows = []
for an in ACC:
    Y = yr_ret[an]
    for i, y in enumerate(years):
        if i < 2:
            continue
        train = {c: np.prod(1 + v[:i]) for c, v in Y.items()}
        best = max(train, key=train.get)
        allv = np.array([v[i] for v in Y.values()])
        wf_rows.append(dict(账户=an, 年份=y, 训练期=f"2018-{y-1}", 训练期最优参数=dict(zip(WF_GRID, best)),
                            F版参数训练期排名=f"{sorted(train.values(), reverse=True).index(train[base_c]) + 1}/{len(train)}",
                            样本外_滚动选参收益=Y[best][i], 样本外_F版固定参数收益=Y[base_c][i],
                            当年全部243组中位数=np.median(allv), 当年F版在243组中的分位=(allv < Y[base_c][i]).mean()))
R["滚动样本外"] = pd.DataFrame(wf_rows)
# 全样本：F版在243组里的位置
full = {an: {c: np.prod(1 + v) for c, v in yr_ret[an].items()} for an in ACC}
R["参数组合分布"] = pd.DataFrame([dict(账户=an, 组合数=len(full[an]), F版累计倍数=full[an][base_c],
                                  F版排名=sorted(full[an].values(), reverse=True).index(full[an][base_c]) + 1,
                                  最优累计倍数=max(full[an].values()), 中位数累计倍数=np.median(list(full[an].values())),
                                  最差累计倍数=min(full[an].values()),
                                  跑赢5万本金的组合占比=np.mean(np.array(list(full[an].values())) > 1),
                                  年化中位数=np.median(list(full[an].values())) ** (1 / 8.79) - 1) for an in ACC])

# ---------------- D 安慰剂
rng = np.random.default_rng(42)
LDN, YZD = fast.market(g)
TR, C, PC, LD, BAD, FMV = g["TR"], g["C"], g["PC"], g["LD"], g["BAD"], g["FMV"]
drop_ok = TR & ~BAD & ~YZD & (C <= PC * 0.95 + 1e-9) & (C >= LD - 1e-6)
sig, _ = fast.signals(g, BASE)
main = [(t, j) for t, j, k, pr, n in sig if k == 0 and n == 1]
fill = [(t, j) for t, j, k, pr, n in sig if k == 1]
cache = {}


def tr(t, j):
    if (t, j) not in cache:
        cache[(t, j)] = fast.trade_alone(g, BASE, t, j)
    return cache[(t, j)]


def avg(pairs):
    r = [tr(t, j) for t, j in pairs]; r = [x for x in r if x is not None]
    return np.mean(r), len(r)


actual_main, n_main = avg(main); actual_fill, n_fill = avg(fill)
pl = []
for name, pool_fn, base_list, actual in [
    ("主信号 vs 同日随机『跌5~10%、市值≥80亿』的股票", lambda t: np.where(drop_ok[t] & (FMV[t] >= 8e9))[0], main, actual_main),
    ("主信号 vs 同日随机『市值≥80亿』任意股票", lambda t: np.where(TR[t] & ~BAD[t] & (FMV[t] >= 8e9))[0], main, actual_main),
    ("补位 vs 同日随机『跌5~10%、市值≥80亿』的股票", lambda t: np.where(drop_ok[t] & (FMV[t] >= 8e9))[0], fill, actual_fill),
]:
    sims = []
    for _ in range(300):
        pairs = []
        for t, j in base_list:
            cand = pool_fn(t)
            if len(cand):
                pairs.append((t, int(rng.choice(cand))))
        sims.append(avg(pairs)[0])
    sims = np.array(sims)
    pl.append(dict(对照=name, 实际每笔平均=actual, 随机300次平均=sims.mean(), 随机95分位=np.percentile(sims, 95),
                   随机最好一次=sims.max(), 实际超过随机的比例=(sims < actual).mean(), 样本笔数=len(base_list)))
    print(name, actual, sims.mean(), f"{time.time()-t00:.0f}s", flush=True)
R["安慰剂"] = pd.DataFrame(pl)

# ---------------- E 成本
cr = []
for lab, kw in [("无滑点（回测原设定）", {}), ("单边滑点0.1%", dict(slip=0.001)), ("单边滑点0.2%", dict(slip=0.002)),
                ("单边滑点0.3%", dict(slip=0.003)), ("5元最低佣金", dict(min_comm=5.0)), ("滑点0.2%+5元最低佣金", dict(slip=0.002, min_comm=5.0))]:
    row = dict(成本设定=lab)
    for an, pa in ACC.items():
        st = fast.stats(fast.account(g, replace(pa, **kw), sig))
        row.update({f"{an}_期末": st["期末"], f"{an}_年化": st["年化"], f"{an}_最大回撤": st["最大回撤"], f"{an}_最差年": st["最差年"]})
    cr.append(row)
R["成本压力"] = pd.DataFrame(cr)

# ---------------- F 逐年
sl_all = fast.signal_level(g, BASE, sig, kinds=(0, 1))
sl_all["年"] = sl_all.date.str[:4]
yrows = []
for y, d in sl_all.groupby("年"):
    m = d[d.kind == 0].ret; f = d[d.kind == 1].ret
    yrows.append(dict(年份=y, 主信号笔数=len(m), 主信号每笔平均=m.mean() if len(m) else np.nan, 主信号胜率=(m > 0).mean() if len(m) else np.nan,
                      补位笔数=len(f), 补位每笔平均=f.mean() if len(f) else np.nan, 补位胜率=(f > 0).mean() if len(f) else np.nan))
R["逐年信号"] = pd.DataFrame(yrows)
# 去掉最好的年份/最好的N笔
ex = []
for an, pa in ACC.items():
    res = fast.account(g, pa, sig)
    s = pd.Series(res["eq"], pd.to_datetime(res["dates"])); ye = s.resample("YE").last(); yr = ye.pct_change(); yr.iloc[0] = ye.iloc[0] / 50000 - 1
    yr = yr.values
    best = yr.argmax(); wo = np.prod(1 + np.delete(yr, best)) ** (1 / 7.79) - 1
    pn = np.sort(np.array([x["pnl"] for x in res["done"]]))[::-1]
    ex.append(dict(账户=an, 全部年化=np.prod(1 + yr) ** (1 / 8.79) - 1, 去掉最好一年后年化=wo, 最好一年=years[best],
                   总盈亏=pn.sum(), 去掉最赚的10笔后总盈亏=pn[10:].sum(), 去掉最赚的20笔后总盈亏=pn[20:].sum()))
R["集中度"] = pd.DataFrame(ex)
pickle.dump(R, open(OUT, "wb"))
print("DONE", f"{time.time()-t00:.0f}s")
