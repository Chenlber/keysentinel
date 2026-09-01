<div align="center">

# KeySentinel

**GitHub 上泄露的 API Key 猎手 — 发现有效密钥，提醒仓库主人**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Zero-Cost](https://img.shields.io/badge/Verification-Zero--Cost-orange)
![Responsible](https://img.shields.io/badge/Disclosure-Responsible-brightgreen)

</div>

---

## 这是什么

KeySentinel 扫描 GitHub 公开仓库中的 API Key（OpenAI / DeepSeek / Moonshot / Anthropic / Groq / OpenRouter），用**零消耗**方式验证有效性，产出**脱敏**的待通知清单，帮助仓库主人发现并轮换泄露的密钥。

**核心理念：负责任披露。** 工具只发现与提醒，不自动通知，不接触第三方代理，不消耗任何 token。

## 工作流程

```
crawler ──> extractor ──> verifier ──> reporter ──> notify
  搜索+拉取     正则提取      零消耗验证     去重+报告       邮件通知
```

| 阶段 | 脚本 | 输入 → 输出 | 说明 |
|---|---|---|---|
| 采集 | `crawler.py` | → `data/raw_items.jsonl` | 关键词 x 语言分片搜索，突破单查询 4000 仓库限制；限速 + 退避重试；断点续跑 |
| 提取 | `extractor.py` | `raw_items.jsonl` → `data/keys.jsonl` | 四类正则提取；占位符硬过滤；官方 base_url 路由信号 |
| 验证 | `verifier.py` | `keys.jsonl` → `data/verified.jsonl` | 多服务路由，仅调用 `GET /models` 零消耗端点 |
| 报告 | `reporter.py` | `verified.jsonl` → `out/` | sha256 去重归并；key 掩码脱敏；按状态排序 |
| 通知 | `notify.py` | `verified.jsonl` → SMTP 邮件 + GitHub Issue | 自动提取维护者邮箱；邮件含脱敏详情，Issue 仅隐晦提醒（不暴露 key）；默认 dry-run，`--send` 才执行 |

## 快速开始

```bash
# 1. 克隆并安装依赖
git clone https://github.com/yourname/keysentinel.git
cd keysentinel
pip install -r requirements.txt

# 2. 配置 GitHub PAT（无需任何权限）
cp .env.example .env
# 编辑 .env，填入你的 token

# 3. 运行
python3 main.py                 # 全量（22 查询，约 40-60 分钟）
python3 main.py --stage crawler # 单阶段运行

# 小批试跑
CRAWL_LIMIT_QUERIES=4 CRAWL_LIMIT_PAGES=1 python3 crawler.py

# 4. 通知（可选）：邮件 + 隐晦 Issue 双通道
# 先 dry-run 预览（不发信、不建 Issue）
SMTP_HOST=smtp.gmail.com SMTP_PORT=465 \
SMTP_USER=you@gmail.com SMTP_PASS=your_app_password \
python3 notify.py

# 确认无误后实际执行（发邮件 + 创建隐晦 Issue）
... python3 notify.py --send
# 或经 main.py
... python3 main.py --stage notify --send

# 通知策略说明：
# - 邮件：含脱敏 key 与文件位置的完整详情，仅收件人可见
# - Issue：只写 "仓库存在安全风险，详情见邮箱"，不暴露 key/文件/验证信息，
#   避免给攻击者提供定向利用信号
# - 顺序建议：先邮件（私下），维护者处理后再视情况公开
```

## GitHub PAT 获取（约 1 分钟）

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
2. Generate new token，Expiration 建议 90 天
3. Resource owner 选自己账号；Repository access 选 **Public repositories (read-only)**
4. **Permissions 全部留空**（Search code 端点不需要任何权限）
5. 复制 `github_pat_` 开头的 token 填入 `.env`

> 安全提示：token 一旦在聊天/日志中明文出现，请立即撤销重建。真实 token 只放 `.env`（已被 gitignore），绝不提交。

## 密钥验证路由

| 服务 | 验证端点 | key 前缀 | 认证方式 |
|---|---|---|---|
| OpenAI | `api.openai.com/v1/models` | `sk-proj-` | Bearer |
| DeepSeek | `api.deepseek.com/models` | `sk-`（歧义候选） | Bearer |
| Moonshot | `api.moonshot.cn/v1/models` | `sk-`（歧义候选） | Bearer |
| OpenRouter | `openrouter.ai/api/v1/key` | `sk-or-` | Bearer |
| Groq | `api.groq.com/openai/v1/models` | `gsk_` | Bearer |
| Anthropic | `api.anthropic.com/v1/models` | `sk-ant-` | x-api-key |

**验证策略**：
- 官方 base_url / key 前缀可辨识 → 路由到对应服务验证
- 裸 `sk-`（OpenAI/DeepSeek/Moonshot 歧义）→ 依次尝试，首个 200 即命中
- 第三方代理 base_url（白名单外）→ 跳过，标 `proxy`
- 全部端点均为零消耗，不产生任何 token 费用

## 报告说明

`out/findings.csv` 与 `out/report.md` 字段：
- `status`：valid（可用）/ invalid（401）/ forbidden（区域限制）/ uncertain（超时）/ proxy（第三方代理）
- `key_masked`：脱敏显示 `前6位***后4位`，完整 key 只存在于 `data/`（gitignored）
- `reason`：验证响应详情，如可用模型列表
- `locations`：同一 key 出现的全部仓库位置

## GitHub Actions 自动化

项目自带 `.github/workflows/scan.yml`，支持 CI 自动化检测：

- **触发方式**：手动（Actions 页面 Run workflow）或定时（每周一 02:30 UTC）
- **缓存断点续跑**：`data/` 通过 actions/cache 缓存，crawler 增量采集，避免每次全量 40-60 分钟
- **参数**：手动触发时可选 `crawl_queries`（0 = 跳过采集只跑下游检测）
- **产物**：脱敏报告 `out/` 作为 artifact 上传（30 天保留）；`data/`（含明文 key）绝不上传

**需要配置的 secrets**：
- `GH_PAT`：你的 GitHub PAT（用于 crawler 的 search code API；与 .env 中同一 token 即可。注意：secret 名称不能以 `GITHUB_` 开头，这是 GitHub 的保留前缀）

**安全边界**：CI 只做检测，**不包含 notify 阶段**——发邮件/建 Issue 是不可逆动作，且 token 不应进入 CI 日志。valid key 的结果会写入 Action Summary，通知请始终在本地人工执行：

```bash
python3 notify.py          # 预览
python3 notify.py --send   # 确认后发送
```

## 贡献指南

欢迎 PR。开发约定：

- 保持零消耗原则：不新增任何可能产生费用的调用
- 保持白名单路由：第三方代理端点不得纳入自动验证
- 新服务接入：在 `config.py` 的 `SERVICE_ENDPOINTS` 添加端点并确认其严格验证 key
- 代码风格：遵循现有模块结构，重要决策写入 docstring

开发流程：

```bash
# 小批验证（不改 crawler 逻辑时）：
# 修改后只需重跑 extractor → verifier → reporter
python3 extractor.py && python3 verifier.py && python3 reporter.py
```

## 免责声明

**使用本工具即表示你同意以下条款：**

1. **合规红线**：本工具只调用零消耗端点验证，不消耗任何 token。但使用他人凭证发送请求可能违反相关法律与服务条款，请自行评估所在地法律风险。
2. **不接触第三方**：工具对白名单外（第三方代理）的 key 一律跳过，不向其发送任何请求。
3. **不自动通知**：工具只产出清单，是否联系仓库主人、如何披露由使用者自行决定。建议通过 GitHub 私有漏洞报告（Security Advisory）而非公开 Issue。
4. **数据安全**：工具运行产生的 `data/` 目录包含明文 key，已被 gitignore 忽略。请勿分享或提交该目录。
5. **用途限制**：本工具仅用于合法的安全研究、密钥泄露自查与负责任披露。禁止用于未授权访问、密钥滥用或任何恶意用途。

## 常见问题

**Q: 为什么找不到有效的 OpenAI key？**
A: OpenAI 参与了 GitHub Secret Scanning Partnership，公开仓库中泄露的 key 会被自动吊销。能扫到大量已失效（invalid）的 key 恰恰说明防护机制在生效。

**Q: 为什么 proxy 类型的 key 不验证？**
A: 这类 key 指向第三方中转站。向未知服务发送请求风险不可控（蜜罐、投毒、法律灰区），且该 key 对官方端点必然无效，验证无意义。保持标记，由人工判断。

**Q: 可以扫其他类型的密钥吗？**
A: 目前聚焦 AI 服务 key。可以在 `config.py` 的 `KEY_PATTERNS` 与 `SERVICE_ENDPOINTS` 扩展其他服务。

## License

MIT License
