"""集中配置：限速、查询分片、key 正则。token 从环境变量或 .env 读取。"""
import os
import re


def _load_dotenv():
    """轻量 .env 加载：已存在的环境变量优先，不覆盖。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# Search code 端点官方限速 10 req/min，保守取每 6.5s 一次
SEARCH_INTERVAL = 6.5
BACKOFF_BASE = 5
MAX_RETRIES = 5

PER_PAGE = 100
MAX_PAGES = 10  # 单查询最多 1000 条

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_DIR = os.path.join(BASE_DIR, "out")

# 查询分片：关键词 x 语言组合，使各子查询命中不同的仓库池。
# sk-proj- 直接命中新版 key 值，提取率高，优先执行。
QUERIES = [
    ("sk-proj-", "python"),
    ("sk-proj-", "javascript"),
    ("sk-proj-", "typescript"),
    ("sk-proj-", "go"),
    ("sk-proj-", "java"),
    ("sk-proj-", "shell"),
    ("sk-proj-", "php"),
    ("sk-proj-", "ruby"),
    ("sk-proj-", "rust"),
    ("sk-proj-", "csharp"),
    ("sk-proj-", "swift"),
    ("sk-proj-", "kotlin"),
    ("OPENAI_API_KEY", "python"),
    ("OPENAI_API_KEY", "javascript"),
    ("OPENAI_API_KEY", "typescript"),
    ("OPENAI_API_KEY", "go"),
    ("OPENAI_API_KEY", "java"),
    ("openai_api_key", "python"),
    ("openai_api_key", "javascript"),
    ("openai_api_key", "typescript"),
    ("OPENAI_KEY", "python"),
    ("OPENAI_KEY", "javascript"),
]

# 四类 key 正则（group(1) 为 key）
KEY_PATTERNS = [
    # 1. 变量赋值形式
    re.compile(
        r"""(?:OPENAI|OpenAI|openai)[_.]?(?:API[_.]?KEY|KEY|API_KEY)\s*[:=]\s*["'](sk-[A-Za-z0-9_\-]{20,})["']"""
    ),
    # 2. 新版 project key
    re.compile(r"\b(sk-proj-[A-Za-z0-9_\-]{20,})\b"),
    # 3. 旧版裸 key
    re.compile(r"\b(sk-[A-Za-z0-9]{20,})\b"),
    # 4. JSON 字段形式
    re.compile(r"""["'](?:api_key|apiKey|api-key)["']\s*:\s*["'](sk-[A-Za-z0-9_\-]{20,})["']"""),
]

# 硬过滤：命中即视为占位符/测试值，直接丢弃
# 注意：不能有 sk-[A-Z]+\b 这类 IGNORECASE 下会误杀 sk-proj- 前缀的规则
HARD_BLOCK = [
    r"sk-test", r"sk-example", r"sk-dummy", r"sk-fake", r"sk-xxxx", r"sk-xxx",
    r"sk-recorded-replay", r"sk-\*+", r"sk-your", r"sk-put", r"sk-replace",
    r"sk-here", r"sk-demo", r"sk-sample", r"sk-mock", r"sk-dummykey",
    r"sk-validation-credential-value", r"sk-proj-validation-credential-value",
    r"^sk-0{20,}$",
    # 常见测试占位符格式
    r"sk-abc\d{3}", r"sk-xyz\d{3}", r"sk-live\d+", r"sk-key\d+",
    r"sk-proj-abc\d{3}", r"sk-proj-[A-Za-z0-9]{8,}\*+",
    r"sk-proj-XYZ\d*[A-Z]*\d*", r"sk-proj-1234567890",
    r"realkey", r"sk-proj-realkey", r"liveSECRET", r"sk-liveSECRET",
    # 明确大写占位符（整串为单词性占位），不再匹配 sk-proj 前缀
    r"^sk-[A-Z]{3,}$",
]
HARD_BLOCK_RE = re.compile("|".join(HARD_BLOCK), re.IGNORECASE)

