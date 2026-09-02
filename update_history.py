#!/usr/bin/env python3
"""生成 history/valid_keys.json —— 历史发现的脱敏汇总数据源。

读取 data/verified.jsonl（valid）+ data/billing.jsonl（欠费状态），
输出脱敏 JSON 供 README 动态展示与后续分析。
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

HISTORY_DIR = os.path.join(config.BASE_DIR, "history")
VALID_KEYS_JSON = os.path.join(HISTORY_DIR, "valid_keys.json")


def mask(key):
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:6] + "***" + key[-4:]


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

    # 读 valid key
    valid = []
    with open(os.path.join(config.DATA_DIR, "verified.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") != "valid":
                continue
            h = r["key_hash"]
            valid.append({
                "repo": r["repo"],
                "path": r["path"],
                "key_masked": mask(r["key"]),
                "html_url": r.get("html_url", ""),
                "billing": billing.get(h, ""),
                "discovered_at": datetime.now().strftime("%Y-%m-%d"),
            })

    os.makedirs(HISTORY_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_valid": len(valid),
        "items": valid,
    }
    with open(VALID_KEYS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"已生成 {VALID_KEYS_JSON}")
    print(f"  valid key 总数: {len(valid)}")
    for v in valid:
        print(f"  - {v['repo']} | {v['key_masked']} | billing={v['billing'] or '-'}")


if __name__ == "__main__":
    main()
