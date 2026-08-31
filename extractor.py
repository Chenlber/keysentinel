#!/usr/bin/env python3
"""Stage 2: 从 raw 内容中提取 OpenAI key。

- 四类正则提取
- 硬过滤占位符/测试值（HARD_BLOCK）
- 软标记：路径疑似示例/文档（suspect）
- 代理 key 检测：代码中 base_url 指向非官方域名时标记 proxy（跳过验证）
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

RAW_FILE = os.path.join(config.DATA_DIR, "raw_items.jsonl")
KEYS_FILE = os.path.join(config.DATA_DIR, "keys.jsonl")


def extract_keys(content):
    found = []
    if not content:
        return found
    for pat in config.KEY_PATTERNS:
        for m in pat.finditer(content):
            k = m.group(1)
            if config.HARD_BLOCK_RE.search(k):
                continue
            found.append((k, m.group(0)[:150]))
    return found


def host_of(url):
    """提取 URL 的 host，失败返回 None。"""
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname
    except Exception:
        return None


def detect_base_url(content):
    """返回内容中第一个 base_url/api_base/endpoint 赋值，找不到返回 None。"""
    m = config.PROXY_URL_RE.search(content)
    if not m:
        return None
    return m.group(1)


def main():
    if not os.path.exists(RAW_FILE):
        print("缺少 data/raw_items.jsonl，请先运行 crawler.py。", file=sys.stderr)
        sys.exit(1)

    seen = set()
    stats = {"extracted": 0, "unique": 0, "proxy": 0, "suspect": 0}

    with open(RAW_FILE, encoding="utf-8") as f, open(KEYS_FILE, "w", encoding="utf-8") as out:
        for line in f:
            rec = json.loads(line)
            content = rec.get("content", "")
            url = detect_base_url(content)
            host = host_of(url) if url else None
            if host and host in config.HOST_ROUTES:
                base_url, proxy = url, None      # 官方 host → 路由信号
            else:
                base_url, proxy = None, url      # 非白名单 host → 跳过验证
            suspect = bool(config.SUSPECT_PATH.search(rec.get("path", "")))
            for key, ctx in extract_keys(content):
                h = hashlib.sha256(key.encode()).hexdigest()[:16]
                if h in seen:
                    continue
                seen.add(h)
                stats["extracted"] += 1
                stats["unique"] += 1
                if proxy:
                    stats["proxy"] += 1
                if suspect:
                    stats["suspect"] += 1
                out.write(json.dumps({
                    "key": key,
                    "key_hash": h,
                    "repo": rec["repo"],
                    "path": rec["path"],
                    "html_url": rec.get("html_url", ""),
                    "context": ctx,
                    "base_url": base_url,
                    "proxy_base_url": proxy,
                    "suspect": suspect,
                }, ensure_ascii=False) + "\n")

    print(f"==== 提取结果 ====")
    print(f"  唯一 key 数: {stats['unique']}")
    print(f"  其中代理 key: {stats['proxy']}")
    print(f"  疑似示例/文档路径: {stats['suspect']}")
    print(f"  落盘: {KEYS_FILE}")


if __name__ == "__main__":
    main()
