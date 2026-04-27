# AI Roundtable 设计文档

## 概述

一个跨平台的多 AI 对话编排工具，让多个 AI CLI（Claude、Codex 等）围绕话题进行讨论，自动检测收敛与僵局，支持用户随时介入。

## 技术选型

- 语言：Python >= 3.9
- 方案：混合模式（subprocess 核心编排 + 可选 tmux 展示层）
- 依赖：pyyaml + Python 标准库

## 架构

三层解耦：

```
┌─────────────────────────────────────┐
│           ai-roundtable             │
├──────────┬──────────────────────────┤
│ 核心编排层 │  Orchestrator            │
│          │  - 管理对话轮转            │
│          │  - 调用各 AI CLI           │
│          │  - 维护对话历史            │
│          │  - 收敛/僵局检测           │
├──────────┼──────────────────────────┤
│ AI 适配层 │  Adapter (每个 AI 一个)    │
│          │  - Claude: claude --print │
│          │  - Codex: codex exec      │
│          │  - 可扩展: 配置文件添加     │
├──────────┼──────────────────────────┤
│ 展示层    │  Renderer (可切换)        │
│          │  - TmuxRenderer: 分屏展示  │
│          │  - TerminalRenderer: 单窗口 │
└──────────┴──────────────────────────┘
```

## 对话流程

### 核心机制：收敛检测 + 僵局检测

```
用户输入话题
    ↓
Orchestrator 发起讨论
    ↓
每轮结束后，分析对话状态：
    ├─ 收敛 → 各方观点趋同，自动总结共识，结束
    ├─ 僵局 → 检测到重复立场，暂停，通知用户介入
    └─ 进行中 → 继续下一轮
```

### 收敛判断

双重检测机制：

**主检测：AI 自报状态。** 在 system prompt 中要求 AI 在回复末尾用专用标记包裹状态 JSON：

```
<<<ROUNDTABLE_STATUS>>>
{"status": "converging", "summary": "双方同意优先考虑开发效率"}
<<<END_STATUS>>>
```

解析规则：
- 只提取 `<<<ROUNDTABLE_STATUS>>>` 和 `<<<END_STATUS>>>` 之间的内容
- 标记外的任何 JSON（代码示例、配置片段等）一律忽略
- 标记缺失或内部 JSON 格式错误 → 视为解析失败，退化到启发式

**备用检测：相似度启发式。** 当 JSON 标记缺失或解析失败时，Orchestrator 用简单启发式判断：
- 对比相邻两轮同一 AI 的回复，如果关键论点重复率高 → 疑似僵局
- 对比不同 AI 最近回复，如果结论趋同 → 疑似收敛
- 启发式结果仅作为信号，连续触发才生效

**判定规则：**
- `converging` 连续 2 轮 → 自动生成共识总结，结束
- `stalemate` 连续 2 轮 → 暂停，提示用户介入
- `diverging` → 正常继续
- JSON 解析失败 → 退化到启发式检测，并在状态面板提示
- 最大轮数保底（默认 20 轮），防止无限循环

### 用户随时介入

不管哪种模式，用户都可以在轮次间隙插话参与讨论：

- **介入时机：** 用户输入在两轮之间生效。当前 AI 的 subprocess 调用是阻塞的，不支持中途打断。每轮结束后、下轮开始前，Orchestrator 检查是否有用户输入排队。
- **TerminalRenderer：** 使用非阻塞 stdin 读取（`select`/`threading`），用户随时输入，内容缓存到队列，在轮次间隙注入。
- **TmuxRenderer：** 底部控制台独立 pane，输入直接写入共享队列。
- **取消当前轮：** 用户按 `Ctrl+C` 可中断当前 subprocess 调用，该轮标记为 `interrupted`（不计入失败次数），立即进入用户介入环节。

用户发言作为 `[用户/主持人]` 注入对话历史，AI 将其视为权威输入调整观点。

### 两种对话模式

**自动收敛模式：** 用户给出话题，AI 自动轮流讨论直到收敛或僵局。

**人工主持模式：** 用户给出话题，AI 各自回复后等待用户指令：
- `继续` → 所有 AI 继续讨论
- `@claude 你怎么看` → 指定某个 AI 回复
- `新话题: xxx` → 切换话题
- `退出` → 结束

## AI 适配层

统一接口：

