# 全局配置
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# 强制 ChromaDB 使用 text-embedding-v4（必须在 crewai 导入前设置）
os.environ.setdefault("OPENAI_EMBEDDINGS_MODEL", "text-embedding-v4")

from crewai import LLM

# 阿里云百炼 GLM-5 API 配置
BAILIAN_MODEL = "glm-5.1"
BAILIAN_API_KEY = os.getenv("BAILIAN_API_KEY")
BAILIAN_BASE_URL = os.getenv("BAILIAN_BASE_URL")
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY")
OPENAI_API_BASE=os.getenv("OPENAI_API_BASE")

# LLM 实例
llm = LLM(
    model=BAILIAN_MODEL,
    api_key=BAILIAN_API_KEY,
    base_url=BAILIAN_BASE_URL,
    extra_body={
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    },
)

# Memory 系统专用的 LLM（不带 response_format）
memory_llm = LLM(
    model="qwen3-max-2026-01-23",
    #model="qwen3-coder-next",
    api_key=BAILIAN_API_KEY,
    base_url=BAILIAN_BASE_URL,
    extra_body={
        "enable_thinking": False,
    }, # qwen3.5 默认启用 thinking 模式，但在 Memory 场景下不需要，关闭以节省成本和加快响应
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR        = BASE_DIR / os.getenv("DATA_DIR", "data")
PROJECTS_DIR    = DATA_DIR / "projects"
#VECTORSTORE_DIR = DATA_DIR / "vectorstore"
KNOWLEDGE_DIR   = BASE_DIR / os.getenv("KNOWLEDGE_DIR", "knowledge")
DATABASE_DIR    = DATA_DIR / "database"

# ── 运行时目录自动创建 ───────────────────────────────────
_runtime_dirs = [PROJECTS_DIR, KNOWLEDGE_DIR, DATABASE_DIR]
if "VECTORSTORE_DIR" in globals():
    _runtime_dirs.append(VECTORSTORE_DIR)

for _dir in _runtime_dirs:
    _dir.mkdir(parents=True, exist_ok=True)
