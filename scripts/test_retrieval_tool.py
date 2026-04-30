"""
测试 deviation_analyzer 的 RetrievalTool 是否被正常调用
"""
from crewai import Agent, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from src.bid_eval.crews.analysis_crew.analysis_crew import AnalysisCrew

# 构造一个最简单的 context，只包含偏离分析需要的输入
fake_requirements = [
    {"item": "服务器主频", "spec": "不低于 2.6GHz"},
    {"item": "内存容量", "spec": "不低于 768GB"},
]
fake_responses = [
    {"item": "服务器主频", "response": "实际配备 2.4GHz"},
    {"item": "内存容量", "response": "实际配备 512GB"},
]

project_id = "test_retrieval_tool"

# 构造偏离任务描述
task_description = f"""你是一个偏离分析专家。以下是招标要求和投标响应，请分析偏离情况：

招标要求：
{chr(10).join([f"- {r['item']}: {r['spec']}" for r in fake_requirements])}

投标响应：
{chr(10).join([f"- {r['item']}: {r['response']}" for r in fake_responses])}

请识别所有偏离项，对于负偏离，给出处理方案。
"""

# 初始化 Crew 并获取 deviation_analyzer agent
crew_instance = AnalysisCrew()
crew_instance.project_id = project_id

# 获取 deviation_analyzer agent
analyzer = crew_instance.deviation_analyzer()

# 构造任务
task = Task(
    description=task_description,
    agent=analyzer,
    expected_output="以 JSON 格式输出偏离分析结果，包含偏离项列表及处理方案",
)

print("开始测试 deviation_analyzer...\n")
result = analyzer.execute_task(task=task)

print("\n===== Agent 输出结果 =====")
print(result)
