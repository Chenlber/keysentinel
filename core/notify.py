#!/usr/bin/env python3
"""Stage 8: 通知仓库维护者（邮件 + Issue 双通道）。

流程：
1. 读取 data/verified.jsonl 中 status=valid 的记录
2. 防重复：history/notified.json 已记录则跳过
3. 发送前实时复查：重新验证 key 仍有效
4. 邮件（含完整位置）+ 隐晦 Issue（仅提醒查邮箱）
5. 记录 notified.json

防重复：默认同一 key_hash 只通知一次；设 RENOTIFY_DAYS=90 可 90 天后重发。
敏感字段加密：notified.json 会 commit 回仓库，repo/path 用 AES-256-GCM 加密，
密钥取自 KEYSTORE_PASSPHRASE（存 GitHub Secrets）。

用法:
  python3 -m core.notify                 # dry-run 预览
  python3 -m core.notify --send          # 实际发送
"""
import json
import os
import sys
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import crypto_util as crypto
from core import issuer, mailer
from core import verifier

VERIFIED_FILE = os.path.join(config.DATA_DIR, "verified.jsonl")
SENSITIVE_FIELDS = ("repo", "path")
HISTORY_DIR = os.path.join(config.BASE_DIR, "history")
NOTIFIED_JSON = os.path.join(HISTORY_DIR, "notified.json")
RENOTIFY_DAYS = int(os.environ.get("RENOTIFY_DAYS", "0") or 0)
# 跳过的仓库（逗号分隔，如 SKIP_REPOS="a/b,c/d"）
SKIP_REPOS = {r.strip() for r in os.environ.get("SKIP_REPOS", "").split(",") if r.strip()}


def load_notified():
    """返回 {key_hash: {...}}，自动解密敏感字段。

    容错：损坏/缺字段的记录逐条跳过，绝不因单条异常返回空字典
    （否则会导致已通知的仓库被重复骚扰）。
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
            # 解密失败：保留原记录，确保 key_hash 仍可用于去重
            print(f"警告：记录 {h} 解密失败，保留密文用于去重（{e}）", file=sys.stderr)
            result[h] = r
    return result


def save_notified(notified):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    records = [crypto.encrypt_fields(dict(r), SENSITIVE_FIELDS) for r in notified.values()]
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "encrypted": crypto.ENABLED,
        "notified": records,
    }
    with open(NOTIFIED_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def should_notify(rec, notified):
    """判断是否需要通知。返回 (是否通知, 原因)。"""
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


def recheck_key(rec, session=None):
    """发送前实时复查：重新验证 key 是否有效。返回 (是否有效, 详情)。"""
    session = session or requests.Session()
    candidates = verifier.build_candidates(
        rec["key"], rec.get("base_url"), rec.get("proxy_base_url")
    )
    if candidates is None:
        return False, "proxy"
    reason = ""
    for service in candidates:
        status, r = verifier.verify_one(session, rec["key"], service)
        if status == "valid":
            return True, f"{service}: {r}"
        reason = r
    return False, reason or "invalid"


def main(send=None):
    """send=None 时从命令行 --send 判断；main.py 可直接传 send=True/False。"""
    dry_run = (not send) if send is not None else ("--send" not in sys.argv)
    if not os.path.exists(VERIFIED_FILE):
        print(f"缺少 {VERIFIED_FILE}，请先运行 verifier。", file=sys.stderr)
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
    sent_mail = sent_issue = skipped = 0

    for rec in valid_recs:
        need, reason = should_notify(rec, notified)
        if not need:
            print(f"=== {rec['repo']} ===")
            print(f"  [SKIP] {reason}，避免重复骚扰\n")
            skipped += 1
            continue
        to_addr = mailer.resolve_recipient(session, rec["repo"])
        print(f"=== {rec['repo']} ===")
        print(f"  收件人: {to_addr or '(未找到邮箱，跳过)'}")
        print(f"  文件:   {rec['path']}")
        if not to_addr:
            print()
            continue

        msg = mailer.build_message(rec, to_addr)
        issue_title, issue_body = issuer.build_issue(rec)
        ok, detail = recheck_key(rec)
        print(f"  复查: {'✓ 仍有效 ' + detail if ok else '✗ 已失效/不可用 ' + detail}")
        if not ok:
            print("  [SKIP] 复查未通过，跳过该条，避免误通知\n")
            continue

        if dry_run:
            print("  [DRY-RUN] 邮件预览：")
            print("  " + str(msg["Subject"]))
            print("  To:", msg["To"])
            print("  [DRY-RUN] Issue 预览：")
            print("  标题: " + issue_title)
            print("  正文: " + issue_body.replace("\n", " | "))
        else:
            mail_ok = mailer.send_mail(msg)
            issue_ok = issuer.create_issue(rec["repo"], issue_title, issue_body)
            if mail_ok:
                print(f"  [SENT] 邮件已发送 → {to_addr}")
                sent_mail += 1
            if issue_ok:
                print(f"  [SENT] Issue 已创建 → {rec['repo']}")
                sent_issue += 1
            if mail_ok or issue_ok:
                notified[rec["key_hash"]] = {
                    "key_hash": rec["key_hash"],
                    "repo": rec["repo"],
                    "path": rec["path"],
                    "key_masked": mailer.mask(rec["key"]),
                    "notified_at": datetime.now().strftime("%Y-%m-%d"),
                    "channels": ("mail+issue" if mail_ok and issue_ok
                                 else ("mail" if mail_ok else "issue")),
                }
        print()

    if not dry_run and (sent_mail or sent_issue):
        save_notified(notified)
        print(f"已更新通知记录（history/notified.json，共 {len(notified)} 条）")
    result = 'dry-run（未实际发送）' if dry_run else f'邮件 {sent_mail} 封, Issue {sent_issue} 个'
    print(f"完成：{result}，跳过重复 {skipped} 条")


if __name__ == "__main__":
    main()
