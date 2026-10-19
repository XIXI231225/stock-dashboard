"""规则或账户变化后重建 data 分支：从 seed/ 出发，用 data 分支里已保存的每日快照逐日重放。

python tools/rebuild_data.py <seed目录> <旧data目录> <新data目录>
- 只用已保存的快照，不重新抓取，因此和当时实际入库的数据完全一致
- 保留旧 runs.jsonl（运行记录），追加一条"重建"记录
"""
import csv
import gzip
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_daily as rd  # noqa: E402
from engine import sitedata, checks  # noqa: E402

seed, old, new = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
if new.exists():
    shutil.rmtree(new)
shutil.copytree(seed, new)
S = rd.load_state(new)
snaps = sorted(p for p in (old / "snapshots").glob("*.csv.gz") if p.name[:10] > S.dates[-1])
rep = None
for p in snaps:
    d = p.name[:10]
    bars = []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            b = dict(r)
            for k in ("open", "high", "low", "close", "preclose", "vol", "amt", "fmv", "tmv"):
                b[k] = float(b[k]) if b[k] not in ("", None) else None
            bars.append(b)
    rep = S.feed(d, bars)
    rd.append_market(new, rep, S)
    shutil.copy(p, new / "snapshots" / p.name)
    print("重放", d, len(bars), "只")
rd.save_state(S, new)
if rep:
    oldlatest = json.load(open(old / "latest.json", encoding="utf-8"))
    latest = sitedata.make_latest(S, rep, [], oldlatest["quality"], oldlatest["generated_at"])
    latest["alerts"] = [a for a in oldlatest["alerts"] if a["level"] == "red"] + checks.market_alerts(rep) + \
        rd.strategy_alerts(S, rep, latest)
    rd.jdump(latest, new / "latest.json")
shutil.copy(old / "history/runs.jsonl", new / "history/runs.jsonl")
for f in ("notify_status.json",):
    if (old / f).exists():
        shutil.copy(old / f, new / f)
with open(new / "history/runs.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(dict(date=S.dates[-1], run_at="重建", sources=["账户规则变更后用已保存快照重放"], stats={},
                            alerts=[checks.alert("info", "重建数据", "新增二次买对照账户，原账户结果不变")]),
                       ensure_ascii=False) + "\n")
for n, A in S.accounts.items():
    print(n, round(A.eq_hist[-1][1]), [p.code for p in A.pos.values()])
