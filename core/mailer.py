#!/usr/bin/env python3
"""邮件通知：维护者邮箱提取、邮件构建、SMTP 发送。

SMTP 配置全部走环境变量，不落盘：
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
可选覆盖收件人：MAIL_TO（全局）、MAIL_TO_REPOS（按仓库，格式 "repo/a=e1,repo/b=e2"）
"""
import os
import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
MAIL_TO_OVERRIDE = os.environ.get("MAIL_TO", "")

# 按仓库指定收件人（格式 "repo/a=email1,repo/b=email2"）
MAIL_TO_REPOS = {}
for _kv in os.environ.get("MAIL_TO_REPOS", "").split(","):
    if "=" in _kv:
        _repo, _email = _kv.split("=", 1)
        MAIL_TO_REPOS[_repo.strip()] = _email.strip()


def mask(key):
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:6] + "***" + key[-4:]


def get_maintainer_email(session, repo):
    """提取维护者邮箱：commits author email 优先，fallback 用户公开邮箱。"""
    import requests  # 局部导入，避免未配置 SMTP 时仍依赖网络库

    headers = {}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    try:
        r = session.get(
            f"{config.GITHUB_API}/repos/{repo}/commits?per_page=5",
            headers=headers, timeout=15,
        )
        if r.status_code == 200:
            emails = {c["commit"]["author"].get("email") for c in r.json()}
            emails = {e for e in emails if e and "noreply" not in e}
            if emails:
                return sorted(emails)[0]
    except Exception:
        pass
    owner = repo.split("/")[0]
    try:
        r = session.get(f"{config.GITHUB_API}/users/{owner}", headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json().get("email") or ""
    except Exception:
        pass
    return ""


def resolve_recipient(session, repo):
    """收件人优先级：按仓库指定 > 全局覆盖 > 自动提取。"""
    return (
        MAIL_TO_REPOS.get(repo)
        or MAIL_TO_OVERRIDE
        or get_maintainer_email(session, repo)
    )


def build_message(rec, to_addr):
    """构建通知邮件（含完整位置信息，脱敏 key）。"""
    key_masked = mask(rec["key"])
    subject = f"[Security] API Key exposed in {rec['repo']}"
    body = f"""Hi {rec['repo'].split('/')[0]} maintainer,

I found a hardcoded API key in your public repository while running an automated leak scan, and wanted to alert you privately.

Repository: {rec['repo']}
File: {rec['path']}
Key (masked): {key_masked}
Verification: confirmed active, can access models

Recommendation:
1. Revoke this key immediately in the provider console
2. Move credentials to environment variables or a secrets manager
3. Check git history for other leaked credentials

This message was generated automatically by KeySentinel (https://github.com/Chenlber/keysentinel).
If this is a false positive or you need details, reply to this email.

If you find this alert helpful, it would mean a lot to us if you could star the KeySentinel project on GitHub — it helps more developers discover this kind of free, responsible leak detection.

Best regards,
KeySentinel"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    return msg


def send_mail(msg):
    """SMTP 发送。返回是否成功。"""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("缺少 SMTP 配置（SMTP_HOST/SMTP_USER/SMTP_PASS），无法发送。", file=sys.stderr)
        return False
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}", file=sys.stderr)
        return False
