#!/usr/bin/env python3
"""GitHub Issue 通知：构建隐晦 Issue 并创建（公开动作，不可撤回）。

设计：Issue 不暴露 key/文件路径/验证信息，仅提醒维护者查邮箱，
避免给攻击者提供定向利用信号。
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def build_issue(rec):
    """构建隐晦 Issue：不暴露 key、文件路径、验证信息。"""
    owner = rec["repo"].split("/")[0]
    title = "[Security] Please check your inbox"
    body = f"""Hi @{owner},

We've identified a potential security concern related to this repository.

Details have been sent to your email address. Please check your inbox and take appropriate action.

— KeySentinel (open source, https://github.com/Chenlber/keysentinel)"""
    return title, body


def create_issue(repo, title, body):
    """通过 GitHub API 创建 Issue。返回是否成功。"""
    if not config.GITHUB_TOKEN:
        print("缺少 GITHUB_TOKEN，无法创建 Issue。", file=sys.stderr)
        return False
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.post(
            f"{config.GITHUB_API}/repos/{repo}/issues",
            json={"title": title, "body": body},
            headers=headers, timeout=30,
        )
    except requests.RequestException as e:
        print(f"创建 Issue 网络异常: {e}", file=sys.stderr)
        return False
    if r.status_code in (200, 201):
        return True
    print(f"创建 Issue 失败 [{r.status_code}]: {r.text[:200]}", file=sys.stderr)
    return False
