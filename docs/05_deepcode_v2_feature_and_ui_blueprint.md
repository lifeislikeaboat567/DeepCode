# DeepCode V2 功能与界面蓝图

> 版本：v2.0-draft  
> 日期：2026-03-17  
> 输入文档：04_openclaw_claude_code_feature_research.md

---

## 一、产品目标

DeepCode V2 从“可对话的代码助手”升级为“可执行、可治理、可扩展的工程 Agent 平台”。

### 1.1 目标结果

- CLI 成为主生产力入口（可计划、可执行、可审查、可恢复）。
- WebUI 成为控制平面（任务、会话、工件、策略、系统状态统一可视化）。
- Agent 具备可扩展工具生态（MCP + Skill + Plugin）。
- 系统具备可治理能力（权限策略、审批、审计、回放）。

---

## 二、V2 功能清单（按优先级）

### 2.1 P0（必须）

| ID | 功能 | 说明 |
|---|---|---|
| V2-CLI-01 | CLI 会话管理 | list/resume/rename/export/clear |
| V2-CLI-02 | Plan Mode | 只读分析并生成可编辑计划 |
| V2-CLI-03 | Run Pipeline | plan -> code -> test -> review 可追踪执行 |
| V2-WEB-01 | 任务中心 | 任务队列、状态流、失败重试、步骤日志 |
| V2-WEB-02 | 会话中心 | 会话历史、分支会话、上下文占用 |
| V2-WEB-03 | 工件中心 | 代码产物、测试报告、Diff、下载 |
| V2-SYS-01 | 运行诊断 | 环境检查、依赖检查、命令入口检查 |
| V2-SAFE-01 | 权限策略 | allow/ask/deny 规则与默认模式 |
| V2-OBS-01 | 可观测性 | 结构化日志、任务时间线、错误面板 |

### 2.2 P1（重要）

| ID | 功能 | 说明 |
|---|---|---|
| V2-EXT-01 | MCP 接入 | mcp add/list/remove/auth + 作用域管理 |
| V2-EXT-02 | Hook 事件 | PreToolUse/PostToolUse/TaskCompleted 等关键事件 |
| V2-EXT-03 | Skill 机制 | 本地技能目录、启停、版本与参数 |
| V2-AGT-01 | 多 Agent 团队 | Planner/Coder/Reviewer/Tester 协作视图 |
| V2-AGT-02 | Worktree 并行 | 并行任务隔离与自动清理 |
| V2-WEB-04 | 权限与审批面板 | 待审批操作、历史决策、一键回滚策略 |
| V2-WEB-05 | 规则与记忆面板 | 项目规则、会话记忆、压缩摘要 |

### 2.3 P2（增强）

| ID | 功能 | 说明 |
|---|---|---|
| V2-ENTER-01 | 多租户策略 | 团队/项目级策略继承 |
| V2-ENTER-02 | 审计报表 | 操作审计导出与风险巡检 |
| V2-OPS-01 | 成本与配额 | 模型成本统计、预算上限 |
| V2-OPS-02 | 自动化编排 | 定时任务、Webhook 触发链路 |

---

## 三、CLI 信息架构（目标）

### 3.1 命令分层

- deepcode doctor：环境与配置诊断
- deepcode chat：交互对话
- deepcode plan：只读计划生成
- deepcode run：执行任务流水线
- deepcode task：任务管理（list/get/stream/retry/cancel）
- deepcode session：会话管理（list/resume/rename/export/clear/fork）
- deepcode policy：权限策略（show/set/check）
- deepcode mcp：MCP 管理
- deepcode hook：Hook 规则管理
- deepcode ui：启动 Web 控制台
- deepcode serve：启动 API

### 3.2 CLI 关键体验规则

- 所有长任务必须支持流式进度。
- 所有失败必须输出可执行修复建议。
- 所有命令支持 json 输出模式，便于脚本集成。

---

## 四、WebUI 信息架构（目标）

### 4.1 页面结构

- 首页 Dashboard
- Task Center（任务中心）
- Sessions（会话中心）
- Artifacts（工件中心）
- Agents（Agent 团队）
- Integrations（MCP/Skill/Plugin）
- Policies（权限与审批）
- Settings（模型、路径、系统设置）

### 4.2 核心页面设计要点

#### Dashboard

- 今日任务吞吐、成功率、平均耗时、失败原因 TopN
- 最近活动流（task/session/tool）

#### Task Center

- 左侧任务列表，右侧任务时间线
- 步骤级日志、工具调用轨迹、失败重试入口
- 支持筛选：状态、标签、时间、模型

#### Sessions

- 会话树（主线 + 分支）
- 上下文占用与压缩标记
- 快速恢复与命名

#### Artifacts

- 文件差异视图（before/after）
- 测试报告与覆盖率
- 一键导出补丁/报告

#### Policies

- 规则表（allow/ask/deny）
- 实时待审批队列
- 审批历史可回放

---

## 五、关键交互流

### 5.1 从需求到交付

1. 用户在 CLI 或 Web 提交需求。  
2. 系统进入 Plan Mode 产出计划。  
3. 用户确认计划后执行流水线。  
4. 失败时进入 Reviewer/Reflect 回路。  
5. 完成后输出工件、报告、会话快照。

### 5.2 高风险操作审批

1. 触发高风险工具调用。  
2. 策略引擎命中 ask/deny。  
3. Web/CLI 审批面板弹出。  
4. 用户决策后继续或终止。  
5. 决策写入审计日志。

---

## 六、非功能要求（NFR）

- 可用性：关键命令成功启动率 >= 99%（本地开发场景）
- 性能：任务状态流首屏 <= 2s
- 可靠性：任务失败可恢复率 >= 90%
- 可观测性：关键路径 100% 有结构化日志
- 安全：高风险工具调用 100% 经过策略检查

---

## 七、API 扩展（V2）

- GET /api/v1/tasks?status=&page=
- POST /api/v1/tasks/{id}/retry
- POST /api/v1/tasks/{id}/cancel
- GET /api/v1/sessions/{id}/timeline
- POST /api/v1/sessions/{id}/fork
- GET /api/v1/artifacts/{task_id}
- GET /api/v1/policies
- POST /api/v1/policies/validate
- GET /api/v1/integrations/mcp

---

## 八、落地原则

1. 先稳定基础运行链路，再做炫技功能。  
2. 一切功能都要可观测、可回放、可审计。  
3. CLI 与 Web 共用同一任务与会话模型，避免双轨实现。  
4. 扩展机制优先标准化（MCP/Hook/Skill），避免硬编码集成。
