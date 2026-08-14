# ADR-001：legacy agent 采用单 Trial Worker 进程接入

- 状态：Accepted
- 日期：2026-08-10
- 范围：`study/Regression/adapters/readonly_replay/`

## 背景

legacy agent 的模块导入阶段会读取环境变量、初始化全局 Anthropic Client、固定 `Path.cwd()`，并创建任务、Worktree、Mailbox 等目录，同时启动 Cron Scheduler 后台线程。Agent 主循环还会共享消息、MCP、后台任务和定时任务等模块级状态。

如果在同一 Python 进程内连续运行多个 Trial，会产生状态串扰、运行目录复用和后台线程无法可靠清理的问题。

## 决策

每个 Trial 启动一个新的 Worker 进程。Worker 完成以下工作：

1. 接收结构化 Trial 输入。
2. 切换到 Trial 专属 Worktree。
3. 在导入 legacy agent 前注入模型配置和运行目录。
4. 加载 legacy agent 模块并调用 `agent_loop(messages, context)`。
5. 通过 Adapter 包装模型、工具、Hook 和压缩边界。
6. 写出 Result、Trace 和 Artifact 引用。
7. 退出进程，由 Runner 回收资源。

## 结果

优点：

- 每个 Trial 拥有干净的 Python 全局状态。
- AgentVersion、CaseVersion 和运行环境的边界更容易冻结。
- 单个 Trial 崩溃不会污染后续 Trial。
- 后续可将 Worker 放入受限的执行环境。

代价：

- 每个 Trial 都有 Python 启动成本。
- Trace Collector 和结果传递需要进程间文件或 JSON 协议。
- 不能直接复用同一个 Agent 的内存状态。

## 未采用的方案

### 进程内连续调用

不采用。与 legacy agent 的全局模块状态和后台线程模型冲突，无法保证 Trial 隔离。

### 直接运行交互式 CLI

不采用。CLI 依赖 stdin，并且无法稳定返回结构化的 Agent、测试和 Artifact 结果。

### 直接修改 legacy agent 源码增加平台逻辑

不采用。会污染 Baseline，破坏版本比较，并违反 Regression 目录边界。

## 复审条件

只有在后续证明 Worker 启动成本成为主要瓶颈，且可以通过显式生命周期 API 完全重置 legacy agent 全局状态时，才重新评估进程内复用。

