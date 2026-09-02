#!/usr/bin/env python3
"""更新 README 中的动态区块（marker 替换）。

区块标记：
  <!-- KEYS_START --> ... <!-- KEYS_END -->

展示策略（负责任披露）：
- 默认只显示统计数字（发现数 / 已通知 / 已修复）
- 仓库名仅在"已确认修复"后展示（见 history/fixed_repos.json）
- 未修复的仓库不暴露仓库名/文件路径，避免给攻击者提供定向情报

用法:
  python3 update_readme.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

README = os.path.join(config.BASE_DIR, "README.md")
HISTORY_DIR = os.path.join(config.BASE_DIR, "history")
VALID_KEYS_JSON = os.path.join(HISTORY_DIR, "valid_keys.json")
# 已确认修复的仓库列表（人工维护或由 notify 后确认）
FIXED_REPOS_JSON = os.path.join(HISTORY_DIR, "fixed_repos.json")
MARKER_START = "<!-- KEYS_START -->"
MARKER_END = "<!-- KEYS_END -->"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_section(data, fixed_repos):
    total = data.get("total_valid", 0)
    items = data.get("items", [])
    fixed_set = {r["repo"] for r in fixed_repos.get("repos", []) if r.get("repo")}

    lines = [MARKER_START, "", "### 已发现的有效 key（自动更新）", ""]
    lines.append(f"**累计发现 {total} 个有效 key**（零消耗验证）")
    lines.append("")
    lines.append(f"- 已通知仓库：**{total}**")
    lines.append(f"- 已确认修复：**{len(fixed_set)}**")
    lines.append("")

    fixed_items = [v for v in items if v["repo"] in fixed_set]
    if fixed_items:
        lines.append("#### 已修复并公开的仓库（脱敏展示）")
        lines.append("")
        lines.append("| 仓库 | 文件 | Key（脱敏） | 状态 | 发现日期 |")
        lines.append("|---|---|---|---|---|")
        for v in fixed_items:
            repo = f"[{v['repo']}](https://github.com/{v['repo']})"
            billing = (
                "欠费" if v.get("billing") == "billing"
                else ("可用" if v.get("billing") == "ok" else "待核")
            )
            lines.append(
                f"| {repo} | `{v.get('path','')}` | `{v.get('key_masked','')}` "
                f"| {billing} | {v.get('discovered_at','')} |"
            )
        lines.append("")
    else:
        lines.append("*暂无已确认修复的仓库。*")
        lines.append("")

    # 未修复的仓库：只显示 sha256 前 8 位，不暴露仓库名
    pending_items = [v for v in items if v["repo"] not in fixed_set]
    if pending_items:
        lines.append("#### 已通知待修复（仅展示哈希，保护维护者）")
        lines.append("")
        lines.append("| 仓库哈希 | Key（脱敏） | 状态 | 通知日期 |")
        lines.append("|---|---|---|---|")
        for v in pending_items:
            rh = v.get("repo_hash") or "-"
            billing = (
                "欠费" if v.get("billing") == "billing"
                else ("可用" if v.get("billing") == "ok" else "待核")
            )
            lines.append(
                f"| `{rh}` | `{v.get('key_masked','')}` "
                f"| {billing} | {v.get('discovered_at','')} |"
            )
        lines.append("")
        lines.append(
            f"> 另有 **{len(pending_items)}** 个仓库已通知但尚未确认修复。"
            "按负责任披露原则，未修复前仅展示仓库名哈希（sha256 前 8 位），"
            "不公开仓库名与文件路径。维护者确认修复后（key 已撤销）将公开其仓库名。"
        )
        lines.append("")
    lines.append(f"*数据源：`history/valid_keys.json`；更新时间：{datetime.now().strftime('%Y-%m-%d')}*")
    lines.append(MARKER_END)
    return "\n".join(lines)


def main():
    if not os.path.exists(VALID_KEYS_JSON):
        print("缺少 history/valid_keys.json，请先运行 update_history.py。", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(README):
        print(f"缺少 {README}", file=sys.stderr)
        sys.exit(1)

    data = load_json(VALID_KEYS_JSON, {"total_valid": 0, "items": []})
    fixed_repos = load_json(FIXED_REPOS_JSON, {"repos": []})
    new_section = build_section(data, fixed_repos)

    with open(README, encoding="utf-8") as f:
        content = f.read()

    if MARKER_START in content and MARKER_END in content:
        start = content.index(MARKER_START)
        end = content.index(MARKER_END) + len(MARKER_END)
        content = content[:start] + new_section + content[end:]
    else:
        content = content.rstrip() + "\n\n## 已发现的有效 key\n\n" + new_section + "\n"

    with open(README, "w", encoding="utf-8") as f:
        f.write(content)

    fixed_n = len({r["repo"] for r in fixed_repos.get("repos", []) if r.get("repo")})
    print(f"README 已更新：发现 {data.get('total_valid', 0)} 个，已修复公开 {fixed_n} 个")


if __name__ == "__main__":
    main()
