#!/usr/bin/env python3
"""Stage 4: 去重归并、脱敏、统计，导出 findings.csv 与 report.md。

- 同一 key 出现在多个位置时归并为一条记录，汇总出现位置
- key 脱敏：前 6 + *** + 后 4，完整 key 只存在于 data/ 中间文件
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

VERIFIED_FILE = os.path.join(config.DATA_DIR, "verified.jsonl")
CSV_FILE = os.path.join(config.OUT_DIR, "findings.csv")
MD_FILE = os.path.join(config.OUT_DIR, "report.md")

STATUS_RANK = {"valid": 0, "forbidden": 1, "uncertain": 2, "proxy": 3, "invalid": 4}


def mask(key):
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:6] + "***" + key[-4:]


def main():
    if not os.path.exists(VERIFIED_FILE):
        print("缺少 data/verified.jsonl，请先运行 verifier.py。", file=sys.stderr)
        sys.exit(1)

    groups = defaultdict(list)
    with open(VERIFIED_FILE, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            groups[rec["key_hash"]].append(rec)

    os.makedirs(config.OUT_DIR, exist_ok=True)
    stats = defaultdict(int)

    rows = []
    for h, recs in groups.items():
        r0 = recs[0]
        status = r0["status"]
        stats[status] += 1
        locs = sorted({(r["repo"], r["path"]) for r in recs})
        rows.append({
            "key_hash": h,
            "key_masked": mask(r0["key"]),
            "status": status,
            "reason": r0.get("reason", ""),
            "base_url": r0.get("base_url", ""),
            "proxy_base_url": r0.get("proxy_base_url", ""),
            "suspect": r0.get("suspect", False),
            "occurrences": len(locs),
            "locations": "; ".join(f"{repo}:{path}" for repo, path in locs),
            "html_url": f"https://github.com/{locs[0][0]}/blob/main/{locs[0][1]}" if locs else "",
        })
    rows.sort(key=lambda r: (STATUS_RANK.get(r["status"], 9), -r["occurrences"]))

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader()
            w.writerows(rows)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(MD_FILE, "w", encoding="utf-8") as f:
        f.write(f"# GitHub OpenAI Key 泄露扫描报告\n\n生成时间：{now}\n\n")
        f.write("## 概览\n\n")
        f.write("| 状态 | 数量 | 说明 |\n|---|---|---|\n")
        f.write(f"| valid | {stats['valid']} | 已通过零消耗验证，仍可使用 |\n")
        f.write(f"| forbidden | {stats['forbidden']} | 认证通过但被限制（区域/组织策略） |\n")
        f.write(f"| uncertain | {stats['uncertain']} | 超时或限流耗尽，未能判定 |\n")
        f.write(f"| proxy | {stats['proxy']} | 指向第三方代理 base_url，跳过验证 |\n")
        f.write(f"| invalid | {stats['invalid']} | 已失效（401） |\n")
        f.write("\n## 待通知清单（valid 优先）\n\n")
        for r in rows:
            f.write(f"- **[{r['status']}]** `{r['key_masked']}` 出现 {r['occurrences']} 处 | "
                    f"{r['locations']} | {r['html_url']} | {r.get('reason', '')}\n")

    print(f"==== 去重归并结果（{len(groups)} 个唯一 key）====")
    for k in ("valid", "forbidden", "uncertain", "proxy", "invalid"):
        print(f"  {k:<10}: {stats[k]}")
    print(f"  报告: {MD_FILE}")
    print(f"  CSV:  {CSV_FILE}")


if __name__ == "__main__":
    main()
