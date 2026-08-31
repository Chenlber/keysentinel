#!/usr/bin/env python3
"""CLI 串联四阶段流水线。

用法:
  GITHUB_TOKEN=ghp_xxx python3 main.py            # 全流程
  GITHUB_TOKEN=ghp_xxx python3 main.py --stage crawler   # 单阶段（支持断点续跑）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawler
import extractor
import reporter
import verifier

STAGES = ["crawler", "extractor", "verifier", "reporter"]


def main():
    parser = argparse.ArgumentParser(description="GitHub OpenAI Key 泄露扫描流水线")
    parser.add_argument("--stage", choices=STAGES + ["all"], default="all",
                        help="执行阶段（默认 all；crawler 支持断点续跑）")
    args = parser.parse_args()

    modules = STAGES if args.stage == "all" else [args.stage]
    mods = {"crawler": crawler, "extractor": extractor, "verifier": verifier, "reporter": reporter}
    for name in modules:
        print(f"\n########## Stage: {name} ##########", flush=True)
        mods[name].main()


if __name__ == "__main__":
    main()
