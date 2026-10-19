"""异常/信号通知：QQ 邮箱 SMTP（有 Secrets 时）；否则在仓库开一个 Issue（GitHub 会发邮件给你）。
不打印、不记录任何凭据。"""
from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from email.message import EmailMessage

LV = {"red": "🔴 数据异常", "yellow": "🟡 提醒", "info": "ℹ️ 记录"}


def compose(latest: dict | None, alerts: list, site_url: str, failure: str | None = None):
    reds = [a for a in alerts if a["level"] == "red"]
    yel = [a for a in alerts if a["level"] == "yellow"]
    date = latest["date"] if latest else ""
    if failure:
        subj = f"【股票看板】{date} 自动更新失败"
    else:
        bits = []
        if reds:
            bits.append(f"数据异常{len(reds)}")
        if yel:
            bits.append(f"提醒{len(yel)}")
        subj = f"【股票看板】{date} " + " · ".join(bits)
    lines = [f"<h3>{html.escape(subj)}</h3>"]
    if failure:
        lines.append(f"<p>本次没有更新数据，状态没有推进，之后的定时任务会自动重试。</p><pre>{html.escape(failure[-3000:])}</pre>")
    for a in reds + yel:
        lines.append(f"<p><b>{LV[a['level']]}｜{html.escape(a['title'])}</b><br>{html.escape(a.get('detail', ''))}</p>")
    if latest and not failure:
        m = latest["market"]
        lines.append(f"<p>跌停家数 {m['ldn']}；市场宽度 {m['breadth']:.1%}（{m['breadth_above']}/{m['breadth_valid']}）</p>")
    if site_url:
        lines.append(f'<p><a href="{html.escape(site_url)}">打开看板</a></p>')
    body = "\n".join(lines)
    return subj, body


def send(subj: str, body_html: str) -> str:
    user, code = os.environ.get("QQ_EMAIL"), os.environ.get("QQ_SMTP_AUTH_CODE")
    to = os.environ.get("EMAIL_TO") or user
    if user and code:
        msg = EmailMessage()
        msg["Subject"] = subj; msg["From"] = user; msg["To"] = to
        msg.set_content("请用支持 HTML 的邮箱查看。")
        msg.add_alternative(body_html, subtype="html")
        with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, code)
            s.send_message(msg)
        return "smtp_accepted"
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if tok and repo:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST",
                                     data=json.dumps({"title": subj, "body": body_html}).encode(),
                                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return "issue_created"
    return "no_channel"


if __name__ == "__main__":
    # 用法: python -m engine.notify <notify.json>   或   python -m engine.notify --failure <logfile>
    site = os.environ.get("SITE_URL", "")
    if sys.argv[1] == "--failure":
        log = open(sys.argv[2], encoding="utf-8", errors="replace").read() if os.path.exists(sys.argv[2]) else "(无日志)"
        s, b = compose(None, [], site, failure=log)
    else:
        js = json.load(open(sys.argv[1], encoding="utf-8"))
        s, b = js["subject"], js["html"]
    print("NOTIFY", send(s, b))
