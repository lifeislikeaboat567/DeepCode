# DeepCode Agent 软件开发文档

> 版本：v1.0  
> 日期：2026-03-17  
> 状态：执行中

---

## 一、文档目的

本文档是 DeepCode Agent 项目的权威开发指南，规定了所有开发阶段的：
- 具体任务清单（Definition of Work）
- 交付物（Deliverables）
- 验收标准（Acceptance Criteria）
- 技术规范（Technical Specifications）

所有开发工作须严格遵循本文档，变更须经版本更新流程。

---

## 二、项目概述

### 2.1 项目信息

| 项目名称 | DeepCode Agent |
|----------|----------------|
| 代码仓库 | github.com/lifeislikeaboat567/DeepCode |
| 开发语言 | Python 3.11+ |
| 许可证 | MIT |
| 目标平台 | Linux / macOS / Windows |

### 2.2 系统边界

**包含**：
- Agent 核心引擎（规划、生成、执行、反思）
- 工具套件（代码执行、文件管理、搜索）
- 记忆管理系统
- REST API 服务
- CLI 命令行界面
- Web UI 界面

**不包含**：
- LLM 模型本身（通过 API 调用）
- IDE 插件（未来扩展）
- 云服务部署（未来扩展）

---

## 三、开发阶段规划

### Phase 1：基础架构搭建

**时间**：Week 1-2  
**目标**：建立项目骨架，实现基础单智能体对话

#### 任务列表

| 任务 ID | 任务描述 | 负责模块 | 验收标准 |
|---------|----------|----------|----------|
| P1-T01 | 初始化 Python 项目（pyproject.toml） | 根目录 | `pip install -e .` 成功 |
| P1-T02 | 配置管理（环境变量 + Pydantic Settings） | `deepcode/config.py` | 可加载 .env 配置 |
| P1-T03 | 实现 LLM 客户端抽象层 | `deepcode/llm/` | 支持 OpenAI/Mock 两种后端 |
| P1-T04 | 实现基础 ReAct Agent | `deepcode/agents/base.py` | 能完成简单 Q&A 任务 |
| P1-T05 | 实现 CLI 入口（click + rich） | `deepcode/cli.py` | `deepcode chat` 命令可用 |
| P1-T06 | 建立测试框架（pytest + 异步支持） | `tests/` | `pytest` 命令可运行 |
| P1-T07 | 配置 Ruff linter 与 mypy | `pyproject.toml` | `ruff check .` 无错误 |

**Phase 1 交付物**：
```
deepcode/
├── __init__.py
├── config.py          # 配置管理
├── cli.py             # CLI 入口
├── llm/
│   ├── __init__.py
│   ├── base.py        # LLM 抽象基类
│   └── openai_client.py  # OpenAI 实现
└── agents/
    ├── __init__.py
    └── base.py        # 基础 ReAct Agent
tests/
├── conftest.py
└── test_base_agent.py
pyproject.toml
.env.example
```

**Phase 1 验收标准**：
- [ ] `pip install -e ".[dev]"` 成功安装所有依赖
- [ ] `deepcode --help` 显示帮助信息
- [ ] `deepcode chat` 可与 Agent 进行对话（需 API Key）
- [ ] `pytest tests/` 测试全部通过
- [ ] `ruff check .` 无错误

---

### Phase 2：核心工具实现

**时间**：Week 3-4  
**目标**：完善工具套件，实现代码执行、文件管理、记忆系统，提供 REST API

#### 任务列表

| 任务 ID | 任务描述 | 负责模块 | 验收标准 |
|---------|----------|----------|----------|
| P2-T01 | 实现代码执行工具（进程隔离+超时） | `deepcode/tools/code_executor.py` | 能安全执行 Python 代码 |
| P2-T02 | 实现 Shell 命令工具（白名单） | `deepcode/tools/shell_tool.py` | 支持安全 Shell 命令 |
| P2-T03 | 实现文件管理工具（读写+树扫描） | `deepcode/tools/file_manager.py` | 能读写项目文件 |
| P2-T04 | 实现短期记忆管理 | `deepcode/memory/short_term.py` | 多轮对话上下文保持 |
| P2-T05 | 实现长期记忆（ChromaDB） | `deepcode/memory/long_term.py` | 能存储和检索向量 |
| P2-T06 | 实现 FastAPI REST API | `deepcode/api/` | OpenAPI 文档可访问 |
| P2-T07 | 会话持久化（SQLite） | `deepcode/storage/` | 会话可保存和恢复 |
| P2-T08 | 工具集成测试 | `tests/` | 工具覆盖率 ≥ 80% |

