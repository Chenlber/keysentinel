#!/usr/bin/env python3
"""从 history/notified.json 重建 history/valid_keys.json（一次性加密保存）。

场景：data/ 已清理（无法从 verified.jsonl 重新生成），
但 notified.json 保存了已通知的仓库与路径清单，足以重建汇总数据。

因为 encrypt() 幂等（enc:v1: 前缀保护），重复运行也不会多层加密。
"""
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import crypto_util as crypto

NOTIFIED = os.path.join(config.BASE_DIR, "history", "notified.json")
VALID = os.path.join(config.BASE_DIR, "history", "valid_keys.json")
FIELDS = ("repo", "path", "html_url")


def repo_hash(repo):
    return hashlib.sha256(repo.encode("utf-8")).hexdigest()[:8]


def main():
    with open(NOTIFIED, encoding="utf-8") as f:
        notified = json.load(f)

    items = []
    for r in notified.get("notified", []):
        # notified.json 中 repo/path 为加密存储，需先解密再算哈希/重建
        plain = crypto.decrypt_fields(dict(r), ("repo", "path"))
        repo, path = plain.get("repo", ""), plain.get("path", "")
        items.append({
            "repo": repo,
            "repo_hash": repo_hash(repo),
            "path": path,
            "key_masked": r.get("key_masked", "sk-***"),
            "html_url": f"https://github.com/{repo}/blob/main/{path}",
            "billing": "billing",
            "discovered_at": r.get("notified_at", datetime.now().strftime("%Y-%m-%d")),
        })

    items = [crypto.encrypt_fields(i, FIELDS) for i in items]  # 幂等加密
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "encrypted": crypto.ENABLED,
        "source": "rebuilt from history/notified.json",
        "total_valid": len(items),
        "items": items,
    }
    with open(VALID, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"已重建 {VALID}: {len(items)} 条 | 加密={crypto.ENABLED}")


if __name__ == "__main__":
    main()
