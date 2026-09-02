#!/usr/bin/env python3
"""生成 history/valid_keys.json —— 历史发现的脱敏汇总数据源。

读取 data/verified.jsonl（valid）+ data/billing.jsonl（欠费状态），
输出脱敏 JSON 供 README 动态展示与后续分析。

敏感字段（repo/path/html_url）用 AES-256-GCM 加密，密钥取自环境变量
KEYSTORE_PASSPHRASE（存 GitHub Secrets）。该 JSON 会 commit 回公开仓库，
加密后无人能从仓库数据反推泄露点。未设置口令时明文存储。
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import crypto_util as crypto

# 需要加密的字段（泄露点定位信息）
SENSITIVE_FIELDS = ("repo", "path", "html_url")

HISTORY_DIR = os.path.join(config.BASE_DIR, "history")
VALID_KEYS_JSON = os.path.join(HISTORY_DIR, "valid_keys.json")


def mask(key):
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:6] + "***" + key[-4:]


def repo_hash(repo):
    """仓库名 sha256 前 8 位：公开展示用，无法反推仓库名。"""
    import hashlib
    return hashlib.sha256(repo.encode("utf-8")).hexdigest()[:8]


def main():
    if not os.path.exists(os.path.join(config.DATA_DIR, "verified.jsonl")):
        print("缺少 data/verified.jsonl，请先运行 verifier.py。", file=sys.stderr)
        sys.exit(1)

    # 读 billing 状态（key_hash -> billing status）
    billing = {}
    if os.path.exists(os.path.join(config.DATA_DIR, "billing.jsonl")):
        with open(os.path.join(config.DATA_DIR, "billing.jsonl"), encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                billing[r["key_hash"]] = r.get("status", "")

    # 读 valid key（按 repo+path 去重：同一 key 可能命中多个文件）
    valid = []
    seen = set()
    with open(os.path.join(config.DATA_DIR, "verified.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") != "valid":
                continue
            h = r["key_hash"]
            dedup_key = (r.get("repo", ""), r.get("path", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            valid.append({
                "repo": r["repo"],
                "repo_hash": repo_hash(r["repo"]),
                "path": r["path"],
                "key_masked": mask(r["key"]),
                "html_url": r.get("html_url", ""),
                "billing": billing.get(h, ""),
                "discovered_at": datetime.now().strftime("%Y-%m-%d"),
            })

    # 加密敏感字段后再落盘（该文件会 commit 回公开仓库）
    encrypted_items = [crypto.encrypt_fields(dict(v), SENSITIVE_FIELDS) for v in valid]

    os.makedirs(HISTORY_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "encrypted": crypto.ENABLED,
        "total_valid": len(valid),
        "items": encrypted_items,
    }
    with open(VALID_KEYS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"已生成 {VALID_KEYS_JSON}")
    print(f"  加密存储: {'是 (AES-256-GCM)' if crypto.ENABLED else '否（未设置 KEYSTORE_PASSPHRASE）'}")
    print(f"  valid key 总数: {len(valid)}")
    for v in valid:
        print(f"  - {v['repo_hash']} | {v['key_masked']} | billing={v['billing'] or '-'}")


if __name__ == "__main__":
    main()