**Phase 2 交付物**：
```
deepcode/
├── tools/
│   ├── __init__.py
│   ├── base.py           # 工具基类
│   ├── code_executor.py  # 代码执行
│   ├── shell_tool.py     # Shell 工具
│   └── file_manager.py   # 文件管理
├── memory/
│   ├── __init__.py
│   ├── short_term.py     # 短期记忆
│   └── long_term.py      # 长期向量记忆
├── storage/
│   ├── __init__.py
│   └── session_store.py  # 会话持久化
└── api/
    ├── __init__.py
    ├── app.py            # FastAPI 应用
    ├── routes/
    │   ├── chat.py       # 对话路由
    │   ├── sessions.py   # 会话管理
    │   └── health.py     # 健康检查
    └── models.py         # API 数据模型
```

**Phase 2 验收标准**：
- [ ] `deepcode run "hello world in python"` 能生成并执行代码
- [ ] `deepcode serve` 启动 API 服务，`/docs` 可访问
- [ ] `GET /api/v1/health` 返回 200
- [ ] `POST /api/v1/chat` 能进行对话
- [ ] 代码执行超时（默认 30s）自动终止
- [ ] 测试覆盖率 ≥ 80%

---

### Phase 3：多智能体编排

**时间**：Week 5-6  
**目标**：实现基于 LangGraph 的多智能体协作系统

#### 任务列表

| 任务 ID | 任务描述 | 负责模块 | 验收标准 |
|---------|----------|----------|----------|
| P3-T01 | 设计 Agent 状态图（LangGraph） | `deepcode/agents/graph.py` | 状态图可视化可导出 |
| P3-T02 | 实现 Orchestrator Agent | `deepcode/agents/orchestrator.py` | 能分解复杂任务 |
| P3-T03 | 实现 Coder Agent | `deepcode/agents/coder.py` | 能生成可运行代码 |
| P3-T04 | 实现 Reviewer Agent | `deepcode/agents/reviewer.py` | 能发现代码问题 |
| P3-T05 | 实现 Tester Agent | `deepcode/agents/tester.py` | 能生成并运行测试 |
| P3-T06 | 实现流式输出（SSE） | `deepcode/api/streaming.py` | 流式响应可用 |
| P3-T07 | Human-in-the-loop 机制 | `deepcode/agents/hitl.py` | 关键步骤可暂停 |
| P3-T08 | 多智能体集成测试 | `tests/integration/` | 端到端测试通过 |

**Phase 3 交付物**：
```
deepcode/
├── agents/
│   ├── base.py
│   ├── graph.py         # LangGraph 状态图
│   ├── orchestrator.py  # 编排 Agent
│   ├── coder.py         # 编码 Agent
│   ├── reviewer.py      # 审查 Agent
│   ├── tester.py        # 测试 Agent
│   └── hitl.py          # Human-in-the-loop
└── api/
    └── streaming.py     # SSE 流式输出
tests/
└── integration/
    ├── test_multi_agent.py
    └── test_coding_workflow.py
```

**Phase 3 验收标准**：
- [ ] `deepcode run "实现一个计算器类"` 能完整完成：规划→编码→测试→交付
- [ ] 流式输出：Agent 思考过程实时显示
- [ ] 代码审查：自动检测明显的代码问题
- [ ] 测试覆盖率 ≥ 85%
- [ ] 端到端工作流在 2 分钟内完成

---

### Phase 4：Web UI 与产品完善

**时间**：Week 7-8  
**目标**：完成 Web UI，完善所有功能，达到可交付标准

#### 任务列表

| 任务 ID | 任务描述 | 负责模块 | 验收标准 |
|---------|----------|----------|----------|
| P4-T01 | 实现 Streamlit Web UI | `deepcode/ui/` | Web UI 可访问 |
| P4-T02 | UI：对话界面 | `deepcode/ui/pages/chat.py` | 流式对话可用 |
| P4-T03 | UI：代码编辑器组件 | `deepcode/ui/components/` | 语法高亮显示 |
| P4-T04 | UI：任务进度面板 | `deepcode/ui/pages/tasks.py` | 任务状态可见 |
| P4-T05 | UI：会话管理 | `deepcode/ui/pages/sessions.py` | 历史会话可查看 |
| P4-T06 | 网络搜索工具集成 | `deepcode/tools/web_search.py` | 支持技术文档搜索 |
| P4-T07 | Docker 化 | `Dockerfile`, `docker-compose.yml` | Docker 部署成功 |
| P4-T08 | 完整文档（README） | `README.md` | 包含安装和使用说明 |
| P4-T09 | 性能优化（并发请求） | 全局 | API 响应 P99 < 5s |
| P4-T10 | 安全加固审计 | 全局 | 无高危安全漏洞 |
| P4-T11 | 全量测试覆盖 | `tests/` | 覆盖率 ≥ 85% |

**Phase 4 交付物**：
```
deepcode/
└── ui/
    ├── __init__.py
    ├── app.py              # Streamlit 主应用
    ├── pages/
    │   ├── chat.py
    │   ├── tasks.py
    │   └── sessions.py
    └── components/
        ├── code_viewer.py
        └── progress.py
Dockerfile
docker-compose.yml
README.md (完整版)
```

