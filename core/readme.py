#!/usr/bin/env python3
"""更新 README 中的动态区块（marker 替换）。

区块标记：
  <!-- KEYS_START --> ... <!-- KEYS_END -->

展示策略（负责任披露）：
- 默认只显示统计数字（发现数 / 已通知 / 已修复）
- 仓库名仅在"已确认修复"后展示（见 history/fixed_repos.json）
- 未修复的仓库不暴露仓库名/文件路径，避免给攻击者提供定向情报

数据源 history/valid_keys.json 中 repo/path/html_url 为加密存储，
读取时用 KEYSTORE_PASSPHRASE 解密（未设置则按明文处理，兼容历史数据）。

用法:
  python3 update_readme.py
"""
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import crypto_util as crypto

SENSITIVE_FIELDS = ("repo", "path", "html_url")

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


def obfuscate_repo(repo):
    """仓库名部分打码：主人能认出自己，外人无法精确搜索定位。

    规则：
      owner  -> 首 2 字符 + *** + 末 1 字符    如 FLD-TN   -> FL***N
      仓库名 -> 首 3 字符 + *** + 末 2 字符    如 MilkteaManage_System -> Mil***em
    这样 GitHub 搜索无法命中完整仓库名，但维护者一眼可辨。
    """
    if not repo or "/" not in repo:
        return repo or ""
    owner, _, name = repo.partition("/")

    def mask_part(s, head, tail):
        # 短名称（<=6 字符）保留首尾各 2 字符，保证主人仍可辨识
        if len(s) <= 6:
            if len(s) <= 4:
                return s[0] + "***" + s[-1] if len(s) >= 3 else s
            return s[:2] + "***" + s[-2:]
        return s[:head] + "***" + s[-tail:]

    return f"{mask_part(owner, 2, 1)}/{mask_part(name, 3, 2)}"


def build_section(data, fixed_repos):
    total = data.get("total_valid", 0)
    items = data.get("items", [])
    # 用 repo_hash 匹配（不受加密影响，无口令时也能正确识别已修复仓库）
    fixed_set = {r.get("repo_hash") for r in fixed_repos.get("repos", []) if r.get("repo_hash")}
    # 兼容旧格式：fixed_repos.json 里只有 repo 时，现场算 hash
    for r in fixed_repos.get("repos", []):
        if r.get("repo") and not r.get("repo_hash"):
            fixed_set.add(hashlib.sha256(r["repo"].encode("utf-8")).hexdigest()[:8])

    lines = [MARKER_START, "", "### 已发现的有效 key（自动更新）", ""]
    lines.append(f"**累计发现 {total} 个有效 key**（零消耗验证）")
    lines.append("")
    lines.append(f"- 已通知仓库：**{total}**")
    lines.append(f"- 已确认修复：**{len(fixed_set)}**")
    lines.append("")

    fixed_items = [v for v in items if v.get("repo_hash") in fixed_set]
    # 未设置 KEYSTORE_PASSPHRASE 时 repo/path 仍是密文（base64 长串），
    # 绝不能把密文渲染进 README（既不可读又暴露数据存在）
    def looks_encrypted(s):
        s = str(s or "")
        return len(s) > 40 and "/" not in s  # 仓库名必含 "/"，密文不含

    can_decrypt = not any(looks_encrypted(v.get("repo")) for v in items)
    if fixed_items and can_decrypt:
        lines.append("#### 已修复（仓库名部分打码）")
        lines.append("")
        lines.append("| 仓库 | Key（脱敏） | 状态 | 发现日期 |")
        lines.append("|---|---|---|---|")
        for v in fixed_items:
            # 打码显示，且不附链接（链接会暴露完整仓库名）
            repo = obfuscate_repo(v["repo"])
            billing = (
                "欠费" if v.get("billing") == "billing"
                else ("可用" if v.get("billing") == "ok" else "待核")
            )
            lines.append(
                f"| `{repo}` | `{v.get('key_masked','')}` "
                f"| {billing} | {v.get('discovered_at','')} |"
            )
        lines.append("")
        lines.append(
            "> 仓库名做了部分打码：维护者可凭首尾字符认出自己的仓库，"
            "但外部无法通过搜索精确定位。文件路径不公开。"
        )
        lines.append("")
    elif fixed_items and not can_decrypt:
        # 有已修复仓库但无法解密 → 只报数量，绝不把密文写进 README
        lines.append(f"*已确认修复 {len(fixed_items)} 个仓库（详情需解密密钥，本地运行查看）。*")
        lines.append("")
    else:
        lines.append("*暂无已确认修复的仓库。*")
        lines.append("")

    # 未修复的仓库：只显示 sha256 前 8 位，不暴露仓库名
    pending_items = [v for v in items if v.get("repo_hash") not in fixed_set]
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
    # 解密敏感字段（未设置 KEYSTORE_PASSPHRASE 时按明文处理）
    data["items"] = [crypto.decrypt_fields(dict(v), SENSITIVE_FIELDS) for v in data.get("items", [])]
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
