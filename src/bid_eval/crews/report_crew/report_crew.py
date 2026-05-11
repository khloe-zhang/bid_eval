from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List

from src.bid_eval.config import llm
from src.bid_eval.model import ReportData

@CrewBase
class ReportCrew:
    """报告生成 Crew：将偏离分析结果整理为规范偏离表并输出 Excel"""

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config  = "config/tasks.yaml"

    @agent
    def report_generator(self) -> Agent:
        return Agent(
            config=self.agents_config["report_generator"], 
            llm=llm,
            #tools=[ExcelWriterTool()],  # Excel 写入移到了 app.py 里直接调用
            verbose=False,
            max_iter=2,
            respect_context_window=True,
        )

    @task
    def generate_report_task(self) -> Task:
        return Task(
            config=self.tasks_config["generate_report_task"], 
            output_pydantic=ReportData,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
            tracing=True,
        )
