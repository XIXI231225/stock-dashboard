"""离线测试：用历史数据冒充接口，验证『每日增量』与『全量回放』结果逐位一致，并模拟各种故障。

python tests/test_pipeline.py <all.pkl> <种子目录(截至10-14)> <全量种子目录(截至10-16)>
"""
import json
import shutil
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_daily as rd  # noqa: E402
from engine import fetch  # noqa: E402
from engine.core import MAIN_PREFIX  # noqa: E402

ALL, SEED14, FULL = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
df = pd.read_pickle(ALL)
df = df[df.code.str[:5].isin(MAIN_PREFIX)]
df["d"] = df.date.dt.strftime("%Y-%m-%d")
BY = {d: g for d, g in df.groupby("d")}
CAL = sorted(BY)


def bars_of(d, src):
    g = BY[d]
    out = {}
    for r in g.itertuples():
        out[r.code] = dict(code=r.code, name=r.name, open=r.open, high=r.high, low=r.low, close=r.close,
                           preclose=r.preclose, vol=r.vol, amt=r.amt, fmv=None if pd.isna(r.fmv) else r.fmv,
                           tmv=r.tmv, src=src)
    return out


def install(target, *, tencent_drop=0.0, tencent_fail=False, sina_fail=False, corrupt=0, shift_sina=False):
    log_target = {"t": target}
    fetch.now_bj = lambda: datetime.fromisoformat(target + "T16:00:00+08:00")
    fetch.index_last_date = lambda log: (log_target["t"], "测试")
    fetch.trade_calendar = lambda since, log: [d for d in CAL if d >= since]

    def tq(codes, date, log):
        if tencent_fail:
            return {}
        b = bars_of(date, "腾讯")
        if tencent_drop:
            ks = sorted(b)[: int(len(b) * tencent_drop)]
            for k in ks:
                b.pop(k)
        for k in sorted(b)[:corrupt]:
            b[k] = {**b[k], "high": b[k]["low"] - 1}
        return b
    fetch.tencent_quotes = tq

    def sl(log):
        if sina_fail:
            return {}
        d = CAL[CAL.index(target) - 1] if shift_sina else target
        return bars_of(d, "新浪")
    fetch.sina_list = sl
    fetch.eastmoney_list = lambda log: {}
    fetch.backfill = lambda codes, beg, end, log: {d: bars_of(d, "回补") for d in CAL if beg <= d <= end}


def run(data, target, **kw):
    install(target, **kw)
    a = types.SimpleNamespace(data=str(data), date=None, dry_run=False)
    return rd.run_daily(a)


def fresh():
    t = Path(tempfile.mkdtemp())
    shutil.copytree(SEED14, t / "d")
    return t / "d"


def same_state(d1, d2):
    a = json.load(open(d1 / "state/state.json")); b = json.load(open(d2 / "state/state.json"))
    for k in ("dates", "arrivals", "pending", "prev_rk30"):
        assert a[k] == b[k], f"{k} 不一致"
    for n in a["accounts"]:
        A, B = a["accounts"][n], b["accounts"][n]
        assert A["eq_hist"] == B["eq_hist"], f"{n} 资金曲线不一致"
        assert A["done"] == B["done"] and A["pos"] == B["pos"], f"{n} 交易不一致"
    return True


ok = []
# 1. 正常两天增量
d = fresh(); run(d, "2026-10-15"); r = run(d, "2026-10-16")
assert same_state(d, FULL); ok.append("增量两天 = 全量回放（资金曲线、交易、观察池、待执行信号逐位一致）")
# 2. 重复触发不重复处理
r2 = run(d, "2026-10-16"); assert r2["status"] == "skip"; ok.append("同一交易日重复运行 → 跳过")
# 3. 漏跑一天，自动回补
d = fresh(); r = run(d, "2026-10-16")
assert same_state(d, FULL)
assert any("回补" in x["title"] for x in r["alerts"]); ok.append("漏跑 10-15 → 自动回补后结果仍一致，并发出提醒")
# 4. 腾讯缺 10% → 新浪补齐 + 红色告警
d = fresh(); run(d, "2026-10-15"); r = run(d, "2026-10-16", tencent_drop=0.10)
assert same_state(d, FULL) and any(x["level"] == "red" and "新浪补齐" in x["title"] for x in r["alerts"])
ok.append("腾讯缺10% → 新浪补齐，结果一致，红色告警")
# 5. 腾讯、新浪、东财都失败 → 不推进状态
d = fresh(); before = json.load(open(d / "state/state.json"))["dates"][-1]
try:
    run(d, "2026-10-15", tencent_fail=True, sina_fail=True); raise AssertionError("应当失败")
except fetch.FetchError:
    pass
assert json.load(open(d / "state/state.json"))["dates"][-1] == before; ok.append("所有源失败 → 抛错、状态不推进（之后的定时任务会重试）")
# 6. 坏数据行被剔除并告警
d = fresh(); r = run(d, "2026-10-15", corrupt=8)
assert any(x["level"] == "red" and "剔除" in x["title"] for x in r["alerts"]); ok.append("高低开收矛盾的行情 → 剔除并红色告警")
# 7. 新浪日期错位（拿到前一天的数据） → 交叉核对报警
d = fresh(); r = run(d, "2026-10-15", shift_sina=True)
assert any(x["level"] == "red" and "不一致" in x["title"] for x in r["alerts"]); ok.append("两个数据源收盘价对不上（日期错位）→ 红色告警")
# 8. 尚未收盘不运行
d = fresh(); install("2026-10-15"); fetch.now_bj = lambda: datetime.fromisoformat("2026-10-15T14:30:00+08:00")
assert rd.run_daily(types.SimpleNamespace(data=str(d), date=None, dry_run=False))["status"] == "skip"; ok.append("收盘前触发 → 跳过")
# 9. 网页构建
d = fresh(); run(d, "2026-10-15"); out = Path(tempfile.mkdtemp()) / "site"; rd.build_site(d, out)
for f in ("index.html", "data/latest.json", "data/market.json", "data/accounts.json", "data/runs.json", "data/snapshot.csv"):
    assert (out / f).exists(), f
ok.append("网页数据文件齐全")
print("\n".join("✅ " + x for x in ok))
