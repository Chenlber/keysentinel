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
可选覆盖收件人：MAIL_TO（全局）、MAIL_TO_REPOS（按仓库）
防重复：history/notified.json 记录已通知 key，默认同一 key 只通知一次；
  设置 RENOTIFY_DAYS=90 可在 90 天后重发（仍未修复时提醒）。

敏感字段加密：notified.json 会 commit 回仓库，其中 repo/path 用 AES-256-GCM 加密，
密钥取自环境变量 KEYSTORE_PASSPHRASE（存 GitHub Secrets）。未设置该变量时明文存储。
"""
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import crypto_util as crypto
import verifier

# 需要加密存储的字段（定位信息，公开仓库中不应明文）
SENSITIVE_FIELDS = ("repo", "path")

VERIFIED_FILE = os.path.join(config.DATA_DIR, "verified.jsonl")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
MAIL_TO_OVERRIDE = os.environ.get("MAIL_TO", "")
# 按仓库指定收件人（格式 "repo/a=email1,repo/b=email2"，逗号分隔）
MAIL_TO_REPOS = {}
for _kv in os.environ.get("MAIL_TO_REPOS", "").split(","):
    if "=" in _kv:
        _repo, _email = _kv.split("=", 1)
        MAIL_TO_REPOS[_repo.strip()] = _email.strip()
# 跳过的仓库（逗号分隔，如 SKIP_REPOS="a/b,c/d"）
SKIP_REPOS = {r.strip() for r in os.environ.get("SKIP_REPOS", "").split(",") if r.strip()}

# 已通知记录：防止重复骚扰同一仓库/key
HISTORY_DIR = os.path.join(config.BASE_DIR, "history")
NOTIFIED_JSON = os.path.join(HISTORY_DIR, "notified.json")
# 同一 key 默认只在首次通知；间隔天数内不重发
RENOTIFY_DAYS = int(os.environ.get("RENOTIFY_DAYS", "0") or 0)


def load_notified():
    """返回 {key_hash: {repo, notified_at, status}}，自动解密敏感字段。

    容错：损坏/缺字段的记录逐条跳过，不影响整体历史。
    绝不因单条异常返回空字典（否则会导致重复通知）。
    """
    if not os.path.exists(NOTIFIED_JSON):
        return {}
    try:
        with open(NOTIFIED_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"警告：读取 {NOTIFIED_JSON} 失败（{e}），视为无历史", file=sys.stderr)
        return {}
    result = {}
    for r in data.get("notified", []):
        h = r.get("key_hash")
        if not h:
            print("警告：跳过缺 key_hash 的通知记录", file=sys.stderr)
            continue
        try:
            result[h] = crypto.decrypt_fields(dict(r), SENSITIVE_FIELDS)
        except Exception as e:
            # 解密失败：保留原记录（密文），确保 key_hash 仍可用于去重
            print(f"警告：记录 {h} 解密失败，保留密文用于去重（{e}）", file=sys.stderr)
            result[h] = r
    return result


def save_notified(notified):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    records = []
    for r in notified.values():
        records.append(crypto.encrypt_fields(dict(r), SENSITIVE_FIELDS))
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "encrypted": crypto.ENABLED,
        "notified": records,
    }
    with open(NOTIFIED_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def should_notify(rec, notified):
    """判断是否需要通知（默认同一 key_hash 只通知一次）。"""
    h = rec["key_hash"]
    if h not in notified:
        return True, "首次"
    if RENOTIFY_DAYS <= 0:
        return False, "已通知过"
    prev = notified[h].get("notified_at", "")
    try:
        days = (datetime.now() - datetime.strptime(prev, "%Y-%m-%d")).days
    except Exception:
        return True, "记录异常，重发"
    if days >= RENOTIFY_DAYS:
        return True, f"距上次 {days} 天"
    return False, f"{RENOTIFY_DAYS - days} 天内已通知"


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


def main(send=None):
    """send=None 时从命令行 --send 判断；main.py 可直接传 send=True/False。"""
    dry_run = (not send) if send is not None else ("--send" not in sys.argv)
    if not os.path.exists(VERIFIED_FILE):
        print("缺少 data/verified.jsonl，请先运行 verifier.py。", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    notified = load_notified()
    valid_recs = []
    with open(VERIFIED_FILE, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == "valid" and rec.get("repo") not in SKIP_REPOS:
                valid_recs.append(rec)

    if not valid_recs:
        print("没有 valid 的 key，无需通知。")
        return

    print(f"发现 {len(valid_recs)} 条 valid key")
    print(f"已通知记录 {len(notified)} 条（history/notified.json）\n")
    sent_mail = sent_issue = 0
    skipped = 0
    for rec in valid_recs:
        need, reason = should_notify(rec, notified)
        if not need:
            print(f"=== {rec['repo']} ===")
            print(f"  [SKIP] {reason}，避免重复骚扰")
            print()
            skipped += 1
            continue
        to_addr = (
            MAIL_TO_REPOS.get(rec["repo"])
            or MAIL_TO_OVERRIDE
            or get_maintainer_email(session, rec["repo"])
        )
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
            mail_ok = send_mail(msg)
            issue_ok = create_issue(rec["repo"], issue_title, issue_body)
            if mail_ok:
                print(f"  [SENT] 邮件已发送 → {to_addr}")
                sent_mail += 1
            if issue_ok:
                print(f"  [SENT] Issue 已创建 → {rec['repo']}")
                sent_issue += 1
            # 任一渠道成功则记录，避免下次重复
            if mail_ok or issue_ok:
                notified[rec["key_hash"]] = {
                    "key_hash": rec["key_hash"],
                    "repo": rec["repo"],
                    "path": rec["path"],
                    "key_masked": mask(rec["key"]),
                    "notified_at": datetime.now().strftime("%Y-%m-%d"),
                    "channels": ("mail+issue" if mail_ok and issue_ok
                                 else ("mail" if mail_ok else "issue")),
                }
        print()
    if not dry_run and (sent_mail or sent_issue):
        save_notified(notified)
        print(f"已更新通知记录（history/notified.json，共 {len(notified)} 条）")
    print(f"完成：{'dry-run（未实际发送）' if dry_run else f'邮件 {sent_mail} 封, Issue {sent_issue} 个'}，跳过重复 {skipped} 条")


if __name__ == "__main__":
    main()
