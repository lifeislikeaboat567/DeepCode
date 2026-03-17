# DeepCode Agent：功能设计与技术路线

> 版本：v1.0  
> 日期：2026-03-17  
> 基于：《AI Agent 调研报告》

---

## 一、项目愿景

DeepCode Agent 是一个面向软件工程师的 **AI 编程助手系统**，能够理解自然语言需求，自主规划开发任务，生成、执行、测试并迭代代码，最终交付可运行的软件产品。

**核心价值主张**：
- 🧠 **深度理解**：理解代码库上下文，而非单纯的代码补全
- 🔄 **闭环执行**：规划→编码→测试→修复全流程自动化
- 🛡️ **安全可控**：关键步骤人工确认，代码执行沙箱隔离
- 🔌 **开放集成**：标准化 REST API，支持 IDE 插件扩展

---

## 二、核心功能点

### 2.1 任务规划模块（Planning）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| PLN-01 | 任务分解 | 将自然语言需求分解为有序子任务列表 | P0 |
| PLN-02 | 依赖分析 | 识别子任务间的依赖关系，构建 DAG | P1 |
| PLN-03 | 资源估算 | 估算完成任务所需的工具、时间、API 调用次数 | P2 |
| PLN-04 | 计划修订 | 执行过程中根据反馈动态调整计划 | P1 |
| PLN-05 | 里程碑设置 | 自动设置阶段性检查点，支持暂停/继续 | P2 |

### 2.2 代码生成模块（Code Generation）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| GEN-01 | 函数生成 | 根据描述生成函数实现 | P0 |
| GEN-02 | 类/模块生成 | 生成完整的类定义和模块骨架 | P0 |
| GEN-03 | API 生成 | 根据接口描述生成 REST/GraphQL API | P1 |
| GEN-04 | 测试生成 | 为代码自动生成单元测试 | P0 |
| GEN-05 | 文档生成 | 生成 Docstring 和 API 文档 | P1 |
| GEN-06 | 重构建议 | 分析代码并提供重构方案 | P2 |

### 2.3 代码执行模块（Execution）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| EXE-01 | Python 执行 | 在隔离环境中执行 Python 代码 | P0 |
| EXE-02 | Shell 命令 | 执行 Shell 命令（白名单管控） | P0 |
| EXE-03 | 测试运行 | 自动运行 pytest/unittest 测试套件 | P0 |
| EXE-04 | 错误捕获 | 捕获执行错误并触发自动修复流程 | P0 |
| EXE-05 | 沙箱隔离 | 通过进程隔离防止恶意代码执行 | P0 |
| EXE-06 | 资源限制 | CPU/内存/时间限制 | P1 |

### 2.4 文件系统模块（File Management）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| FS-01 | 文件读写 | 读取和写入项目文件 | P0 |
| FS-02 | 代码库扫描 | 扫描项目目录，构建文件树 | P0 |
| FS-03 | 差异对比 | 生成并展示文件变更 diff | P1 |
| FS-04 | Git 集成 | 自动提交、分支管理、PR 创建 | P2 |
| FS-05 | 模板管理 | 管理项目模板和代码片段库 | P2 |

### 2.5 搜索与检索模块（Search & Retrieval）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| SRC-01 | 网络搜索 | 检索技术文档和解决方案 | P1 |
| SRC-02 | 代码语义搜索 | 在代码库中进行语义相似度搜索 | P1 |
| SRC-03 | 文档检索（RAG） | 从本地文档库检索相关内容 | P1 |
| SRC-04 | 包文档查询 | 查询 PyPI/NPM 包的 API 文档 | P2 |

### 2.6 记忆管理模块（Memory）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| MEM-01 | 对话历史 | 维护多轮对话上下文 | P0 |
| MEM-02 | 项目上下文 | 记录当前项目的技术栈和约定 | P0 |
| MEM-03 | 长期知识库 | 向量化存储历史解决方案 | P1 |
| MEM-04 | 会话持久化 | 保存和恢复会话状态 | P1 |
| MEM-05 | Token 管理 | 自动压缩超长上下文 | P1 |

### 2.7 多智能体协作模块（Multi-Agent）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| MA-01 | Orchestrator | 任务分配与整体协调 | P0 |
| MA-02 | Coder Agent | 专注代码生成与实现 | P0 |
| MA-03 | Reviewer Agent | 代码审查与安全检查 | P1 |
| MA-04 | Tester Agent | 测试生成与执行 | P1 |
| MA-05 | Researcher Agent | 搜索与文档检索 | P2 |

### 2.8 用户接口模块（User Interface）

| 功能 ID | 功能名称 | 描述 | 优先级 |
|---------|----------|------|--------|
| UI-01 | CLI 交互 | 命令行对话界面 | P0 |
| UI-02 | REST API | 标准化 HTTP API | P0 |
| UI-03 | Web UI | Streamlit 网页界面 | P1 |
| UI-04 | 流式输出 | 实时展示 Agent 思考过程 | P1 |
| UI-05 | 进度面板 | 可视化任务执行进度 | P2 |

---

