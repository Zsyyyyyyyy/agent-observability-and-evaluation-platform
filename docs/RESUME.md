# 简历与面试表述

## 项目名称

**Regression Lab｜Coding Agent 可观测与评测平台**

## 简历项目描述（可直接使用）

- 设计并实现面向 Coding Agent 的可观测与回归评测平台，使用 Manifest 驱动 Case × Trial × Agent Version 实验，支持真实 Agent 适配、结果复现与版本对比。
- 构建 Docker 隔离执行层：默认禁网、只读根文件系统、capability drop、CPU/内存/PID 限制；结合 Worktree、路径策略与工具 Allow/Deny Policy 限制 Agent 操作边界。
- 实现 JSONL Trace、SQLite + JSONL Run Store 与 6 类自动评测器（测试、Diff、路径、Trace、工具、预算），将每次 Trial 的模型调用、工具调用、测试、Git Diff 和评分 Evidence 串联为可审计 Artifact。
- 建立 8 个确定性修复基准并完成 `react-agent-v1/v2` 对照实验：两版本均 8/8 通过，v2 平均耗时降低 19%，同时识别到工具调用与 Token 成本上升，形成基于数据的版本决策结论。

## 面试时的三个重点

1. **为什么不只看测试是否通过？** 测试通过不能证明 Agent 修改范围合法、调用可控或成本合理；所以评测输出同时保留 Diff、Policy、Trace 和预算证据。
2. **为什么 Docker 默认失败而非自动退回宿主机？** 退回会改变安全语义，可能把不受信任的 Agent 命令放到本机；必须由使用者显式选择受信任宿主机模式。
3. **如何避免把一次实验说成优化？** 固定模型和策略、用相同 Case 对照、记录 Artifact；当前只跑 1 Trial，因此只称为观察到的权衡，后续以多 Trial 中位数与方差决定晋级。

## 不应夸大的点

- 当前是本地单机 MVP，不具备多租户、远程队列、鉴权或云端大规模调度。
- 实验样本量仍小，不能声称模型能力有统计显著提升。
- `react-agent` 是参考实现；平台的价值在 Adapter 契约与评测闭环，而非绑定单一模型供应商。