**Phase 4 验收标准**：
- [ ] `deepcode ui` 启动 Web UI，浏览器可访问
- [ ] `docker-compose up` 完整启动所有服务
- [ ] README 包含：安装、配置、快速开始、API 文档链接
- [ ] 测试覆盖率 ≥ 85%
- [ ] 无 P0/P1 级别的安全漏洞

---

## 四、代码规范

### 4.1 目录结构规范

```
deepcode/          # 主包
├── __init__.py    # 版本信息
├── config.py      # 配置（单例）
├── cli.py         # CLI 入口
├── agents/        # Agent 实现
├── tools/         # 工具实现
├── memory/        # 记忆管理
├── storage/       # 持久化存储
├── api/           # REST API
├── ui/            # Web UI
└── utils/         # 工具函数
tests/
├── conftest.py    # 公共 fixture
├── unit/          # 单元测试
├── integration/   # 集成测试
└── fixtures/      # 测试数据
docs/              # 文档
```

### 4.2 编码规范

1. **类型注解**：所有公共函数必须有完整类型注解
2. **Docstring**：所有公共类和函数必须有 Docstring（Google style）
3. **异步优先**：I/O 操作使用 `async/await`
4. **错误处理**：所有异常使用自定义异常类，禁止裸 `except`
5. **日志**：使用 `structlog` 结构化日志，禁止 `print`
6. **配置**：所有配置通过 `deepcode.config.Settings` 访问

### 4.3 测试规范

1. 单元测试：`tests/unit/test_<module>.py`
2. 集成测试：`tests/integration/test_<workflow>.py`
3. 使用 `pytest-asyncio` 处理异步测试
4. LLM 调用必须 Mock（使用 `pytest-mock`）
5. 每个公共函数至少一个测试
6. 边界条件和错误路径必须有测试

### 4.4 提交规范

```
<type>(<scope>): <subject>

type: feat/fix/refactor/test/docs/chore
scope: agents/tools/memory/api/ui/cli
subject: 动词开头，现在时，不超过 50 字符
```

---

## 五、API 规范

### 5.1 REST API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/chat` | 发送消息 |
| GET | `/api/v1/chat/stream` | 流式对话（SSE） |
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{id}` | 获取会话 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| POST | `/api/v1/tasks` | 创建任务 |
| GET | `/api/v1/tasks/{id}` | 获取任务状态 |
| GET | `/api/v1/tasks/{id}/stream` | 任务流式进度 |

### 5.2 核心请求/响应格式

```json
// POST /api/v1/chat
{
  "message": "写一个 Python 冒泡排序函数",
  "session_id": "uuid-optional",
  "stream": false
}

// Response
{
  "session_id": "uuid",
  "message": "...",
  "code_artifacts": [
    {
      "filename": "bubble_sort.py",
      "content": "def bubble_sort(arr): ...",
      "language": "python"
    }
  ],
  "steps": [...]
}
```

---

## 六、环境配置

### 6.1 必需环境变量

```bash
# LLM 配置
DEEPCODE_LLM_PROVIDER=openai          # openai | anthropic | ollama
DEEPCODE_LLM_MODEL=gpt-4o-mini        # 模型名称
DEEPCODE_LLM_API_KEY=sk-...           # API Key

# 服务配置
DEEPCODE_API_HOST=0.0.0.0
DEEPCODE_API_PORT=8000
DEEPCODE_DEBUG=false

# 存储配置
DEEPCODE_DATA_DIR=~/.deepcode         # 数据目录
DEEPCODE_DB_URL=sqlite:///deepcode.db # 数据库 URL
```

### 6.2 可选环境变量

```bash
# 搜索工具
DEEPCODE_SEARCH_API_KEY=             # Tavily/SerpAPI Key

# 安全配置
DEEPCODE_MAX_EXECUTION_TIME=30       # 代码执行超时（秒）
DEEPCODE_ALLOWED_SHELLS=ls,cat,grep,find,python,pip

# 记忆配置
DEEPCODE_MAX_HISTORY_MESSAGES=50     # 最大对话历史
DEEPCODE_VECTOR_COLLECTION=deepcode  # ChromaDB 集合名
```

---

## 七、风险与应对

| 风险 | 严重程度 | 概率 | 应对策略 |
|------|----------|------|----------|
| LLM API 限流 | 高 | 中 | 实现退避重试（exponential backoff） |
| 代码执行安全 | 严重 | 低 | 进程隔离 + 命令白名单 + 超时控制 |
| 上下文超长 | 中 | 高 | 自动摘要 + 滑动窗口 |
| LLM 幻觉 | 中 | 高 | 执行验证 + 反思循环 |
| 依赖版本冲突 | 低 | 中 | 固定依赖版本 + CI 测试 |

---

*文档结束 - DeepCode Agent v1.0*
