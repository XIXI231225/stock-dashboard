"""行情抓取：多数据源 + 重试 + 统一字段。

统一后的每只股票一行（dict）：
  code(sh600000) name open high low close preclose vol(股) amt(元) fmv(元) tmv(元) src

数据源（按优先级）：
  1. 腾讯 qt.gtimg.cn  —— 按代码批量取，每只股票自带行情时间，可逐只核对交易日
  2. 新浪 Market_Center —— 全市场分页列表，同时用于发现新股
  3. 东方财富 clist      —— 全市场分页
缺口回补（漏跑的历史交易日）：东方财富个股日K（有涨跌额→前收盘），腾讯日K备用。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .core import MAIN_PREFIX

BJ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class FetchError(RuntimeError):
    pass


def now_bj() -> datetime:
    return datetime.now(BJ)


def http_get(url, encoding="utf-8", referer=None, retries=3, timeout=15, log=None):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(encoding, errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.8 * (k + 1))
    if log is not None:
        log.append(f"GET失败 {url[:90]} : {last!r}")
    raise FetchError(f"{url[:90]} : {last!r}")


def _f(x):
    try:
        if x in (None, "", "-"):
            return None
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def is_main(code: str) -> bool:
    return code[:5] in MAIN_PREFIX


def to_prefixed(c6: str) -> str:
    return ("sh" if c6.startswith("6") else "sz") + c6


# ------------------------------------------------------------------ 交易日
def index_last_date(log) -> tuple[str | None, str]:
    """返回 (上证指数最新行情日期, 来源)。"""
    try:
        t = http_get("https://qt.gtimg.cn/q=sh000001", "gb18030", "https://stockapp.finance.qq.com/", log=log)
        m = re.search(r'="(.*?)";', t, re.S)
        f = m.group(1).split("~")
        ts = f[30]
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", "腾讯"
    except Exception as e:  # noqa: BLE001
        log.append(f"腾讯指数日期失败: {e!r}")
    try:
        t = http_get("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=3"
                     "&fields1=f1&fields2=f51", log=log)
        k = json.loads(t)["data"]["klines"]
        return k[-1][:10], "东方财富"
    except Exception as e:  # noqa: BLE001
        log.append(f"东财指数日期失败: {e!r}")
    return None, "无"


def trade_calendar(since: str, log) -> list[str]:
    """上证指数日K的交易日列表（since 之后，含当天）。用于发现漏跑的交易日。"""
    beg = since.replace("-", "")
    for url, parse in [
        (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=0&beg={beg}&end=20500101"
         "&fields1=f1&fields2=f51",
         lambda t: [k[:10] for k in json.loads(t)["data"]["klines"]]),
        (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,{since},,60,",
         lambda t: [k[0] for k in json.loads(t)["data"]["sh000001"]["day"]]),
    ]:
        try:
            ds = parse(http_get(url, log=log))
            return sorted(d for d in ds if d >= since)
        except Exception as e:  # noqa: BLE001
            log.append(f"交易日历失败: {e!r}")
    raise FetchError("拿不到交易日历")


# ------------------------------------------------------------------ 源1：腾讯
def tencent_quotes(codes: list[str], date: str, log) -> dict:
    out = {}
    stale = 0
    chunks = [codes[i:i + 60] for i in range(0, len(codes), 60)]

    def one(ch):
        return http_get("https://qt.gtimg.cn/q=" + ",".join(ch), "gb18030", "https://stockapp.finance.qq.com/",
                        retries=4)

    with ThreadPoolExecutor(6) as ex:
        texts = list(ex.map(lambda ch: _safe(one, ch, log), chunks))
    for t in texts:
        if not t:
            continue
        for m in re.finditer(r'v_([a-z]{2}\d{6})="(.*?)";', t, re.S):
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 46 or not is_main(code):
                continue
            ts = f[30]
            d = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ""
            close, vol = _f(f[3]), _f(f[36])
            if d != date:
                stale += 1          # 停牌或行情未更新 → 当天不算交易
                continue
            if not close or not vol:
                continue
            fsh = _f(f[72]) if len(f) > 72 else None
            tsh = _f(f[73]) if len(f) > 73 else None
            fmv = fsh * close if fsh else ((_f(f[44]) or 0) * 1e8 or None)
            tmv = tsh * close if tsh else ((_f(f[45]) or 0) * 1e8 or None)
            out[code] = dict(code=code, name=f[1].strip(), open=_f(f[5]), high=_f(f[33]), low=_f(f[34]), close=close,
                             preclose=_f(f[4]), vol=vol * 100, amt=(_f(f[37]) or 0) * 1e4, fmv=fmv, tmv=tmv, src="腾讯")
    log.append(f"腾讯: 请求{len(chunks)}批, 当日有成交{len(out)}只, 非当日/停牌{stale}只")
    return out


def _safe(fn, arg, log):
    try:
        return fn(arg)
    except Exception as e:  # noqa: BLE001
        log.append(f"批次失败: {e!r}")
        return None


# ------------------------------------------------------------------ 源2：新浪
def sina_list(log, max_pages=80) -> dict:
    out = {}
    pages = 0
    for page in range(1, max_pages + 1):
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?"
               + urllib.parse.urlencode(dict(page=page, num=100, sort="symbol", asc=1, node="hs_a", symbol="",
                                             _s_r_a="page")))
        try:
            rows = json.loads(http_get(url, "gb18030", "https://vip.stock.finance.sina.com.cn/", retries=4))
        except Exception as e:  # noqa: BLE001
            log.append(f"新浪第{page}页失败: {e!r}")
            break
        pages += 1
        if not rows:
            break
        for r in rows:
            code = r.get("symbol", "")
            if not is_main(code):
                continue
            close, vol = _f(r.get("trade")), _f(r.get("volume"))
            if not close or not vol:
                continue
            nmc, mk = _f(r.get("nmc")), _f(r.get("mktcap"))
            out[code] = dict(code=code, name=r.get("name", "").strip(), open=_f(r.get("open")), high=_f(r.get("high")),
                             low=_f(r.get("low")), close=close, preclose=_f(r.get("settlement")), vol=vol,
                             amt=_f(r.get("amount")), fmv=nmc * 1e4 if nmc else None, tmv=mk * 1e4 if mk else None,
                             src="新浪")
        if len(rows) < 100:
            break
        time.sleep(0.15)
    log.append(f"新浪: {pages}页, 主板有成交{len(out)}只")
    return out


# ------------------------------------------------------------------ 源3：东方财富
EM_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com", "82.push2.eastmoney.com"]


def eastmoney_list(log, max_pages=60) -> dict:
    out = {}
    fs = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80"
    fields = "f2,f5,f6,f12,f13,f14,f15,f16,f17,f18,f20,f21"
    total = None
    for page in range(1, max_pages + 1):
        got = None
        for host in EM_HOSTS:
            url = (f"https://{host}/api/qt/clist/get?pn={page}&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs="
                   + urllib.parse.quote(fs) + "&fields=" + fields)
            try:
                js = json.loads(http_get(url, retries=2))
                data = js.get("data") or {}
                diff = data.get("diff") or []
                if isinstance(diff, dict):
                    diff = list(diff.values())
                total = data.get("total", total)
                got = diff
                break
            except Exception as e:  # noqa: BLE001
                log.append(f"东财{host}第{page}页失败: {e!r}")
        if got is None or not got:
            break
        for r in got:
            c6 = str(r.get("f12", ""))
            code = to_prefixed(c6)
            if not is_main(code):
                continue
            close, vol = _f(r.get("f2")), _f(r.get("f5"))
            if not close or not vol:
                continue
            out[code] = dict(code=code, name=str(r.get("f14", "")).strip(), open=_f(r.get("f17")), high=_f(r.get("f15")),
                             low=_f(r.get("f16")), close=close, preclose=_f(r.get("f18")), vol=vol * 100,
                             amt=_f(r.get("f6")), fmv=_f(r.get("f21")), tmv=_f(r.get("f20")), src="东方财富")
        if total and page * 100 >= total:
            break
        time.sleep(0.15)
    log.append(f"东财: total={total}, 主板有成交{len(out)}只")
    return out


# ------------------------------------------------------------------ 缺口回补（个股日K）
def eastmoney_kline(code: str, beg: str, end: str):
    secid = ("1." if code.startswith("sh") else "0.") + code[2:]
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=0"
           f"&beg={beg.replace('-', '')}&end={end.replace('-', '')}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f60")
    js = json.loads(http_get(url, retries=3))
    d = js.get("data") or {}
    name = d.get("name", "")
    rows = []
    for k in d.get("klines") or []:
        x = k.split(",")
        close, chg = float(x[2]), float(x[7])
        rows.append(dict(date=x[0], code=code, name=name, open=float(x[1]), close=close, high=float(x[3]),
                         low=float(x[4]), vol=float(x[5]) * 100, amt=float(x[6]), preclose=round(close - chg, 3),
                         fmv=None, tmv=None, src="东财日K"))
    return rows


def tencent_kline(code: str, beg: str, end: str):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{beg},{end},40,"
    js = json.loads(http_get(url, retries=3))
    node = (js.get("data") or {}).get(code) or {}
    bars = node.get("day") or node.get("qfqday") or []
    rows = []
    prev = None
    for b in bars:
        d, o, c, h, l, v = b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
        rows.append(dict(date=d, code=code, name="", open=o, close=c, high=h, low=l, vol=v * 100, amt=None,
                         preclose=prev, fmv=None, tmv=None, src="腾讯日K(前收=上一日收盘)"))
        prev = c
    return [r for r in rows if r["preclose"] is not None]


def backfill(codes: list[str], beg: str, end: str, log) -> dict:
    """返回 {date: {code: bar}}，beg..end 含两端。"""
    res: dict[str, dict] = {}
    fails = []

    def one(code):
        for fn in (eastmoney_kline, tencent_kline):
            try:
                return fn(code, beg, end)
            except Exception:  # noqa: BLE001
                continue
        fails.append(code)
        return []

    with ThreadPoolExecutor(8) as ex:
        for rows in ex.map(one, codes):
            for r in rows:
                if beg <= r["date"] <= end and r["vol"] and r["close"]:
                    res.setdefault(r["date"], {})[r["code"]] = r
    log.append(f"回补 {beg}~{end}: {len(codes)}只请求, 失败{len(fails)}只, 得到日期{sorted(res)}")
    return res
