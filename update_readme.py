#!/usr/bin/env python3
"""更新 README 中的动态区块（marker 替换）。

区块标记：
  <!-- KEYS_START --> ... <!-- KEYS_END -->
从 history/valid_keys.json 读取数据生成有效 key 表格。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

README = os.path.join(config.BASE_DIR, "README.md")
VALID_KEYS_JSON = os.path.join(config.BASE_DIR, "history", "valid_keys.json")
MARKER_START = "<!-- KEYS_START -->"
MARKER_END = "<!-- KEYS_END -->"


def build_section(data):
    lines = [MARKER_START, "", "### 已发现的有效 key（自动更新）", ""]
    total = data.get("total_valid", 0)
    lines.append(f"**累计发现 {total} 个有效 key**（零消耗验证，脱敏展示）")
    lines.append("")
    if total == 0:
        lines.append("暂无有效 key。")
        lines.append("")
    else:
        lines.append("| 仓库 | 文件 | Key（脱敏） | 状态 | 发现日期 |")
        lines.append("|---|---|---|---|---|")
        for v in data.get("items", []):
            repo = f"[{v['repo']}](https://github.com/{v['repo']})" if v.get("html_url") else v["repo"]
            path = v.get("path", "")
            billing = "欠费" if v.get("billing") == "billing" else ("可用" if v.get("billing") == "ok" else "待核")
            lines.append(f"| {repo} | `{path}` | `{v.get('key_masked','')}` | {billing} | {v.get('discovered_at','')} |")
        lines.append("")
    lines.append(f"*数据源：`history/valid_keys.json`，由 `update_history.py` 生成。*")
    lines.append("")
    lines.append(MARKER_END)
    return "\n".join(lines)


def main():
    if not os.path.exists(VALID_KEYS_JSON):
        print("缺少 history/valid_keys.json，请先运行 update_history.py。", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(README):
        print(f"缺少 {README}", file=sys.stderr)
        sys.exit(1)

    with open(VALID_KEYS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    new_section = build_section(data)

    with open(README, encoding="utf-8") as f:
        content = f.read()

    if MARKER_START in content and MARKER_END in content:
        start = content.index(MARKER_START)
        end = content.index(MARKER_END) + len(MARKER_END)
        content = content[:start] + new_section + content[end:]
    else:
        # 未找到标记，追加到文件末尾（首次运行）
        content = content.rstrip() + "\n\n## 已发现的有效 key\n\n" + new_section + "\n"

    with open(README, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"README 已更新：有效 key {data.get('total_valid', 0)} 个")


if __name__ == "__main__":
    main()
