"""一次性：用本地历史日线（谷票主板 2018-01-02 ~ 2026-10-16）逐日回放，生成种子数据目录。

python tools/make_seed.py <all.pkl> <输出目录>
all.pkl 由 H:\\AIspace\\首次到达Top2_回撤买法\\code\\load.py 同口径读入（09-21 仅收盘价行按一字处理）。
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.core import State, MAIN_PREFIX  # noqa: E402
from engine import sitedata  # noqa: E402
import run_daily as rd  # noqa: E402

src, out = sys.argv[1], Path(sys.argv[2])
stop = sys.argv[3] if len(sys.argv) > 3 else "9999"
out.mkdir(parents=True, exist_ok=True)
df = pd.read_pickle(src)
df = df[df.code.str[:5].isin(MAIN_PREFIX)].sort_values(["date", "code"])
df["d"] = df.date.dt.strftime("%Y-%m-%d")
last = df.groupby("code").d.max()
cols = ["code", "name", "open", "high", "low", "close", "preclose", "vol", "amt", "fmv"]
S = State(); S._ensure(sorted(df.code.unique()))
(out / "history").mkdir(exist_ok=True)
mk = out / "history/market.csv"
if mk.exists():
    mk.unlink()
rep = None; bars = None
for d, g in df.groupby("d", sort=True):
    if d > stop:
        break
    bars = g[cols].to_dict("records")
    for b in bars:
        if pd.isna(b["fmv"]):
            b["fmv"] = None
    rep = S.feed(d, bars, set(last[last < d].index))
    rd.append_market(out, rep, S)
rd.save_state(S, out)
rd.write_snapshot(out, rep["date"], [{**b, "src": "谷票历史数据"} for b in bars])
quality = dict(sources=[f"{rep['date']}: 种子（本地历史数据回放 2018-01-02 起）"], stats={}, log=[], index_src="-")
latest = sitedata.make_latest(S, rep, [], quality, "种子数据")
latest["alerts"] = rd.strategy_alerts(S, rep, latest)
rd.jdump(latest, out / "latest.json")
with open(out / "history/runs.jsonl", "w", encoding="utf-8") as f:
    f.write(json.dumps(dict(date=rep["date"], run_at="种子", sources=quality["sources"], stats={}, alerts=latest["alerts"]),
                       ensure_ascii=False) + "\n")
for n, A in S.accounts.items():
    print(n, round(A.eq_hist[-1][1]), len(A.done), "笔已平仓 +", len(A.pos), "持仓")