# 软标记：命中这些路径的 key 仍保留但标记（疑似示例/文档/fork）
SUSPECT_PATH = re.compile(
    r"(test|example|demo|sample|docs|documentation|readme|fixtures?|templates?)",
    re.IGNORECASE,
)

# 代理/第三方 base url 检测：命中则跳过验证
PROXY_URL_RE = re.compile(
    r"(?:base_url|BASE_URL|api_base|apiBase|endpoint|host)\s*[:=]\s*[\"'](https?://[^\"']+)[\"']",
    re.IGNORECASE,
)

# 服务 → 验证端点与认证方式
# 注意：openrouter 的 /models 端点不验证 key（无认证也返回 200），
# 必须用 /api/v1/key 端点（严格 401 拒绝无效 key）做验证。
SERVICE_ENDPOINTS = {
    "openai":     {"url": "https://api.openai.com/v1/models",          "auth": "bearer"},
    "deepseek":   {"url": "https://api.deepseek.com/models",           "auth": "bearer"},
    "moonshot":   {"url": "https://api.moonshot.cn/v1/models",         "auth": "bearer"},
    "openrouter": {"url": "https://openrouter.ai/api/v1/key",          "auth": "bearer"},
    "groq":       {"url": "https://api.groq.com/openai/v1/models",     "auth": "bearer"},
    "anthropic":  {"url": "https://api.anthropic.com/v1/models",       "auth": "anthropic"},
}

# 官方 host → 服务（用于 base_url 路由）
HOST_ROUTES = {
    "api.openai.com": "openai",
    "api.deepseek.com": "deepseek",
    "api.moonshot.cn": "moonshot",
    "api.anthropic.com": "anthropic",
    "api.groq.com": "groq",
    "openrouter.ai": "openrouter",
}

# key 前缀 → 服务（确定性路由）
PREFIX_ROUTES = {
    "sk-proj-": "openai",
    "sk-ant-": "anthropic",
    "sk-or-": "openrouter",
    "gsk_": "groq",
}

# 裸 sk- 前缀存在歧义（OpenAI/DeepSeek/Moonshot 共用），依次尝试，首个 200 即命中
SK_FALLBACK = ["openai", "deepseek", "moonshot"]

# 欠费确认（billing_check.py 使用）：
# 先查可用模型再发请求：不硬编码唯一模型名（模型可能失效/下线），
# 而是先零消耗 GET models_url 拉取该 key 实际可用的模型列表，
# 按 prefer（便宜优先）匹配，匹配不到用列表第一个。
# 仅发送单条 "你好"（max_tokens=1），成本可忽略。
CHAT_ENDPOINTS = {
    "openai":     {"chat_url": "https://api.openai.com/v1/chat/completions",       "models_url": "https://api.openai.com/v1/models",          "prefer": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-3.5-turbo"], "auth": "bearer"},
    "deepseek":   {"chat_url": "https://api.deepseek.com/chat/completions",        "models_url": "https://api.deepseek.com/models",           "prefer": ["deepseek-chat"],                                 "auth": "bearer"},
    "moonshot":   {"chat_url": "https://api.moonshot.cn/v1/chat/completions",      "models_url": "https://api.moonshot.cn/v1/models",         "prefer": ["moonshot-v1-8k", "kimi-latest"],                 "auth": "bearer"},
    "groq":       {"chat_url": "https://api.groq.com/openai/v1/chat/completions",  "models_url": "https://api.groq.com/openai/v1/models",     "prefer": ["llama-3.1-8b-instant", "llama3-8b-8192"],        "auth": "bearer"},
    "openrouter": {"chat_url": "https://openrouter.ai/api/v1/chat/completions",    "models_url": "https://openrouter.ai/api/v1/models",       "prefer": ["meta-llama/llama-3.1-8b-instruct:free"],          "auth": "bearer"},
    "anthropic":  {"chat_url": "https://api.anthropic.com/v1/messages",            "models_url": "https://api.anthropic.com/v1/models",       "prefer": ["claude-3-5-haiku-latest", "claude-3-haiku-20240307"], "auth": "anthropic"},
}

VERIFY_TIMEOUT = 15
VERIFY_INTERVAL = 0.5  # 串行验证间隔，防 429
