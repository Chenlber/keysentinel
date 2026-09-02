#!/usr/bin/env python3
"""Stage 3.5: 欠费确认——对 valid key 发送单条"你好"，确认是否欠费。

背景：部分 key 能通过 GET /models（200）验证为 valid，但账户欠费后
实际调用 chat 接口会失败。本阶段发送一条最小成本请求（max_tokens=1，
内容"你好"，成本可忽略），根据响应区分：
  ok      可正常调用（未欠费）
  billing 欠费（402 / insufficient_quota 等）
  invalid 已验证的 key 实际已失效（401/403）
  uncertain 网络或未知错误

注意：
- openrouter 欠费状态由 verifier 的 /api/v1/key（usage/limit）直接给出，
  这里用免费模型仅确认"可调用"。
- 手动模式（--key）会真实计费（仅单条，成本约等于 0）。

用法:
  python3 billing_check.py               # 读取 verified.jsonl 中 status=valid 的记录
  python3 billing_check.py --key sk-xxx  # 手动指定 key 快速检查
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.verifier import build_candidates, VERIFIED_FILE

BILLING_FILE = os.path.join(config.DATA_DIR, "billing.jsonl")

# 欠费特征错误码（OpenAI 兼容）
BILLING_CODES = {"insufficient_quota", "billing_hard_limit_reached", "insufficient_quota_error"}


def pick_model(session, key, service):
    """先查可用模型，避免硬编码模型名失效导致误判。

    流程：GET models_url（零消耗）→ prefer 偏好匹配（便宜优先）→ 列表第一个。
    返回 (model, status, reason)：
      model 非 None 表示可用，可继续发 chat 请求；
      model 为 None 时 status 为判定状态（invalid/uncertain），reason 为原因。
    """
    ep = config.CHAT_ENDPOINTS[service]
    if ep["auth"] == "bearer":
        headers = {"Authorization": f"Bearer {key}"}
    else:  # anthropic
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    try:
        resp = session.get(ep["models_url"], headers=headers, timeout=config.VERIFY_TIMEOUT)
    except requests.RequestException as e:
        return None, "uncertain", f"{service} 模型列表 net: {str(e)[:50]}"
    if resp.status_code == 401 or resp.status_code == 403:
        return None, "invalid", f"{service} 模型列表 {resp.status_code}: {resp.text[:60]}"
    if resp.status_code != 200:
        return None, "uncertain", f"{service} 模型列表 http {resp.status_code}: {resp.text[:60]}"
    try:
        models = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
    except Exception:
        models = []
    if not models:
        return None, "uncertain", f"{service} 模型列表为空"
    for m in ep["prefer"]:
        if m in models:
            return m, "", ""
    return models[0], "", ""


def check_one(session, key, service):
    """向单个服务发送"你好"。返回 (status, reason)。"""
    ep = config.CHAT_ENDPOINTS[service]
    model, status, err = pick_model(session, key, service)
    if model is None:
        return status, err
    if ep["auth"] == "bearer":
        headers = {"Authorization": f"Bearer {key}"}
    else:  # anthropic
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "你好"}],
    }
    try:
        resp = session.post(ep["chat_url"], json=body, headers=headers, timeout=config.VERIFY_TIMEOUT)
    except requests.RequestException as e:
        return "uncertain", f"{service} net: {str(e)[:50]}"
    if resp.status_code == 200:
        return "ok", f"{service} 可正常调用 ({model})"
    code, msg = "", ""
    try:
        errj = resp.json().get("error") or {}
        code, msg = errj.get("code", ""), errj.get("message", "")
    except Exception:
        pass
    if resp.status_code == 402 or code in BILLING_CODES:
        return "billing", f"{service} 欠费: http {resp.status_code} code={code or '-'} {resp.text[:80]}"
    if resp.status_code in (401, 403):
        return "invalid", f"{service} {resp.status_code}: {resp.text[:60]}"
    return "uncertain", f"{service} http {resp.status_code}: {code or msg[:60]}"


def main():
    parser = argparse.ArgumentParser(description="欠费确认：发送'你好'检查 key 是否欠费")
    parser.add_argument("--key", help="直接指定 key 检查（跳过 verified.jsonl）")
    # parse_known_args：经 main.py 串联时会带 --stage，未知参数直接忽略
    args, _ = parser.parse_known_args()

    session = requests.Session()
    stats = {"ok": 0, "billing": 0, "invalid": 0, "uncertain": 0, "proxy": 0}
    n = 0
    f = None
    out = sys.stdout

    if args.key:
        recs = [{"key": args.key, "key_hash": args.key[:12] + "***",
                 "repo": "-", "path": "manual"}]
    else:
        if not os.path.exists(VERIFIED_FILE):
            print(f"缺少 {VERIFIED_FILE}，请先运行 verifier.py。", file=sys.stderr)
            sys.exit(1)
        f = open(VERIFIED_FILE, encoding="utf-8")
        out = open(BILLING_FILE, "w", encoding="utf-8")
        recs = (r for r in (json.loads(line) for line in f) if r["status"] == "valid")

    try:
        for rec in recs:
            n += 1
            candidates = build_candidates(
                rec["key"], rec.get("base_url"), rec.get("proxy_base_url")
            )
            if candidates is None:
                status, reason = "proxy", f"base_url={rec.get('proxy_base_url')}"
            else:
                status, reason = "invalid", ""
                for service in candidates:
                    s, r = check_one(session, rec["key"], service)
                    if s in ("ok", "billing"):
                        status, reason = s, r
                        break
                    if s != "invalid" and status == "invalid":
                        status, reason = s, r
                    time.sleep(config.VERIFY_INTERVAL)
            rec["status"], rec["reason"] = status, reason
            stats[status] += 1
            if out is not sys.stdout:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  [{n}] {status:<8} {rec['key_hash']} {rec['repo']} {rec['path']} <- {reason}", flush=True)
            time.sleep(config.VERIFY_INTERVAL)
    finally:
        if f:
            f.close()
        if out is not sys.stdout:
            out.close()

    print(f"==== 欠费确认（共 {n} 条）====")
    for k, v in stats.items():
        print(f"  {k:<10}: {v}")


if __name__ == "__main__":
    main()
