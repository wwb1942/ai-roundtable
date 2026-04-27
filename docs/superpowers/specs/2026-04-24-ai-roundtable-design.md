# AI Roundtable 设计文档

> 本文档为 `ai-roundtable` 的当前设计基线，取代 `2026-04-23-ai-roundtable-design.md` 中的早期草稿方向。当前版本聚焦“多 AI 自动圆桌讨论系统”，默认主输出为“结论 + 建议 + 风险”。

## 1. 产品定义

`ai-roundtable` 是一个多 AI 自动圆桌讨论系统，不是聊天室，也不是单次 prompt 包装器。

系统目标：

- 让多个 AI participant 围绕同一主题持续讨论。
- 由 orchestrator 主持轮次、管理上下文、处理用户介入。
- 在讨论结束时输出结构化结果，而不是只有聊天记录。

默认主产物：

- `结论`
- `建议`
- `风险`

可升级产物：

- `Actionable Plan Candidate`
- `Task List Candidate`

原则：

- 默认输出 `Roundtable Report`，而不是直接输出 implementation plan。
- 只有在 `plan_ready = true` 时才升级生成 plan 候选。

## 2. 设计目标

本项目第一阶段同时满足三类需求：

- `真实可用`
  能稳定驱动真实 AI CLI 参与讨论。
- `展示效果`
  默认终端可用，tmux 为增强展示层，适合演示和比赛。
- `研究玩法`
  能分析收敛、僵局、分歧、介入效果，并保留可复盘事件日志。

## 3. 总体架构

系统采用四层结构：

```text
┌──────────────────────────────────────────────┐
│                 ai-roundtable                │
├──────────────────────────────────────────────┤
│ Participant Plugins                          │
│ - Claude plugin                              │
│ - Codex plugin                               │
│ - future plugins                             │
├──────────────────────────────────────────────┤
│ Event Log                                    │
│ - JSONL append-only event stream             │
│ - replay / resume / audit                    │
├──────────────────────────────────────────────┤
│ Orchestrator                                 │
│ - round scheduling                           │
│ - state machine                              │
│ - convergence / stalemate detection          │
│ - user intervention                          │
│ - final report generation                    │
├──────────────────────────────────────────────┤
│ Renderer                                     │
│ - TerminalRenderer (default)                 │
│ - TmuxRenderer (enhanced display only)       │
└──────────────────────────────────────────────┘
```

边界约束：

- `Participant Plugins` 负责与具体 AI CLI 交互。
- `Event Log` 是系统事实来源，不允许只靠 stdout 临时拼装状态。
- `Orchestrator` 负责所有业务判断。
- `Renderer` 只负责展示，不负责业务决策。
- `tmux` 不能成为核心依赖；无 tmux 也必须可完整运行。

## 4. Participant Plugin Contract

### 4.1 设计原则

不把“持久会话”定义成模型能力，而定义成 participant plugin 是否提供“会话托管能力”。

系统允许不同 participant 采用不同运行模式：

- `context-managed`
  orchestrator 自己管理上下文，每轮发起一次独立调用。
- `session-managed`
  participant 背后的 CLI/agent 自己维护会话，orchestrator 只追加新输入和控制信息。

### 4.2 能力声明

每个 plugin 必须声明能力：

```python
class ParticipantCapabilities(TypedDict):
    session_mode: Literal["context-managed", "session-managed"]
    interruptible: bool
    structured_status: bool
    supports_resume: bool
    supports_artifacts: bool
```

字段解释：

- `session_mode`
  当前 plugin 是上下文托管还是会话托管。
- `interruptible`
  当前发言过程中能否安全中断。
- `structured_status`
  是否能稳定输出约定的状态块。
- `supports_resume`
  会话中断后能否恢复。
- `supports_artifacts`
  是否支持文件、路径、URL 等材料引用。

### 4.3 统一接口

