#!/usr/bin/env python3
"""针对指定仓库扫描 key，复用 crawler 的搜索/拉取逻辑，数据落入 data/ 走标准流水线。

用法:
  python3 scan_repos.py                 # 扫描内置仓库列表
  python3 scan_repos.py repo/a repo/b   # 或指定仓库
"""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import crawler  # 复用 RateLimiter / fetch_raw / ref_from_html / HEADERS

REPOS = [
    "abhiram-120/ec2-code",
    "yzhsuisuis/ChatGPT-sdk-java",
    "abdullahktk760/transcription",
    "tabrej-the-developer/mydiaree",
    "JawherBalti/wbcc_extranet",
    "Abdallah-Salah7/Graduation-Project",
    "fsawadogo/sqordia-repo",
    "mac383/Utemy",
    "RobertAlexBarbu/RestaurantMenuApp",
]


def search_repo(session, rl, repo, page):
    """搜索指定仓库中的 sk- 代码，复用 crawler 的限速与重试策略。"""
    params = {"q": f'repo:{repo} sk-', "per_page": config.PER_PAGE, "page": page}
    for attempt in range(config.MAX_RETRIES):
        rl.wait()
        try:
            resp = session.get(f"{config.GITHUB_API}/search/code", params=params, headers=crawler.HEADERS, timeout=30)
        except requests.RequestException:
            time.sleep(config.BACKOFF_BASE)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (403, 422):
            time.sleep(config.BACKOFF_BASE * (2 ** attempt))
            continue
        time.sleep(config.BACKOFF_BASE)
    return None


def main():
    if not config.GITHUB_TOKEN:
        print("缺少 GITHUB_TOKEN", file=sys.stderr)
        sys.exit(1)
    repos = sys.argv[1:] or REPOS
    os.makedirs(config.DATA_DIR, exist_ok=True)
    session = requests.Session()
    rl = crawler.RateLimiter(config.SEARCH_INTERVAL)
    done = crawler.load_done()

    with open(crawler.RAW_FILE, "a", encoding="utf-8") as raw_f, open(crawler.DONE_FILE, "a", encoding="utf-8") as done_f:
        for repo in repos:
            print(f"[repo] {repo}", flush=True)
            page = 1
            got = 0
            while page <= config.MAX_PAGES:
                data = search_repo(session, rl, repo, page)
                if not data:
                    break
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    full = item["repository"]["full_name"]
                    path = item["path"]
                    key = f"{full}:{path}"
                    if key in done:
                        continue
                    done.add(key)
                    html_url = item.get("html_url", "")
                    ref = crawler.ref_from_html(html_url) or "main"
                    content = crawler.fetch_raw(session, full, path, ref)
                    record = {
                        "repo": full,
                        "path": path,
                        "html_url": html_url,
                        "branch": ref,
                        "content": content or "",
                    }
                    raw_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    done_f.write(key + "\n")
                    got += 1
                    time.sleep(0.15)
                print(f"  page {page}: +{len(items)} 命中, 新增 {got}", flush=True)
                if len(items) < config.PER_PAGE:
                    break
                page += 1
            print(f"[done] {repo}: 新增 {got} 文件", flush=True)
    print("采集完成，接下来运行: python3 extractor.py && python3 verifier.py && python3 reporter.py")


if __name__ == "__main__":
    main()