```python
class AIAdapter:
    name: str           # 显示名，如 "Claude"
    command: str        # CLI 命令，如 "claude"
    args: list          # 参数，如 ["--print"]
    subcommand: str     # 子命令，如 "" 或 "exec"（Codex 需要 codex exec）
    
    def validate() -> bool
        # 启动时检查 CLI 是否可用（which/where 检测）
        # 并执行 smoke test（如 --help）验证子命令可用
    
    def send(history: list[dict], prompt: str) -> str
        # Orchestrator 传入对话历史 + 当前 prompt
        # 内部拼装为完整 prompt 文本后调用 CLI
        # 注意：不同 CLI 的调用方式不同：
        #   Claude: claude --print -p "{prompt}"
        #   Codex:  codex exec "{prompt}"
    
    def parse_status(response: str) -> dict | None
        # 提取 <<<ROUNDTABLE_STATUS>>> 和 <<<END_STATUS>>> 之间的 JSON
        # 标记外的 JSON 一律忽略
        # 解析失败返回 None，由 Orchestrator 退化到启发式检测
```

**Prompt 拼装模板：** Orchestrator 将对话历史格式化后传给 adapter：

```
[系统] 你正在和其他 AI 讨论以下话题：{topic}
[Claude] 第1轮回复...
[Codex] 第1轮回复...
[用户] 用户介入内容...
[当前] 请给出你的观点，并在回复末尾用以下格式附上状态：
<<<ROUNDTABLE_STATUS>>>
{"status": "converging|diverging|stalemate", "summary": "当前共识/分歧点"}
<<<END_STATUS>>>
```

**上下文预算策略：** 对话历史不做无限全量重放，采用滑动窗口 + 摘要机制：
- 默认保留最近 5 轮完整对话
- 超出窗口的历史由 Orchestrator 调用参与 AI 生成一段摘要，替换原始内容
- 摘要格式：`[历史摘要] 前 N 轮讨论要点：...`
- 可通过 `settings.context_window` 配置窗口大小（默认 5）

通过配置文件扩展，不需要改代码：

```yaml
participants:
  - name: Claude
    command: claude
    subcommand: ""
    args: ["--print"]
    
  - name: Codex
    command: codex
    subcommand: "exec"
    args: []

settings:
  max_rounds: 20
  convergence_threshold: 2
  stalemate_threshold: 2
  context_window: 5          # 保留最近 N 轮完整对话，超出部分摘要替换
  call_timeout: 120          # 单次 subprocess 超时（秒）
```

## 展示层

### TerminalRenderer（默认，跨平台）

单窗口聊天流，群聊风格：

```
═══════════════════════════════════
  话题: Python vs Rust 在 CLI 工具中的选择
  参与者: Claude, Codex
═══════════════════════════════════

[第 1 轮]

🔵 Claude:
  我认为 Python 更适合快速原型...

🟢 Codex:
  从性能角度看 Rust 更合适...

👤 用户:
  我们团队只有 3 个人

⚡ 状态: 收敛中

═══════════════════════════════════
  ✅ 共识达成
═══════════════════════════════════
```

### TmuxRenderer（可选增强）

```
┌─────────────────┬─────────────────┐
│   对话流         │   状态面板       │
│                 │                 │
│ [Claude] ...    │ 轮次: 3/20      │
│ [Codex] ...     │ 状态: 收敛中     │
│                 │ 共识点: 2       │
│                 │ 分歧点: 1       │
├─────────────────┴─────────────────┤
│ > 控制台 (输入指令或介入讨论)        │
└───────────────────────────────────┘
```

自动检测：默认使用 TerminalRenderer。通过 `--tmux` 显式启用 TmuxRenderer，`--no-tmux` 显式禁用。未指定时，若检测到 tmux 可用则提示用户选择。

## 项目结构

```
ai-roundtable/
├── main.py              # 入口，解析参数启动
├── orchestrator.py      # 核心编排，轮转+收敛/僵局检测
├── adapter.py           # AI 适配器基类 + 内置适配器
├── mention.py           # @mention 解析器，独立模块
├── store.py             # JSONL 对话历史持久化
├── renderer.py          # TerminalRenderer + TmuxRenderer
├── config.yaml          # AI 参与者 + 设置
└── requirements.txt     # 依赖（pyyaml）
```

## 对话历史持久化

对话历史写入 JSONL 文件，支持断点恢复和事后回顾：

