#!/usr/bin/env python3
"""Stage 5: 通过 SMTP 邮件 + GitHub Issue 通知泄露 key 的仓库维护者。

流程：
1. 从 data/verified.jsonl 取 status=valid 的记录
2. 用 GitHub API 提取维护者邮箱（commits author email，fallback 用户公开邮箱）
3. 构建英文邮件（脱敏 key，绝不包含完整密钥）
4. 构建隐晦 Issue（不暴露 key/文件/验证信息，仅提醒维护者查邮箱）
5. dry-run 双预览；--send 才真正发邮件 + 创建 Issue

SMTP 配置全部走环境变量，不落盘：
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
可选覆盖收件人：MAIL_TO（默认自动从 GitHub 提取）
"""
import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.header import Header

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import verifier

VERIFIED_FILE = os.path.join(config.DATA_DIR, "verified.jsonl")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
MAIL_TO_OVERRIDE = os.environ.get("MAIL_TO", "")


def mask(key):
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:6] + "***" + key[-4:]


def recheck_key(rec, session=None):
    """发送前实时复查：重新验证 key 是否仍有效，防止已撤销仍误通知。"""
    session = session or requests.Session()
    candidates = verifier.build_candidates(
        rec["key"], rec.get("base_url"), rec.get("proxy_base_url")
    )
    if candidates is None:
        return False, "proxy"
    for service in candidates:
        status, reason = verifier.verify_one(session, rec["key"], service)
        if status == "valid":
            return True, f"{service}: {reason}"
    return False, reason or "invalid"


def build_issue(rec):
    """构建隐晦 Issue：不暴露 key、文件路径、验证信息，仅提醒维护者查邮箱。"""
    owner = rec["repo"].split("/")[0]
    title = "[Security] Please check your inbox"
    body = f"""Hi @{owner},

We've identified a potential security concern related to this repository.

Details have been sent to your email address. Please check your inbox and take appropriate action.

— KeySentinel (open source, https://github.com/Chenlber/keysentinel)"""
    return title, body


def create_issue(repo, title, body):
    """通过 GitHub API 创建 Issue（公开动作）。"""
    if not config.GITHUB_TOKEN:
        print("缺少 GITHUB_TOKEN，无法创建 Issue。", file=sys.stderr)
        return False
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"title": title, "body": body}
    r = requests.post(
        f"{config.GITHUB_API}/repos/{repo}/issues",
        json=payload, headers=headers, timeout=30,
    )
    if r.status_code in (200, 201):
        return True
    print(f"创建 Issue 失败 [{r.status_code}]: {r.text[:200]}", file=sys.stderr)
    return False


def get_maintainer_email(session, repo):
    """提取维护者邮箱：commits author email 优先，fallback 用户公开邮箱。"""
    headers = {}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    try:
        r = session.get(f"{config.GITHUB_API}/repos/{repo}/commits?per_page=5", headers=headers, timeout=15)
        if r.status_code == 200:
            emails = {c["commit"]["author"].get("email") for c in r.json()}
            emails = {e for e in emails if e and "noreply" not in e}
            if emails:
                return sorted(emails)[0]
    except Exception:
        pass
    # fallback: 用户公开邮箱
    owner = repo.split("/")[0]
    try:
        r = session.get(f"{config.GITHUB_API}/users/{owner}", headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json().get("email") or ""
    except Exception:
        pass
    return ""


def build_message(rec, to_addr):
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
        print(f"发送失败: {e}", file=sys.stderr)
        return False


def main():
    dry_run = "--send" not in sys.argv
    if not os.path.exists(VERIFIED_FILE):
        print("缺少 data/verified.jsonl，请先运行 verifier.py。", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    valid_recs = []
    with open(VERIFIED_FILE, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == "valid":
                valid_recs.append(rec)

    if not valid_recs:
        print("没有 valid 的 key，无需通知。")
        return

    print(f"待通知 {len(valid_recs)} 条 valid key\n")
    sent_mail = sent_issue = 0
    for rec in valid_recs:
        to_addr = MAIL_TO_OVERRIDE or get_maintainer_email(session, rec["repo"])
        print(f"=== {rec['repo']} ===")
        print(f"  收件人: {to_addr or '(未找到邮箱，跳过)'}")
        print(f"  文件:   {rec['path']}")
        if not to_addr:
            continue
        msg = build_message(rec, to_addr)
        issue_title, issue_body = build_issue(rec)
        # 发送前实时复查（仅 --send 时真实请求；dry-run 也复查以展示）
        ok, detail = recheck_key(rec)
        print(f"  复查: {'✓ 仍有效 ' + detail if ok else '✗ 已失效/不可用 ' + detail}")
        if not ok:
            print("  [SKIP] 复查未通过，跳过该条，避免误通知")
            print()
            continue
        if dry_run:
            print("  [DRY-RUN] 邮件预览：")
            print("  " + str(msg["Subject"]))
            print("  To:", msg["To"])
            print("  [DRY-RUN] Issue 预览：")
            print("  标题: " + issue_title)
            print("  正文: " + issue_body.replace("\n", " | "))
        else:
            if send_mail(msg):
                print(f"  [SENT] 邮件已发送 → {to_addr}")
                sent_mail += 1
            if create_issue(rec["repo"], issue_title, issue_body):
                print(f"  [SENT] Issue 已创建 → {rec['repo']}")
                sent_issue += 1
        print()
    print(f"完成：{'dry-run（未实际发送）' if dry_run else f'邮件 {sent_mail} 封, Issue {sent_issue} 个'}")


if __name__ == "__main__":
    main()
