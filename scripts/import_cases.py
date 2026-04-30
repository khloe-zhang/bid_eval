"""
偏离案例数据导入脚本
将 knowledge/ 目录下的 Excel 文件导入到 SQLite deviation_cases 表

用法:
    python scripts/import_cases.py

Excel 列名映射:
    招标要求 → requirement
    投标响应 → response
    偏离情况 → deviation_type
    偏离说明 → deviation_detail
    处理方案 → solution
    类别     → category
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

# 项目根目录，加入 sys.path 以便导入 src 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.bid_eval.tools.retrieval_tool import _ensure_schema, _get_connection, DB_FILE


COLUMN_MAP = {
    "招标要求": "requirement",
    "投标响应": "response",
    "偏离情况": "deviation_type",
    "偏离说明": "deviation_detail",
    "处理方案": "solution",
    "类别": "category",
}

# Excel 文件列名（中文）→ SQLite 字段名
EXCEL_COLUMNS = list(COLUMN_MAP.keys())


def find_excel_files(knowledge_dir: Path):
    """遍历目录查找所有 .xlsx 文件（排除 ~$ 开头的临时文件）"""
    if not knowledge_dir.exists():
        return []
    return [
        f for f in knowledge_dir.iterdir()
        if f.suffix.lower() == ".xlsx" and not f.name.startswith("~$")
    ]


def read_excel_cases(excel_path: Path) -> list[dict]:
    """
    读取 Excel 文件，返回偏离案例记录列表。
    表头在第 3 行（header=2），跳过统计摘要行。
    """
    df = pd.read_excel(excel_path, header=2)

    # 只保留有中文列名的数据行
    df = df[df["序号"].apply(lambda x: str(x).isdigit() if pd.notna(x) else False)]

    records = []
    for _, row in df.iterrows():
        record = {}
        for cn, en in COLUMN_MAP.items():
            val = row.get(cn, "")
            record[en] = str(val) if pd.notna(val) else ""
        records.append(record)

    return records


def import_file(excel_path: Path) -> int:
    """将单个 Excel 文件导入 SQLite，返回插入记录数"""
    records = read_excel_cases(excel_path)
    if not records:
        print(f"  警告：未从 {excel_path.name} 读取到有效数据，跳过")
        return 0

    conn = _get_connection()
    try:
        cursor = conn.cursor()
        for rec in records:
            cursor.execute(
                """
                INSERT INTO deviation_cases
                    (requirement, response, deviation_type, deviation_detail, solution, category)
                VALUES
                    (:requirement, :response, :deviation_type, :deviation_detail, :solution, :category)
                """,
                rec,
            )
        conn.commit()
        return len(records)
    finally:
        conn.close()


def main():
    knowledge_dir = PROJECT_ROOT / "knowledge"

    excel_files = find_excel_files(knowledge_dir)
    if not excel_files:
        print("未在 knowledge/ 目录下找到 Excel 文件")
        return

    total = 0
    for f in excel_files:
        print(f"导入：{f.name}")
        count = import_file(f)
        print(f"  成功导入 {count} 条记录")
        total += count

    # 验证
    conn = _get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM deviation_cases")
        db_count = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"\n完成，共导入 {total} 条，当前数据库共 {db_count} 条记录")


if __name__ == "__main__":
    main()
