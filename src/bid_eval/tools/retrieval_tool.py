from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from chromadb import PersistentClient
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from src.bid_eval.config import VECTORSTORE_DIR
import math


# ── 常量 ─────────────────────────────────────────────────────────────────────

COLLECTION_NAME = "deviation_cases"
N_RESULTS       = 3          # 每次检索返回的最大案例数
MIN_RELEVANCE   = 0.5        # 相似度阈值，低于此值的结果不返回


# ── Embedding 函数 ────────────────────────────────────────────────────────────

def _get_embedding_fn() -> OpenAIEmbeddingFunction:
    """
    使用 DeepSeek 兼容 OpenAI 格式的 embedding 接口。
    注意：DeepSeek 目前不提供原生 embedding 模型，
    这里预留接口，MVP 阶段使用 ChromaDB 默认的本地 embedding。
    """
    return None  # MVP 阶段使用 ChromaDB 默认 embedding（all-MiniLM-L6-v2）


# ── ChromaDB 客户端 ───────────────────────────────────────────────────────────

def _get_collection():
    """获取或创建 deviation_cases collection"""
    client = PersistentClient(path=str(VECTORSTORE_DIR))
    embedding_fn = _get_embedding_fn()

    kwargs = {"name": COLLECTION_NAME, "get_or_create": True}
    if embedding_fn:
        kwargs["embedding_function"] = embedding_fn

    return client.get_or_create_collection(**kwargs)


# ── Tool 输入模型 ─────────────────────────────────────────────────────────────

class RetrievalToolInput(BaseModel):
    query: str = Field(
        description=(
            "检索查询文本，描述当前遇到的偏离情况，"
            "例如：'CPU 主频低于招标要求 0.2GHz 的负偏离处理方案'"
        )
    )
    category: str = Field(
        default="",
        description=(
            "可选的指标类别过滤，如：硬件规格 / 软件功能 / 性能指标 / "
            "合规资质 / 商务条款 / 服务要求。为空则不过滤。"
        )
    )


# ── Tool 实现 ─────────────────────────────────────────────────────────────────

class RetrievalTool(BaseTool):
    name: str = "历史偏离案例检索工具"
    description: str = (
        "根据当前偏离情况描述，从历史案例库中检索相似的偏离案例和处理方案。"
        "在判断负偏离的处理方案时调用，为 Agent 提供参考依据。"
        "知识库为空时返回空结果，不影响 Agent 继续工作。"
    )
    args_schema: type[BaseModel] = RetrievalToolInput

    def _run(self, query: str, category: str = "") -> str:
        collection = _get_collection()

        # 知识库为空时直接返回，不报错
        if collection.count() == 0:
            return "历史案例库暂无数据，请根据专业判断给出处理方案。"

        # 构造过滤条件
        where = {"deviation_type": "负偏离"}
        if category:
            where["category"] = category

        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(N_RESULTS, collection.count()),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            return f"检索失败：{e}，请根据专业判断给出处理方案。"

        # 解析结果
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not documents:
            return "未找到相似历史案例，请根据专业判断给出处理方案。"

        # 过滤低相关度结果并格式化输出
        cases = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            relevance = 1 / (1 + dist)   # ChromaDB 默认用 L2 距离，转换为相似度
            if relevance < MIN_RELEVANCE:
                continue
            cases.append(
                f"【案例】相似度 {relevance:.0%}\n"
                f"偏离描述：{meta.get('requirement', '')} | "
                f"{meta.get('deviation_detail', '')}\n"
                f"处理方案：{meta.get('solution', '')}\n"
                f"结果：{meta.get('outcome', '未知')}"
            )

        if not cases:
            return "检索到的案例相关度过低，请根据专业判断给出处理方案。"

        header = f"找到 {len(cases)} 条相似历史案例，供参考：\n"
        return header + "\n\n".join(cases)