```python
class ParticipantPlugin(Protocol):
    id: str
    display_name: str
    capabilities: ParticipantCapabilities

    def validate(self) -> None:
        ...

    def start_session(self, topic: str, system_contract: str) -> str | None:
        ...

    def send_turn(
        self,
        session_id: str | None,
        round_context: dict,
    ) -> "ParticipantTurnResult":
        ...

    def interrupt(self, session_id: str | None) -> bool:
        ...

    def resume(self, session_id: str) -> bool:
        ...

    def close_session(self, session_id: str | None) -> None:
        ...
```

行为约束：

- `validate()` 在启动前验证 CLI 或运行环境是否可用。
- `start_session()` 仅在需要时创建会话；`context-managed` 可返回 `None`。
- `send_turn()` 是唯一的发言入口。
- `interrupt()` 和 `resume()` 允许失败，但必须显式返回结果。
- `close_session()` 用于释放资源。

### 4.4 单轮返回结构

```python
class ParticipantTurnResult(TypedDict):
    content: str
    raw_output: str
    status: Literal["converging", "diverging", "stalemate", "unknown"]
    status_summary: str | None
    artifacts: list[str]
    error: str | None
    duration_ms: int
```

要求：

- `content` 用于讨论展示和历史记录。
- `raw_output` 用于调试、审计、排错。
- `status` 和 `status_summary` 服务于轮次分析。
- `artifacts` 存放文件、路径、URL 等引用，不把大材料直接塞进讨论正文。
- `error` 用于记录单轮故障。

## 5. Orchestrator

### 5.1 核心职责

orchestrator 是主持人，不是简单循环器。它负责：

- 初始化会话
- 决定轮次顺序
- 注入用户输入
- 读取 participant 输出
- 判断继续、暂停、总结或终止
- 生成最终报告

### 5.2 会话级状态机

```text
ready
  -> running
  -> waiting_for_user
  -> summarizing
  -> completed
  -> failed
```

状态说明：

- `ready`
  所有配置与 participant 校验完成，准备开始。
- `running`
  正在自动轮转讨论。
- `waiting_for_user`
  因僵局、主持模式、用户主动介入或降级而暂停。
- `summarizing`
  已判定结束，正在生成结果报告。
- `completed`
  正常结束。
- `failed`
  因 participant 不足、配置错误、连续故障等无法继续。

### 5.3 轮次级流程

每一轮固定为四个阶段：

1. `prepare_round`
   - 生成 round context
   - 决定发言顺序
   - 注入轮次间隙的用户输入
2. `collect_responses`
   - 调用各 participant plugin
   - 逐个记录输出事件
3. `analyze_round`
   - 解析状态块
   - 必要时退化到启发式分析
   - 产出本轮判定
4. `decide_next_action`
   - 继续下一轮
   - 进入用户介入
   - 开始总结
   - 强制结束

### 5.4 讨论完成判定

采用“硬规则 + 软信号”。

硬规则：

- 达到 `max_rounds`
- 可用 participant 少于 2 个
- 用户显式要求结束
- 连续两轮 `converging`
- 连续两轮 `stalemate`

软信号：

- 最近两轮不同 AI 的结论趋同
- 新信息显著减少
- 参与者反复复述既有立场

规则约束：

- 软信号不能单独结束会话，只能作为辅助信号。
- `degraded` 场景下不得伪装成正常收敛。

## 6. 状态检测

### 6.1 主路径：结构化状态块

要求 participant 在回复末尾输出专用标记：

```text
<<<ROUNDTABLE_STATUS>>>
{"status": "converging", "summary": "双方趋向同一方案"}
<<<END_STATUS>>>
```

解析规则：

- 只解析两个标记之间的内容。
- 标记外的 JSON 一律忽略。
- 标记缺失或内部 JSON 非法则视为解析失败。

### 6.2 回退路径：启发式分析

在结构化状态不可用时，orchestrator 采用启发式判断：

- 同一 participant 与前一轮相比是否重复论点
- 不同 participant 最近回复是否趋同
- 是否连续多轮没有新增有效信息

