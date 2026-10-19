"""每日任务入口。

  python run_daily.py --data <数据目录> --site <网页输出目录>            # 正常每日运行
  python run_daily.py --data <数据目录> --site <网页输出目录> --build    # 只用已有数据重建网页
  加 --dry-run 只抓取和计算，不写任何状态

数据目录（data 分支）结构：
  state/state.npz, state/state.json   引擎状态（含两个回测账户）
  state/backup/                       最近几天的状态备份
  history/market.csv                  每日市场指标
  history/runs.jsonl                  每次运行的数据来源/质量/告警
  snapshots/YYYY-MM-DD.csv.gz         当日主板全量行情
  latest.json                         最新一期看板数据
  notify_status.json                  通知发送记录（防重复）
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.core import State  # noqa: E402
from engine import fetch, checks, sitedata, notify  # noqa: E402

MARKET_COLS = ["date", "n_traded", "ldn", "breadth", "breadth_above", "breadth_valid", "eff", "eff_ma3", "top10"]


def jdump(obj, path, compact=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 1)


def load_state(data: Path) -> State:
    return State.load(data / "state/state.npz", data / "state/state.json")


def save_state(S: State, data: Path):
    st = data / "state"; bk = st / "backup"; bk.mkdir(parents=True, exist_ok=True)
    for f in ("state.npz", "state.json"):
        if (st / f).exists():
            shutil.copy(st / f, bk / f"{S.dates[-2] if len(S.dates) > 1 else 'prev'}_{f}")
    S.save(st / "state.npz", st / "state.json")
    keep = sorted(bk.glob("*_state.json"))[-5:]
    for p in bk.iterdir():
        if p.name.split("_")[0] not in {k.name.split("_")[0] for k in keep}:
            p.unlink()


def append_market(data: Path, rep, S):
    p = data / "history/market.csv"; p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(MARKET_COLS)
        w.writerow([rep["date"], rep["n_traded"], rep["ldn"], round(rep["breadth"], 6), rep["breadth_above"],
                    rep["breadth_valid"], round(rep["eff"], 6) if rep["eff"] == rep["eff"] else "",
                    round(rep["eff_ma3"], 6) if rep["eff_ma3"] == rep["eff_ma3"] else "", "|".join(S.prev_top10)])


def last_market_row(data: Path):
    p = data / "history/market.csv"
    if not p.exists():
        return None
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    return rows[-1] if rows else None


def write_snapshot(data: Path, date: str, bars: list):
    p = data / f"snapshots/{date}.csv.gz"; p.parent.mkdir(parents=True, exist_ok=True)
    cols = ["code", "name", "open", "high", "low", "close", "preclose", "vol", "amt", "fmv", "tmv", "src"]
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for b in sorted(bars, key=lambda x: x["code"]):
        w.writerow([b.get(c, "") if b.get(c) is not None else "" for c in cols])
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(buf.getvalue())


def strategy_alerts(S, rep, latest):
    al = []
    nm = S.names
    sig = latest["signals"]
    mains = [s for s in sig if s["kind"] == "主信号"]
    fills = [s for s in sig if s["kind"] == "补位"]
    if mains:
        al.append(checks.alert("yellow", "明日开盘·主信号", "；".join(f"{s['name']} {s['code']}（今日{s['pct']:+.1%}，市值{s['fmv']}亿）" for s in mains)))
    if fills:
        al.append(checks.alert("yellow", "明日开盘·补位信号（开盘时空仓才买）",
                               "；".join(f"{s['name']} {s['code']}（前日排名{s['prev_rank']}，今日{s['pct']:+.1%}）" for s in fills)))
    if rep["new_arrivals"]:
        al.append(checks.alert("yellow", "新进观察池（首次进20日涨幅前二）",
                               "；".join(f"{nm.get(c, '')} {c}" for c in rep["new_arrivals"])))
    for name, a in latest["accounts"].items():
        if a["today"]:
            al.append(checks.alert("yellow", f"回测账户（{name}）今日成交",
                                   "；".join(f"{e['action']} {e['name']} {e['shares']}股@{e['px']}" + (f"（{e['reason']}）" if e.get('reason') else "") for e in a["today"])))
    return al


def build_site(data: Path, site: Path):
    """把静态页面 + 数据拷到 site 输出目录。"""
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ROOT / "site", site)
    d = site / "data"; d.mkdir(exist_ok=True)
    S = load_state(data)
    shutil.copy(data / "latest.json", d / "latest.json")
    rows = list(csv.DictReader(open(data / "history/market.csv", encoding="utf-8")))
    jdump(dict(cols=["date", "ldn", "breadth", "eff_ma3", "n_traded"],
               rows=[[r["date"], int(r["ldn"]), float(r["breadth"]), float(r["eff_ma3"]) if r["eff_ma3"] else None,
                      int(r["n_traded"])] for r in rows]), d / "market.json")
    jdump(sitedata.make_accounts(S), d / "accounts.json")
    runs = []
    p = data / "history/runs.jsonl"
    if p.exists():
        runs = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()][-60:]
    jdump(runs[::-1], d / "runs.json")
    snaps = sorted((data / "snapshots").glob("*.csv.gz"))
    if snaps:
        last = snaps[-1]
        with gzip.open(last, "rt", encoding="utf-8") as f:
            text = f.read()
        (d / "snapshot.csv").write_text("﻿" + text, encoding="utf-8")   # 带 BOM，Excel 直接打开不乱码


def run_daily(a):
    data = Path(a.data); log: list[str] = []
    S = load_state(data)
    last = S.dates[-1]
    now = fetch.now_bj(); today = now.strftime("%Y-%m-%d")
    idx_date, idx_src = fetch.index_last_date(log)
    log.append(f"运行时间(北京) {now:%Y-%m-%d %H:%M}；指数最新日 {idx_date}（{idx_src}）；状态最新日 {last}")
    target = a.date or idx_date
    if target is None:
        raise fetch.FetchError("拿不到最新交易日（腾讯、东财指数都失败）\n" + "\n".join(log))
    if target <= last:
        print("\n".join(log)); print(f"SKIP 已是最新（{last}）")
        return dict(status="skip", date=last)
    if target == today and now.hour * 60 + now.minute < 15 * 60 + 5 and not a.date:
        print("\n".join(log)); print("SKIP 尚未收盘")
        return dict(status="skip", date=last)

    all_alerts, sources, stats_all = [], [], {}
    prev = last_market_row(data)
    prev_n = int(prev["n_traded"]) if prev else 0

    # ---- 1) 漏跑的交易日先回补（个股日K）
    cal = fetch.trade_calendar(last, log)
    missing = [d for d in cal if last < d < target]
    if missing:
        active = [c for c, ld in zip(S.codes, S.last_date) if ld and ld >= S.dates[max(0, len(S.dates) - 20)]]
        got = fetch.backfill(active, missing[0], missing[-1], log)
        for d in missing:
            bars = got.get(d, {})
            for b in bars.values():
                b["name"] = b["name"] or S.names.get(b["code"], "")
            clean, al, st = checks.validate_bars(bars, S, d, prev_n)
            if len(clean) < prev_n * 0.9:
                raise fetch.FetchError(f"回补 {d} 只拿到 {len(clean)} 只（上一日 {prev_n}），停止推进\n" + "\n".join(log))
            rep = S.feed(d, clean)
            append_market(data, rep, S); write_snapshot(data, d, clean)
            all_alerts += al + [checks.alert("yellow", f"已回补漏跑交易日 {d}",
                                             f"用个股日K重建（{len(clean)}只），非当日快照；流通市值按股本推算")]
            prev_n = rep["n_traded"]; stats_all[d] = st
            sources.append(f"{d}: 个股日K回补")

    # ---- 2) 当日快照
    sina = {}
    try:
        sina = fetch.sina_list(log)
    except Exception as e:  # noqa: BLE001
        log.append(f"新浪异常 {e!r}")
    recent = S.dates[max(0, len(S.dates) - 20)]
    universe = sorted({c for c, ld in zip(S.codes, S.last_date) if ld and ld >= recent} | set(sina))
    tx = fetch.tencent_quotes(universe, target, log)
    base, used = dict(tx), ["腾讯"]
    need = max(int(prev_n * 0.97), 2800)
    if len(base) < need and sina:
        add = {c: b for c, b in sina.items() if c not in base}
        base.update(add); used.append(f"新浪补{len(add)}只")
        all_alerts.append(checks.alert("red", "主数据源（腾讯）不完整，已用新浪补齐", f"腾讯 {len(tx)} 只，补 {len(add)} 只"))
    if len(base) < need:
        em = fetch.eastmoney_list(log)
        add = {c: b for c, b in em.items() if c not in base}
        base.update(add); used.append(f"东财补{len(add)}只")
        all_alerts.append(checks.alert("red", "腾讯+新浪仍不完整，已用东方财富补齐", f"补 {len(add)} 只"))
    if len(base) < need:
        raise fetch.FetchError(f"{target} 有效行情只有 {len(base)} 只（需要≥{need}），停止推进\n" + "\n".join(log))
    xc_al, xc = checks.cross_check(tx, sina, "腾讯", "新浪")
    all_alerts += xc_al
    for b in base.values():
        b["name"] = b["name"] or S.names.get(b["code"], "")
    clean, al, st = checks.validate_bars(base, S, target, prev_n)
    all_alerts += al
    st["cross_check"] = xc; stats_all[target] = st
    sources.append(f"{target}: " + "、".join(used))

    if a.dry_run:
        print("\n".join(log)); print(json.dumps(dict(stats=stats_all, alerts=all_alerts), ensure_ascii=False, indent=1))
        return dict(status="dry", date=target)

    rep = S.feed(target, clean)
    all_alerts += checks.market_alerts(rep) + checks.holding_alerts(S, target)
    quality = dict(sources=sources, stats=stats_all, log=log[-40:], index_src=idx_src)
    latest = sitedata.make_latest(S, rep, [], quality, now.strftime("%Y-%m-%d %H:%M"))
    all_alerts += strategy_alerts(S, rep, latest)
    latest["alerts"] = all_alerts

    save_state(S, data)
    append_market(data, rep, S)
    write_snapshot(data, target, clean)
    jdump(latest, data / "latest.json")
    with open(data / "history/runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(date=target, run_at=latest["generated_at"], sources=sources, stats=stats_all,
                                alerts=all_alerts), ensure_ascii=False) + "\n")
    print("\n".join(log))
    print(f"OK {target} 有效{len(clean)}只 跌停{rep['ldn']} 宽度{rep['breadth']:.1%} 信号{len(S.pending)} 告警{len(all_alerts)}")
    return dict(status="ok", date=target, alerts=all_alerts, latest=latest)


def write_notify(res, data: Path, out: Path, site_url: str):
    """需要通知时写 notify.json，由工作流在数据推送成功后发送。"""
    st_path = data / "notify_status.json"
    st = json.load(open(st_path, encoding="utf-8")) if st_path.exists() else {}
    if res["status"] == "ok":
        alerts = [x for x in res["alerts"] if x["level"] in ("red", "yellow")]
        latest = res["latest"]
    elif res["status"] == "skip" and st.get("date") == res["date"] and st.get("state") == "pending":
        latest = json.load(open(data / "latest.json", encoding="utf-8"))   # 上次没发出去 → 重试
        alerts = [x for x in latest["alerts"] if x["level"] in ("red", "yellow")]
    else:
        return
    if not alerts:
        jdump(dict(date=res["date"], state="none"), st_path); return
    subj, body = notify.compose(latest, alerts, site_url)
    jdump(dict(subject=subj, html=body, date=res["date"]), out / "notify.json")
    jdump(dict(date=res["date"], state="pending"), st_path)


def selftest(data: Path):
    """逐个检查各数据接口（用于排查），并用回补接口重抓状态里最近一天与快照比对。"""
    log: list[str] = []
    print("指数日期:", fetch.index_last_date(log))
    S = load_state(data); last = S.dates[-1]
    try:
        print("交易日历:", fetch.trade_calendar(S.dates[-3], log))
    except Exception as e:  # noqa: BLE001
        print("交易日历失败", e)
    codes = ["sh600000", "sz000001", "sz002242", "sh603200", "sz002487"]
    for fn in (fetch.eastmoney_kline, fetch.tencent_kline):
        for c in codes:
            try:
                rows = [r for r in fn(c, S.dates[-2], last) if r["date"] == last]
                snap = None
                p = data / f"snapshots/{last}.csv.gz"
                if p.exists():
                    with gzip.open(p, "rt", encoding="utf-8") as f:
                        for r in csv.DictReader(f):
                            if r["code"] == c:
                                snap = r
                print(fn.__name__, c, rows[0] if rows else "无", "| 快照收盘/前收", snap and (snap["close"], snap["preclose"]))
            except Exception as e:  # noqa: BLE001
                print(fn.__name__, c, "失败", repr(e))
    try:
        em = fetch.eastmoney_list(log, max_pages=2); print("东财列表(2页):", len(em))
    except Exception as e:  # noqa: BLE001
        print("东财列表失败", e)
    print("\n".join(log))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--site", required=True)
    ap.add_argument("--build", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date"); ap.add_argument("--out", default="out"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(Path(a.data)); return
    out = Path(a.out); out.mkdir(exist_ok=True)
    site_url = os.environ.get("SITE_URL", "")
    if a.build:
        build_site(Path(a.data), Path(a.site)); print("BUILD OK"); return
    try:
        res = run_daily(a)
    except Exception:
        tb = traceback.format_exc(); print(tb)
        (out / "failure.txt").write_text(tb, encoding="utf-8")
        sys.exit(1)
    if res["status"] in ("ok", "skip") and not a.dry_run:
        write_notify(res, Path(a.data), out, site_url)
        build_site(Path(a.data), Path(a.site))
    (out / "result.txt").write_text(res["status"], encoding="utf-8")


if __name__ == "__main__":
    main()
