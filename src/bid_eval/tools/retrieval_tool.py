from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import sqlite3
from src.bid_eval.config import DATABASE_DIR

# ── 常量 ─────────────────────────────────────────────────────────────────────

DB_FILE = DATABASE_DIR / "deviation_cases.db"
N_RESULTS = 3  # 每次检索返回的最大案例数

# ── 数据库连接 ────────────────────────────────────────────────────────────────

def _get_connection() -> sqlite3.Connection:
    """获取数据库连接，数据库文件不存在时自动创建"""
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    _ensure_schema(conn)
    return conn

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """确保表结构存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deviation_cases (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement     TEXT NOT NULL,
            response        TEXT NOT NULL,
            deviation_type  TEXT NOT NULL,
            deviation_detail TEXT NOT NULL,
            solution        TEXT NOT NULL,
            category        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

def _search_cases(query: str, category: str = "") -> list[dict]:
    """执行 LIKE 模糊搜索，返回匹配的案例列表"""
    conn = _get_connection()
    try:
        sql = """
            SELECT requirement, deviation_detail, solution, response, category
            FROM deviation_cases
            WHERE deviation_type = '负偏离'
              AND (requirement     LIKE '%' || ? || '%'
                   OR deviation_detail LIKE '%' || ? || '%'
                   OR solution     LIKE '%' || ? || '%'
                   OR response     LIKE '%' || ? || '%')
        """
        params = [query, query, query, query]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " LIMIT ?"
        params.append(N_RESULTS)

        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        return [
            {
                "requirement": row[0],
                "deviation_detail": row[1],
                "solution": row[2],
                "response": row[3],
                "category": row[4],
            }
            for row in rows
        ]
    finally:
        conn.close()

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
    name: str = "retrieval_tool"
    description: str = (
        "根据当前偏离情况描述，从历史案例库中检索相似的偏离案例和处理方案。"
        "在判断负偏离的处理方案时调用，为 Agent 提供参考依据。"
        "知识库为空时返回空结果，不影响 Agent 继续工作。"
    )
    args_schema: type[BaseModel] = RetrievalToolInput

    def _run(self, query: str, category: str = "") -> str:
        cases = _search_cases(query, category)

        if not cases:
            return "未找到相似历史案例，请根据专业判断给出处理方案。"

        lines = []
        lines.append(f"找到 {len(cases)} 条相似历史案例，供参考：\n")
        for case in cases:
            lines.append(
                f"【案例】相似度 100%（关键词匹配）\n"
                f"偏离描述：{case['requirement']} | {case['deviation_detail']}\n"
                f"处理方案：{case['solution']}\n"
                f"结果：{case['response']}"
            )
        return "\n\n".join(lines)
