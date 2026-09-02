#!/usr/bin/env python3
"""KeySentinel CLI —— 串联完整流水线。

阶段顺序:
  crawler → extractor → verifier → billing → reporter → history → readme → notify

用法:
  python3 main.py                      # 全流程（notify 为 dry-run）
  python3 main.py --send               # 全流程并实际发送通知
  python3 main.py --stage crawler      # 单阶段（crawler 支持断点续跑）
  python3 main.py --stage notify --send

环境变量:
  GITHUB_TOKEN          crawler 采集用（GitHub code search）
  KEYSTORE_PASSPHRASE   history/ 敏感字段加解密密钥
  CRAWL_LIMIT_QUERIES / CRAWL_LIMIT_PAGES   限制采集规模
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import billing_check
import crawler
import extractor
import notify
import reporter
import update_history
import update_readme
import verifier

STAGES = ["crawler", "extractor", "verifier", "billing", "reporter",
          "history", "readme", "notify"]

MODULES = {
    "crawler": crawler,
    "extractor": extractor,
    "verifier": verifier,
    "billing": billing_check,
    "reporter": reporter,
    "history": update_history,
    "readme": update_readme,
    "notify": notify,
}


def main():
    parser = argparse.ArgumentParser(description="KeySentinel 泄露扫描流水线")
    parser.add_argument("--stage", choices=STAGES + ["all"], default="all",
                        help="执行阶段（默认 all；crawler 支持断点续跑）")
    parser.add_argument("--send", action="store_true",
                        help="notify 阶段实际发送邮件与 Issue（默认 dry-run）")
    args = parser.parse_args()

    stages = STAGES if args.stage == "all" else [args.stage]
    for name in stages:
        print(f"\n########## Stage: {name} ##########", flush=True)
        if name == "notify":
            MODULES[name].main(send=args.send)   # 显式传参，避免 argv 副作用
        else:
            MODULES[name].main()


if __name__ == "__main__":
    main()