判定原则：

- 回退路径只作辅助，不直接替代全部业务规则。
- 启发式命中需要连续触发才生效。

## 7. 上下文管理

### 7.1 为什么不能无限重放

多 AI 讨论会快速增长上下文长度。无限全量重放会带来：

- token 和成本失控
- 调用时延增加
- 重点信息被淹没
- session 被无关噪声污染

### 7.2 统一上下文结构

每轮上下文由四部分组成：

1. `System Contract`
   - 角色与讨论规则
   - 状态块格式
   - 输出行为约束
2. `Session Summary`
   - 历史摘要
   - 当前共识
   - 当前分歧
   - 用户约束
3. `Recent Window`
   - 最近 `N` 轮完整对话
4. `Current Turn Instruction`
   - 本轮发言者
   - 本轮目标
   - 是否推进、反驳、整合、总结

### 7.3 历史压缩

默认策略：

- 保留最近 3 轮完整原文
- 更早历史压缩成结构化摘要

摘要格式固定：

```text
[Roundtable Summary]
- Topic:
- Current consensus:
- Open disagreements:
- User constraints:
- Important proposals:
```

约束：

- 摘要优先结构化，避免自由散文。
- 即使使用 `session-managed` participant，orchestrator 仍维护可见摘要与事实状态。

## 8. 用户介入模型

用户永远保留主持权。

支持三种介入：

- `旁注`
  在轮次间隙注入一条补充观点。
- `主持指令`
  例如 `继续`、`暂停`、`总结`、`结束`、`@claude ...`、`@codex ...`。
- `强制中断`
  用户中断当前 subprocess 或 participant 轮次。

规则：

- 默认在轮次间隙处理用户输入。
- 若 plugin `interruptible = true`，可尝试安全中断。
- 若 plugin `interruptible = false`，则等待当前轮结束后处理。
- 用户中断不计入 participant 连续失败次数。

## 9. Event Log

### 9.1 原则

JSONL append-only 事件流是系统事实来源。

它服务于：

- 回放
- 恢复
- 调试
- 审计
- 研究分析

### 9.2 事件类型

至少包含以下事件：

- `session_started`
- `participant_validated`
- `round_started`
- `turn_prompted`
- `turn_completed`
- `turn_failed`
- `user_intervened`
- `state_detected`
- `session_degraded`
- `session_summarized`
- `session_completed`

### 9.3 示例

```json
{"type":"session_started","topic":"Should we rewrite the CLI core?","timestamp":"2026-04-24T04:00:00Z"}
{"type":"turn_completed","round":1,"participant":"claude","status":"diverging","timestamp":"2026-04-24T04:00:10Z"}
{"type":"turn_completed","round":1,"participant":"codex","status":"diverging","timestamp":"2026-04-24T04:00:18Z"}
{"type":"user_intervened","round":2,"content":"团队只有 3 个人","timestamp":"2026-04-24T04:01:00Z"}
```

### 9.4 持久化用途

事件日志必须支持：

- 断点恢复
- 赛后回顾
- 研究“何时收敛/何时卡住”
- 比较不同 plugin 与主持策略效果

## 10. Renderer

### 10.1 TerminalRenderer

默认模式，跨平台、最小依赖。

职责：

- 展示主题、参与者、轮次、状态
- 展示 participant 回复
- 接受用户输入

### 10.2 TmuxRenderer

增强模式，适合 demo、比赛、现场展示。

职责：

- 分屏展示对话流
- 展示状态面板
- 提供控制台输入区域

约束：

- TmuxRenderer 是增强展示层，不参与业务判断。
- 无 tmux 时必须完整退化到 TerminalRenderer。

## 11. 最终输出契约

### 11.1 默认输出：Roundtable Report

每场讨论结束后都要生成：

- `Markdown Report`
- `JSON Structured Result`

Markdown 至少包含：

