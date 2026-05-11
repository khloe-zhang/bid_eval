
# BidEval — 招投标偏离表智能比对系统

[![Python](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://python.org)
[![CrewAI](https://img.shields.io/badge/CrewAI-1.14.2-FF6B6B.svg)](https://crewai.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.45-FF4B4B.svg)](https://streamlit.io)
[![License: Apache](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## 功能介绍

BidEval 是一个基于 CrewAI 多 Agent 协作的评标辅助系统。用户上传招标文件和投标文件后，系统自动完成文档解析、条款比对与偏离分析，生成结构化的偏离表 Excel 报告，包含条款编号、招标需求、投标响应、偏离情况、整改措施，帮助乙方投标团队节省人工核查时间，提升效率。

## 核心流程

```
文件上传  →  文档解析  →  CrewAI 分析  →  人工审核(HITL)  →  Excel 报告生成
   ↓            ↓               ↓              ↓                ↓
 upload    parse_documents  run_analysis  review_deviations generate_report
```

- **文件上传**：支持 `.docx`、`.pdf`、图片（`.png`/`.jpg`）混合上传
- **CrewAI 分析**：多 Agent 协作链路，支持 RAG 知识库检索，输出结构化偏离条目
- **人工审核**：Web 界面展示偏离明细，支持人工批准或要求修改，触发重分析
- **Excel 报告**：生成含颜色标注的偏离表，支持下载

## 核心特性

### 多 Agent 协作链路

 `AnalysisCrew` 由三个 Agent 组成：

| Agent                  | 角色           | 职责                                  |
| :--------------------- | -------------- | ------------------------------------- |
| `tender_extractor`   | 招标文件提取器 | 从招标文档中解析需求条目              |
| `bid_extractor`      | 投标响应映射器 | 将需求条目与投标文件内容对应          |
| `deviation_analyzer` | 偏离分析器     | 判定偏离类型与处理方案，触发 RAG 检索 |

Agent 之间通过 `output_pydantic` 输出结构化 JSON，下游 Agent 接收经过 Pydantic 模型校验过的格式化数据，无需额外解析，确保数据流转时的格式一致性。

`ReportCrew` 包含一个 Agent `report_generator`，负责将技术语言润色成招投标官方语言风格，并且按照要求调用 `ExcelWriterTool` 生成最终表格。

### 记忆与上下文管理

**项目级 Memory**：每个评标项目拥有独立的 Memory 存储目录（`data/memory/{project_id}/`），实现跨项目数据隔离，避免上下文污染。

**双 LLM 架构**：分析任务与记忆操作使用不同模型，避免两类任务在上下文窗口和模型特性上互相干扰。

- 分析 LLM：**GLM-5.1**（支持 Structured Output 结构化输出），负责 Agent 任务执行与偏离推理
- 记忆 LLM：**Qwen3-max**（关闭思考模式，关闭结构化输出），负责 CrewAI Memory 的写入与检索

**上下文串接**：在 Agent 的 tasks.yaml 配置中通过 `context: [previous_task]` 将前序任务的输出作为下游任务的输入，实现有状态的信息流。

**上下文压缩**：利用 CrewAI 原生上下文压缩机制，在 crew.py 中开启 `respect_context_window`，在达到上下文 token 阈值时，触发自动摘要总结。

### 状态管理与持久化

状态机设计：idle → parsing → analyzing → awaiting_review (如触发修改，退回analyzing) → generating → done

`DeviationFlow` 即主流程使用 CrewAI 原生的 Flow 状态管理机制，通过 `@persist` 装饰器实现 Flow 级别的状态持久化。流程状态（当前步骤、解析结果、分析结果）序列化为 JSON 写入 `data/projects/{project_id}/flow_state.json`，支持中断后从断点恢复，下次上传时输入相同的项目名（即project_id）即可从中断的状态继续，避免因网络或页面刷新丢失分析进度。Flow 重构时通过 `DeviationFlow.from_pending(state_id)` 加载历史状态，再以 `flow.resume()` 继续执行。

### 人在回路（Human in the Loop）

CrewAI 提供 @humanfeedback 模块，支持挂起状态等待用户意见。偏离分析完成后，系统通过自定义 `StreamlitHumanFeedbackProvider` 在 Web 界面暂停 Agent 执行，等待人工确认：

- 分析结果先在前端以视觉效果丰富、容易辨识的形式展示，用户可逐条审阅
- 支持**批准**（approved）或要求**修改**（revise），用户可输入修改意见
- 根据用户选择，Flow 通过 `@listen("approved")` / `@listen("revise")` 动态路由到不同分支
- 循环直到最终批准生成表格，结束流程

将 CrewAI 原生的控制台阻塞式反馈改为了非阻塞的 Web 暂停机制，无需修改 CrewAI 核心代码。

### RAG 双层检索

偏离分析器可调用两套检索工具：

**向量知识库（ChromaDB）**：将 `knowledge/` 目录下的 `.docx`、`.pdf` 文档向量化存储于 ChromaDB，检索时将相关文档片段作为上下文注入 Agent Prompt，为偏离判断提供行业标准依据。

**结构化案例库（SQLite）**：历史偏离案例以结构化形式存入 `deviation_cases.db`，通过关键词 LIKE 检索相似历史处理方案，为当前偏离提供参考先例。

## 技术栈

| 层级       | 技术选型                                 |
| ---------- | ---------------------------------------- |
| Agent 框架 | CrewAI 1.14.2（Flow + Crew + Agent）     |
| 主 LLM     | GLM-5.1 (支持 Structured Output)         |
| 记忆 LLM   | Qwen3-max                                |
| 向量嵌入   | text-embedding-v4（OpenAI-compatible）   |
| Web UI     | Streamlit 1.45+                          |
| 向量数据库 | ChromaDB                                 |
| 关系数据库 | SQLite                                   |
| 文档解析   | pdfplumber、python-docx、DeepSeek 多模态 |
| 项目打包   | Hatchling + Docker                       |

## 项目结构

```
bid_eval/
├── app.py                        # Streamlit 前端主入口，UI 状态机
├── src/bid_eval/
│   ├── main.py                   # CrewAI 运行主流程控制，包含 @start, @listen, @humanfeedback 等
│   ├── config.py                 # LLM 配置、目录路径
│   ├── model.py                  # Pydantic 输出结构模型
│   ├── crews/
│   │   ├── analysis_crew/        # AnalysisCrew + agents.yaml + tasks.yaml，定义 Agent 的人设与输出任务
│   │   └── report_crew/          # ReportCrew + agents.yaml + tasks.yaml，用于生成表格
│   ├── parsing/
│   │   ├── file_router.py        # parse_files() 调度器
│   │   ├── docx_parser.py        # Word 文档解析（含合并单元格处理）
│   │   ├── pdf_parser.py         # PDF 解析（pdfplumber + MinerU）
│   │   └── image_parser.py       # 多模态图片处理（DeepSeek）
│   └── tools/
│       ├── retrieval_tool.py      # SQLite 历史案例检索
│       └── excel_writer.py       # 偏离表 Excel 生成
├── data/
│   ├── projects/                  # per-project 文件与 flow_state.json
│   ├── database/
│   │   └── deviation_cases.db     # SQLite 历史案例库
│   └── memory/                    # per-project CrewAI Memory 存储
└── knowledge/                     # RAG 向量知识库源文档
```

## 快速开始

### 环境要求

- Python 3.13
- 阿里云百炼 API Key（GLM-5.1 + Qwen3-max + embedding），也可改用其他模型厂商

### 本地运行

```bash
# 安装依赖
pip install crewai[tools]==1.14.2 streamlit>=1.45.0

# 配置环境变量
cp .env.example .env
# 在项目根目录下创建 .env 填入 BAILIAN_API_KEY、BAILIAN_BASE_URL等配置

# 启动应用
streamlit run app.py --server.port 8501
```

### Docker 运行

```bash
docker build -t bid_eval .
docker run -p 8501:8501 --env-file .env bid_eval
```
