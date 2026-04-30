from crewai import Agent, Crew, Process, Task, Memory
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.tools import BaseTool
from crewai.rag.embeddings.types import ProviderSpec
from crewai.rag.embeddings.factory import build_embedder
from typing import List
from pathlib import Path

from src.bid_eval.config import DATA_DIR, llm, memory_llm, KNOWLEDGE_DIR, BAILIAN_API_KEY, BAILIAN_BASE_URL
from src.bid_eval.model import RequirementItemList, ResponseItemList, DeviationItemList
from src.bid_eval.tools.retrieval_tool import RetrievalTool

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 通过 pre-built callable 创建自定义 embedder 
embedder = build_embedder({"provider": "openai", "config": {"model_name": "text-embedding-v4"}})


def _create_rag_tool() -> BaseTool:
    """创建 RAG Tool 从 knowledge/ 文件夹加载 Word 和 PDF 文档作为知识库"""
    from crewai_tools import RagTool

    kb_path = Path(KNOWLEDGE_DIR)
    if not kb_path.exists():
        return None

    # 过滤掉以 ~$ 开头的临时文件（Word 锁文件）
    docx_files = [f for f in kb_path.glob("*.docx") if not f.name.startswith('~$')]
    pdf_files = list(kb_path.glob("*.pdf"))

    if not docx_files and not pdf_files:
        return None

    # 使用唯一的 collection 名称，避免被其他实例覆盖
    collection_name = "bid_eval_knowledge"

    rag_tool = RagTool(
        collection_name=collection_name,
        summarize=False,
        config={
            "vectordb": {
                "provider": "chromadb",
                "config": {
                    "collection_name": collection_name,
                }
            },
            "embedding_model": {
                "provider": "openai",
                "config": {
                    "model_name": "text-embedding-v4",
                    "api_key": BAILIAN_API_KEY,
                    "api_base": BAILIAN_BASE_URL,
                },
            },
        }
    )

    # 直接添加文件路径
    for f in docx_files:
        rag_tool.add(path=str(f), data_type="file")
    for f in pdf_files:
        rag_tool.add(path=str(f), data_type="file")

    return rag_tool


@CrewBase
class AnalysisCrew():
    """分析Crew：负责从招标/投标文件中提取需求、响应，并进行偏离分析"""

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    project_id: str = ""          # 声明为类字段，由外部赋值
    _rag_tool: BaseTool | None = None


    @classmethod # 这个 classmethod 是为了确保整个 Crew 生命周期内只创建一个 RagTool 实例，避免重复加载知识库
    def _get_rag_tool(cls) -> BaseTool | None:
        if cls._rag_tool is None:
            cls._rag_tool = _create_rag_tool()
        return cls._rag_tool

    @agent
    def tender_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config["tender_extractor"],  # type: ignore[index]
            llm=llm,
            #tools=tools,
            verbose=False,
            respect_context_window=True,
            max_iter=3,
        )

    @agent
    def bid_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config["bid_extractor"],  # type: ignore[index]
            llm=llm,
            #tools=tools,
            verbose=False,
            respect_context_window=True,
            max_iter=3,
        )

    @agent
    def deviation_analyzer(self) -> Agent:
        # 只有分析Agent需要RAG Tool来查询知识库，提取Agent和响应Agent不需要，所以只在这里获取RAG Tool
        rag_tool = self._get_rag_tool()
        tools = [rag_tool, RetrievalTool()] if rag_tool else [RetrievalTool()]
        return Agent(
            config=self.agents_config["deviation_analyzer"],
            llm=llm,
            tools=tools,
            verbose=True,
            respect_context_window=True,
            max_iter=3,
        )

    @task
    def extract_requirements_task(self) -> Task:
        return Task(
            config=self.tasks_config["extract_requirements_task"],
            #output_pydantic=list[RequirementItem], 
            output_pydantic=RequirementItemList,
        )

    @task
    def extract_responses_task(self) -> Task:
        return Task(
            config=self.tasks_config["extract_responses_task"],
            #output_pydantic=ResponseItem,
            output_pydantic=ResponseItemList,
        )

    @task
    def analyze_deviations_task(self) -> Task:
        return Task(
            config=self.tasks_config["analyze_deviations_task"],
            #output_pydantic=DeviationItem,
            output_pydantic=DeviationItemList,
        )

    @crew
    def crew(self) -> Crew:
        # 全局共享的 Memory 实例
        project_memory = Memory(
            embedder=embedder,
            llm=memory_llm,
            storage=str(DATA_DIR / "memory" / self.project_id),
        )

        # 用 project_id 创建专属 scope 视图
        #project_memory = base_memory.scope(f"/projects/{self.project_id}")

        """创建分析Crew，顺序执行：提取需求 → 提取响应 → 偏离分析"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            tracing=True,
            #memory=True, 如果是True，会用CrewAI默认的 LLM（GPT）创建一个 Memory，这里我们需要自定义 embedder 和 llm，所以直接传 Memory 实例
            #memory = Memory(embedder=embedder, llm=memory_llm) 全局记忆，不区分项目
            memory = project_memory,  # 项目专属记忆
        )