- `Topic`
- `Participants`
- `End Reason`
- `Final Conclusion`
- `Recommendations`
- `Risks`
- `Open Questions`
- `Remaining Disagreements`
- `Confidence`
- `Plan Readiness`

### 11.2 JSON 结果结构

```json
{
  "topic": "是否采用 Rust 重写 CLI 核心",
  "participants": ["Claude", "Codex"],
  "end_reason": "stalemate",
  "final_conclusion": "暂无统一结论，倾向分阶段迁移方案",
  "recommendations": [
    "先做 profiling",
    "只对热点模块做 PoC"
  ],
  "risks": [
    "团队 Rust 经验不足",
    "重写成本被低估"
  ],
  "open_questions": [
    "性能目标是否明确",
    "是否接受双语言维护"
  ],
  "dissent": [
    "是否应该立即重写核心模块"
  ],
  "confidence": "medium",
  "plan_ready": false
}
```

### 11.3 结束原因影响输出风格

- `converged`
  可输出明确结论与执行导向建议。
- `stalemate`
  重点输出分歧、风险、人类决策点。
- `max_rounds`
  必须明确“截止但未完全收敛”。
- `degraded`
  必须标注为阶段性判断，而非圆桌共识。

## 12. Plan 生成条件

plan 不是默认主产物，而是可升级产物。

### 12.1 `plan_ready = true` 的条件

满足任意一种即可：

- 用户显式要求生成 plan
- 讨论已足够收敛，且无关键阻塞问题
- 当前 roundtable 运行在“决策转计划”模式

### 12.2 `plan_ready = false` 的行为

必须输出：

- 当前不能形成 plan 的原因
- 剩余阻塞问题
- 需要用户补充的信息

示例：

```text
Plan Readiness: not ready

Blockers:
- 关键执行目标未统一
- 成本约束缺失
- 技术路线仍有核心分歧
```

## 13. 错误处理与降级

错误分为四类：

- `adapter_unavailable`
- `call_timeout`
- `call_failed`
- `user_interrupted`

处理规则：

- `adapter_unavailable`
  启动前剔除，不满足最小参与者数则无法进入自动圆桌。
- `call_timeout`
  本轮记失败，可重试一次。
- `call_failed`
  非零退出码、空输出、无法解析等，本轮记失败。
- `user_interrupted`
  不计入 participant 失败，直接转入用户介入。

连续失败规则：

- 同一 participant 连续 3 次失败则移除。
- 剩余 participant 少于 2 个则标记 `degraded` 并暂停自动圆桌。

`degraded` 后必须交给用户选择，不允许单 AI 自动产生“圆桌共识”：

- `终止并总结`
  结束自动圆桌，输出当前阶段性报告。
- `转人工主持`
  用户充当另一方，剩余 participant 继续提供分析。
- `等待恢复`
  暂停会话，等待故障 participant 恢复后继续。

## 14. 配置与扩展

建议采用 `yaml` 配置：

```yaml
participants:
  - id: claude
    plugin: claude
  - id: codex
    plugin: codex

settings:
  max_rounds: 12
  convergence_threshold: 2
  stalemate_threshold: 2
  context_window: 3
  call_timeout: 120
  renderer: terminal
  output_mode: report
```

扩展原则：

- 新 participant 通过 plugin 注册，而不是改 orchestrator 主逻辑。
- renderer 可以扩展，但不能侵入业务状态判断。
- 可增加新的报告模板，但不能破坏默认 `Roundtable Report` 契约。

## 15. 跨平台策略

### 15.1 核心原则

- 核心编排纯 Python subprocess，Windows/macOS/Linux 均可运行
- tmux 展示层可选，无 tmux 完整退化到 TerminalRenderer
- 路径处理使用 `pathlib` 自动适配

### 15.2 WSL 环境

WSL 下可直接调用 Windows 侧已安装的 CLI，无需重复安装：

- Claude: `claude.cmd` 或 `/mnt/c/Users/<user>/AppData/Roaming/npm/claude.cmd`
- Codex: `codex.cmd` 或 `/mnt/c/Users/<user>/AppData/Roaming/npm/codex.cmd`

