#!/usr/bin/env python3
"""Stage 3: 零消耗验证 key 有效性。

路由策略（按优先级）：
1. proxy_base_url（非白名单第三方）→ 跳过，标 proxy
2. base_url 指向官方 host → 路由到该服务验证
3. key 前缀可辨识（sk-proj-/sk-ant-/sk-or-/gsk_）→ 对应服务
4. 裸 sk-（OpenAI/DeepSeek/Moonshot 歧义）→ 依次尝试，首个 200 即命中

所有端点 GET /models 均为零消耗。200 = valid 并返回可用模型。
"""
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

KEYS_FILE = os.path.join(config.DATA_DIR, "keys.jsonl")
VERIFIED_FILE = os.path.join(config.DATA_DIR, "verified.jsonl")

# QUIET=1 时不打印每条明细（CI 日志安全：repo/path/hash 不进入公开日志）
QUIET = os.environ.get("QUIET", "").strip() == "1"


def host_of(url):
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def build_candidates(key, base_url, proxy_base_url):
    """返回候选服务列表；None 表示应跳过验证（第三方代理）。"""
    if proxy_base_url:
        return None
    if base_url:
        host = host_of(base_url)
        service = config.HOST_ROUTES.get(host)
        if service:
            return [service]
    for prefix, service in config.PREFIX_ROUTES.items():
        if key.startswith(prefix):
            return [service]
    if key.startswith("sk-"):
        return config.SK_FALLBACK
    return None


def verify_one(session, key, service):
    """对单个服务做零消耗验证。返回 (status, reason)。"""
    ep = config.SERVICE_ENDPOINTS[service]
    if ep["auth"] == "bearer":
        headers = {"Authorization": f"Bearer {key}"}
    else:  # anthropic: x-api-key + 版本头
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    for attempt in range(3):
        try:
            resp = session.get(ep["url"], headers=headers, timeout=config.VERIFY_TIMEOUT)
        except requests.RequestException as e:
            return "uncertain", f"{service} net: {str(e)[:50]}"
        if resp.status_code == 200:
            try:
                j = resp.json()
                if service == "openrouter":
                    # /api/v1/key 返回 {data: {label, usage, limit, is_free_tier}}
                    d = j.get("data", {})
                    return "valid", f"openrouter: {d.get('label','?')} 额度剩余 ${d.get('usage')}/{d.get('limit')}"
                models = [m.get("id") for m in j.get("data", [])][:8]
            except Exception:
                models = []
            return "valid", f"{service}: {', '.join(models)}" if models else f"{service}: ok"
        if resp.status_code == 401:
            return "invalid", f"{service} 401"
        if resp.status_code == 403:
            return "forbidden", f"{service} 403: {resp.text[:60]}"
        if resp.status_code == 429:
            wait = 5 * (2 ** attempt)
            if not QUIET:
                print(f"    [{service} 429] 退避 {wait}s", flush=True)
            time.sleep(wait)
            continue
        return "uncertain", f"{service} http {resp.status_code}: {resp.text[:60]}"
    return "uncertain", f"{service} 429 exhausted"


def main():
    if not os.path.exists(KEYS_FILE):
        print("缺少 data/keys.jsonl，请先运行 extractor.py。", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    stats = {"valid": 0, "invalid": 0, "forbidden": 0, "uncertain": 0, "proxy": 0}
    n = 0

    with open(KEYS_FILE, encoding="utf-8") as f, open(VERIFIED_FILE, "w", encoding="utf-8") as out:
        for line in f:
            rec = json.loads(line)
            n += 1
            candidates = build_candidates(
                rec["key"], rec.get("base_url"), rec.get("proxy_base_url")
            )
            if candidates is None:
                rec["status"] = "proxy"
                rec["reason"] = f"base_url={rec.get('proxy_base_url')}"
                stats["proxy"] += 1
            else:
                status, reason = "invalid", ""
                for service in candidates:
                    s, r = verify_one(session, rec["key"], service)
                    if s == "valid":
                        status, reason = s, r
                        break
                    if s != "invalid" and status == "invalid":
                        status, reason = s, r  # 保留首个非 invalid 结果
                    time.sleep(config.VERIFY_INTERVAL)
                rec["status"], rec["reason"] = status, reason
                stats[status] = stats.get(status, 0) + 1
                if not QUIET:
                    print(f"  [{n}] {status:<9} {rec['key_hash']} {rec['repo']} {rec['path']} <- {reason}", flush=True)
                time.sleep(config.VERIFY_INTERVAL)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"==== 验证结果（共 {n} 条）====")
    for k, v in stats.items():
        print(f"  {k:<10}: {v}")


if __name__ == "__main__":
    main()