```
data/
└── {topic_slug}_{timestamp}.jsonl
```

每行一条消息：

```json
{"round": 1, "sender": "Claude", "content": "...", "status": "diverging", "timestamp": "2026-04-23T10:00:00Z"}
{"round": 1, "sender": "Codex", "content": "...", "status": "diverging", "timestamp": "2026-04-23T10:00:15Z"}
{"round": 1, "sender": "用户", "content": "...", "timestamp": "2026-04-23T10:01:00Z"}
```

- 启动时检查是否有未完成的对话文件，提示用户继续或新建
- Orchestrator 每轮结束后 append，不做全量重写
- `store.py` 封装读写逻辑，提供 `append()`、`read_all()`、`read_after(round_num)` 接口

## Mention 解析器

独立模块 `mention.py`，从用户输入中解析 `@` 指令：

```python
def parse_mentions(text: str, known_agents: list[str]) -> list[str]
```

- 识别 `@claude`、`@codex` 等已注册 agent 名称
- 过滤 email 地址等误匹配（`user@example.com`）
- 用于人工主持模式中 `@claude 你怎么看` 的路由

## 未来扩展：MCP 接口（v2）

v1 使用 subprocess 调用 CLI（被动模式）。v2 可考虑通过 MCP 协议让 AI 主动参与：

- 暴露 `roundtable.send_message` 和 `roundtable.read_history` 作为 MCP 工具
- AI agent 可以主动读取对话历史、主动发言，而不是等待 Orchestrator 投喂 prompt
- 这将把 AI 从"被编排的工具"升级为"房间里的参与者"
- MCP server 复用 `store.py` 的 JSONL 存储

此扩展不影响 v1 架构，store 层和 mention 解析器可直接复用。

## 启动方式

```bash
# 自动收敛模式
python main.py "Python vs Rust 在 CLI 工具中的选择"

# 人工主持模式
python main.py --manual "如何设计认证系统"

# 指定配置文件
python main.py --config my_config.yaml "话题"

# 指定最大轮数
python main.py --max-rounds 10 "话题"

# 显式启用 tmux 展示
python main.py --tmux "话题"
```

## 错误处理

- **启动检查：** 启动时对所有配置的 AI 执行 `validate()`，不可用的参与者跳过并警告，至少需要 2 个可用 AI 才能开始
- **调用超时：** 每次 subprocess 调用设超时（默认 120 秒，可配置），超时视为该轮跳过
- **空输出/异常退出：** 非零退出码或空输出时，记录错误，跳过该 AI 本轮，下轮继续尝试
- **连续失败：** 同一 AI 连续 3 次失败，自动移除该参与者并通知用户
- **参与者不足：** 运行中参与者降至 1 个时，自动暂停并提示用户选择：(a) 终止讨论并总结当前状态，(b) 转为人工主持模式（用户充当另一方），(c) 等待故障 AI 恢复后继续。不允许单 AI 自行"收敛"产生伪共识
- **编码处理：** 统一 UTF-8，WSL 跨边界调用时显式指定编码

## 跨平台策略

- 核心编排纯 Python subprocess，Windows/macOS/Linux 均可运行
- tmux 展示层可选，无 tmux 自动退化为单窗口聊天流
- WSL 环境下可通过 `claude.cmd` / `codex.cmd` 调用 Windows 侧已安装的 CLI
- 路径处理使用 `os.path` / `pathlib` 自动适配
- WSL 跨边界调用注意事项：路径自动转换（`/mnt/c/...`），显式 UTF-8 编码，使用 `.cmd` 后缀调用 Windows npm 全局包

## 测试策略

采用 TDD 方式，测试嵌入每个实现步骤：

- **Task 1 - mention parser：** 先写测试（已知 agent 提取、去重、email 过滤），再实现
- **Task 2 - store：** 先写测试（append/read/read_after），再实现 JSONL 读写
- **Task 3 - adapter：** 先写测试（validate 检测、send 调用、status 解析），用 mock subprocess
- **Task 4 - orchestrator：** 先写测试（轮转逻辑、收敛判定、僵局判定、最大轮数），用 mock adapter
- **集成测试：** 用 echo-based 假 AI（简单脚本回显输入），测试完整对话流程
- **手动验收：** 用真实 Claude + Codex 跑一轮完整对话，验证端到端体验