## 三、技术架构设计

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     用户界面层                                │
│  ┌──────────┐   ┌──────────┐   ┌─────────────────────────┐  │
│  │  CLI     │   │  Web UI  │   │      REST API           │  │
│  │ (Rich)   │   │(Streamlit│   │      (FastAPI)           │  │
│  └────┬─────┘   └────┬─────┘   └──────────┬──────────────┘  │
└───────┼──────────────┼────────────────────┼─────────────────┘
        └──────────────┴────────────────────┘
                               │
┌─────────────────────────────▼─────────────────────────────┐
│                    Agent 编排层                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Orchestrator Agent                      │   │
│  │         (LangGraph StateGraph)                      │   │
│  └──┬──────────┬──────────┬──────────┬─────────────────┘   │
│     │          │          │          │                       │
│  ┌──▼──┐   ┌──▼──┐   ┌──▼──┐   ┌──▼──────┐               │
│  │Coder│   │Tester│  │Review│  │Researcher│               │
│  │Agent│   │Agent │  │Agent │  │  Agent   │               │
│  └─────┘   └──────┘  └──────┘  └──────────┘               │
└────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────▼─────────────────────────────┐
│                       工具层                                 │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │
│  │ Code   │ │  File  │ │  Web   │ │  Shell │ │  Git   │  │
│  │Executor│ │Manager │ │ Search │ │  Tool  │ │  Tool  │  │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘  │
└────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────▼─────────────────────────────┐
│                     记忆与存储层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 短期记忆      │  │  长期向量库   │  │   会话持久化      │  │
│  │(In-Memory)   │  │  (ChromaDB)  │  │   (SQLite)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────▼─────────────────────────────┐
│                      LLM 接入层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  OpenAI API  │  │  Anthropic   │  │  Local (Ollama)  │  │
│  │  (GPT-4o)    │  │  (Claude)    │  │  (Llama/Mistral) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 3.2 技术栈选型

#### 核心依赖

| 类别 | 选型 | 版本要求 | 选型理由 |
|------|------|----------|----------|
| Python | Python | ≥ 3.11 | asyncio 支持，类型提示完善 |
| Agent 框架 | LangChain | ≥ 0.3 | 生态最丰富，工具集成完善 |
| Agent 编排 | LangGraph | ≥ 0.2 | 图结构适合复杂工作流 |
| LLM 接口 | openai | ≥ 1.0 | 标准 API，可对接多种模型 |
| API 框架 | FastAPI | ≥ 0.110 | 异步、自动文档、类型安全 |
| 数据验证 | Pydantic | ≥ 2.0 | 类型安全的配置与数据模型 |
| 向量数据库 | ChromaDB | ≥ 0.5 | 轻量级，无需独立服务 |
| 关系数据库 | SQLite | 内置 | 零配置，适合本地持久化 |
| ORM | SQLAlchemy | ≥ 2.0 | 异步支持，迁移管理 |
| Web UI | Streamlit | ≥ 1.30 | 快速构建 AI 应用界面 |
| CLI | Click + Rich | ≥ 8.0 | 美观的命令行体验 |
| 测试框架 | pytest | ≥ 8.0 | 异步测试、fixture 支持 |
| 代码质量 | Ruff | ≥ 0.4 | 超快 linter + formatter |
| 类型检查 | mypy | ≥ 1.10 | 静态类型验证 |

### 3.3 Agent 状态机设计

使用 LangGraph 定义 Agent 执行图：

```python
# 状态定义
class AgentState(TypedDict):
    messages: list[BaseMessage]
    task: str
    plan: list[str]
    current_step: int
    code_context: dict
    tool_results: list[dict]
    final_answer: str | None
    error: str | None

# 节点定义
nodes = {
    "planner": plan_task,       # 任务规划
    "coder": generate_code,     # 代码生成
    "executor": execute_code,   # 代码执行
    "reviewer": review_code,    # 代码审查
    "tester": run_tests,        # 测试运行
    "reflector": reflect,       # 反思修正
}

# 边定义（条件跳转）
edges = {
    "planner": ["coder"],
    "coder": ["executor"],
    "executor": {
        "success": "reviewer",
        "error": "reflector",
    },
    "reviewer": {
        "pass": "tester",
        "fail": "coder",
    },
    "tester": {
        "pass": END,
        "fail": "reflector",
    },
    "reflector": ["coder"],
}
```

### 3.4 数据模型设计

```python
# 核心数据模型

class Task(BaseModel):
    id: str
    description: str
    status: TaskStatus  # pending/running/completed/failed
    created_at: datetime
    steps: list[TaskStep]

class TaskStep(BaseModel):
    id: str
    type: StepType  # plan/code/execute/review/test
    input: str
    output: str | None
    status: StepStatus
    duration_ms: int | None

class CodeArtifact(BaseModel):
    filename: str
    content: str
    language: str
    created_at: datetime
    version: int

class Session(BaseModel):
    id: str
    name: str
    messages: list[Message]
    artifacts: list[CodeArtifact]
    project_context: dict
```

---

## 四、技术路线规划

### 4.1 阶段划分

```
Phase 1: 基础架构 (Week 1-2)
    ├── 项目脚手架
    ├── LLM 接入层
    ├── 基础 Agent（单智能体 ReAct）
    └── CLI 接口

Phase 2: 核心工具 (Week 3-4)
    ├── 代码执行工具
    ├── 文件管理工具
    ├── 记忆管理
    └── REST API

Phase 3: 多智能体 (Week 5-6)
    ├── LangGraph 编排
    ├── Coder + Tester Agent
    ├── Reviewer Agent
    └── 流式输出

Phase 4: Web UI & 集成 (Week 7-8)
    ├── Streamlit Web UI
    ├── 向量记忆（ChromaDB）
    ├── 会话持久化
    └── 完整测试覆盖
```

### 4.2 质量保障策略

1. **测试驱动**：新功能先写测试，测试覆盖率 ≥ 80%
2. **类型安全**：全量 Pydantic 模型 + mypy 检查
3. **代码规范**：Ruff 自动格式化 + 类型注解
4. **安全审计**：代码执行白名单 + 沙箱隔离
5. **可观测性**：结构化日志 + 执行追踪

---

*文档结束*