注意事项：

- 路径自动转换（`/mnt/c/...` ↔ `C:\...`）
- 显式 UTF-8 编码，避免中文乱码
- plugin 配置中可通过 `command_override` 指定 WSL 下的替代命令

### 15.3 TmuxRenderer 布局

```text
┌─────────────────┬─────────────────┐
│   对话流         │   状态面板       │
│                 │                 │
│ [Claude] ...    │ 轮次: 3/12      │
│ [Codex] ...     │ 状态: 收敛中     │
│                 │ 共识点: 2       │
│                 │ 分歧点: 1       │
├─────────────────┴─────────────────┤
│ > 控制台 (输入指令或介入讨论)        │
└───────────────────────────────────┘
```

启用方式：`--renderer tmux`，不自动检测，避免在共享服务器上意外创建 tmux session。

## 16. Mention 解析器

独立模块，从用户输入中解析 `@` 指令用于路由：

```python
def parse_mentions(text: str, known_participants: list[str]) -> list[str]
```

- 识别 `@claude`、`@codex` 等已注册 participant id
- 过滤 email 地址等误匹配（`user@example.com`）
- 用于人工主持模式中 `@claude 你怎么看` 的定向发言路由
- 无 mention 时视为广播，所有 participant 参与下一轮

## 17. 未来扩展：MCP 接口

V1 以 orchestrator 主持为核心，不把 MCP 作为主路径。MCP 放到 V2，避免第一阶段被 room/broker 基础设施拖慢。

V2 可考虑暴露：

- `roundtable.send_message`
  participant 或外部 agent 主动写入圆桌消息。
- `roundtable.read_history`
  participant 或外部 agent 主动读取事件历史。
- `roundtable.get_state`
  查询当前轮次、状态、分歧点和报告草稿。

设计约束：

- MCP server 复用 Event Log，不另建第二套事实来源。
- MCP 只扩展参与方式，不替代 Orchestrator 的主持权。
- 只有当 V1 的事件日志、状态机、报告契约稳定后再进入 V2。

## 18. 第一阶段非目标

V1 不做以下内容：

- Web UI
- 远程 broker / 多人在线协作
- 复杂权限系统
- 自动执行任务链
- 高级长期记忆编排
- 大规模 participant marketplace

## 19. 测试策略

采用 TDD 方式，测试嵌入每个实现步骤。推荐实现顺序：

- `Task 1 - mention parser`
  先测已知 participant 提取、去重、email 过滤、广播行为。
- `Task 2 - event log`
  先测 append、read、read_after、resume 所需事件回放。
- `Task 3 - participant plugin`
  先测 validate、start_session、send_turn、status 解析，用 mock subprocess 或 fake plugin。
- `Task 4 - orchestrator`
  先测轮转、状态机、收敛判定、僵局判定、最大轮数、degraded 用户选项。
- `Task 5 - renderer`
  先测 TerminalRenderer 输出格式，再手动验收 TmuxRenderer。
- `Task 6 - report generator`
  先测 Markdown/JSON 双格式输出、`plan_ready` 和不同 `end_reason` 的报告差异。

验收分三层：

- `单元测试`
  覆盖解析器、状态机、检测器、事件日志和报告生成。
- `集成测试`
  使用 fake participant plugins 跑完整 lifecycle、degrade、interrupt、summary。
- `手动验收`
  使用真实 Claude/Codex 跑 terminal 模式、tmux 模式和最终报告验证。

## 20. 推荐的 V1 成功标准

V1 达标条件：

- 至少支持两个官方 plugin：Claude、Codex
- 能自动轮转 3 到 10 轮
- 能处理用户介入
- 能检测 `continue / converging / stalemate / degraded`
- 能写入并回放 JSONL 事件日志
- 能生成 Markdown + JSON 双格式结果
- Terminal 模式完整可用
- tmux 模式可作为增强展示
