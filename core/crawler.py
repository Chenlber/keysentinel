#!/usr/bin/env python3
"""Stage 1: 分片搜索 GitHub code，拉取文件原始内容，落盘 data/raw_items.jsonl。

- 滑动窗口限速（Search code 官方 10 req/min，保守 6.5s/次）
- 关键词 x 语言分片，突破单查询 4000 仓库限制
- 403/422/429 指数退避重试
- (repo,path) 去重断点续跑，跳过 fork 仓库
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

HEADERS = {
    "Accept": "application/vnd.github.text-match+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if config.GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"

RAW_FILE = os.path.join(config.DATA_DIR, "raw_items.jsonl")
DONE_FILE = os.path.join(config.DATA_DIR, "fetched.txt")

# 小批试跑控制：CRAWL_LIMIT_QUERIES 限制查询数，CRAWL_LIMIT_PAGES 限制每查询页数
LIMIT_QUERIES = int(os.environ.get("CRAWL_LIMIT_QUERIES", "0") or 0)
LIMIT_PAGES = int(os.environ.get("CRAWL_LIMIT_PAGES", "0") or 0)


class RateLimiter:
    def __init__(self, interval):
        self.interval = interval
        self.last = 0.0

    def wait(self):
        elapsed = time.time() - self.last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last = time.time()


def search_page(session, rl, keyword, language, page):
    q = f'{keyword} language:{language}'
    params = {"q": q, "per_page": config.PER_PAGE, "page": page}
    for attempt in range(config.MAX_RETRIES):
        rl.wait()
        try:
            resp = session.get(f"{config.GITHUB_API}/search/code", params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  [net] {e}", flush=True)
            time.sleep(config.BACKOFF_BASE)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (403, 422):
            wait = config.BACKOFF_BASE * (2 ** attempt)
            print(f"  [{resp.status_code}] 退避 {wait}s", flush=True)
            time.sleep(wait)
            continue
        print(f"  [http {resp.status_code}] {resp.text[:200]}", flush=True)
        time.sleep(config.BACKOFF_BASE)
    return None


def ref_from_html(html_url):
    """从 html_url 解析 commit sha 作为 raw 拉取的 ref，避免分支猜测。"""
    m = re.search(r"/blob/([^/]+)/", html_url)
    return m.group(1) if m else None


def fetch_raw(session, repo, path, ref):
    url = f"{config.GITHUB_RAW}/{repo}/{ref}/{path}"
    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as e:
        print(f"    [raw net] {e}", flush=True)
        return None
    if resp.status_code == 200 and len(resp.content) < 1024 * 1024:
        return resp.text
    return None


def load_done():
    if not os.path.exists(DONE_FILE):
        return set()
    with open(DONE_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def main():
    if not config.GITHUB_TOKEN:
        print("缺少 GITHUB_TOKEN，请设置环境变量后重试。", file=sys.stderr)
        sys.exit(1)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    session = requests.Session()
    rl = RateLimiter(config.SEARCH_INTERVAL)
    done = load_done()
    total_items = 0

    queries = config.QUERIES[:LIMIT_QUERIES] if LIMIT_QUERIES else config.QUERIES
    max_pages = LIMIT_PAGES if LIMIT_PAGES else config.MAX_PAGES

    with open(RAW_FILE, "a", encoding="utf-8") as raw_f, open(DONE_FILE, "a", encoding="utf-8") as done_f:
        for keyword, language in queries:
            print(f"[query] '{keyword}' language:{language}", flush=True)
            page = 1
            got = 0
            while page <= max_pages:
                data = search_page(session, rl, keyword, language, page)
                if not data:
                    break
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    repo = item["repository"]["full_name"]
                    path = item["path"]
                    key = f"{repo}:{path}"
                    if key in done:
                        continue
                    done.add(key)
                    html_url = item.get("html_url", "")
                    ref = ref_from_html(html_url) or "main"
                    content = fetch_raw(session, repo, path, ref)
                    record = {
                        "repo": repo,
                        "path": path,
                        "html_url": html_url,
                        "branch": ref,
                        "content": content or "",
                    }
                    raw_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    done_f.write(key + "\n")
                    got += 1
                    total_items += 1
                    time.sleep(0.15)  # raw 拉取节流
                print(f"  page {page}: +{len(items)} 命中, 新增 {got}", flush=True)
                if data.get("incomplete_results") or len(items) < config.PER_PAGE:
                    break
                page += 1
            print(f"[done] '{keyword}' language:{language}: 累计新增 {got}", flush=True)
    print(f"\n==== 总计新增 {total_items} 条文件 ====", flush=True)


if __name__ == "__main__":
    main()
