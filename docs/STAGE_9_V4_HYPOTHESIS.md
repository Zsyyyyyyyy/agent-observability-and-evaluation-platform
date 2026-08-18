# 阶段 9：V4 候选假设（基于扩展 Benchmark Trace）

## 结论先行

`external-openai-v3` 是下一轮的**基线版本**，而不是要被立即替换的失败版本。最新扩展 Benchmark 的 18 条真实 Trial 证明 V3 在本组复杂任务上保持正确性，并避免了 V2 的一次工具预算耗尽；但 3 个 Case 的样本不足以证明它整体更快、更省 Token 或更少调用工具。

V4 只测试一个控制策略假设：**在指定测试成功后，立即结束；平台拒绝一次工具调用后，不重复探索被拒路径，也不读取构建产物。** 该策略只通过新的 Prompt Profile 表达，不改变模型、工具白名单、Sandbox、Case、预算、Evaluator 或 Gate。

## 证据范围

- Artifact：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/`
- 对照：`external-openai-v2`（baseline）与 `external-openai-v3`（candidate）
- 工作负载：`dependency_cycle_detection`、`batch_partial_failure_isolation`、`profile_v1_migration`
- 设计：3 Case × 3 Trial × 2 Version = 18 条真实 Trial；交错执行；Protocol Fingerprint 为 `sha256:ee19a468c601a61bc0baf48f84e8a341f3993d77ec07b46fca4fab764669ba89`；报告比较等级为 `strict`。

本文件只解释已固化 Artifact；不修改该实验目录，也不把其中的性能点估计提升为广泛结论。

## 观察到的事实

| 维度 | V2 | V3 | 解读 |
|---|---:|---:|---|
| 平台有效通过 | 8/9 | 9/9 | V2 有一条 Agent 级预算失败；V3 无模型、基础设施或 Trace 失败。|
| All-pass@3 Case | 2/3 | 3/3 | V3 在此工作负载上稳定性更好。|
| 平均延迟 | 25.36s | 19.06s | 点估计偏向 V3，但 Case 聚类 95% 区间跨零。|
| 平均 Token | 22,032 | 18,232 | 点估计偏向 V3，但 Case 聚类 95% 区间跨零。|
| 平均工具调用 | 10.78 | 10.00 | 点估计偏向 V3，但 Case 聚类 95% 区间跨零。|
| 重复工具调用率 | 35.6% | 36.1% | 两版均偏高，不能宣称 V3 已解决冗余工具行为。|
| 重复读取率 | 12.8% | 11.1% | 有轻微改善，但样本不足以做泛化判断。|
| 被拒工具调用 | 11 | 7 | 两版都会碰撞受限边界。|

性能统计使用 Case 聚类 Bootstrap（2,000 resamples）。V3 − V2 的 95% 区间为：延迟 `[-4459ms, +1313ms]`、Token `[-3240, +3736]`、工具调用 `[-1.0, +1.33]`。三项均为 `inconclusive`，且报告明确标注仅 3 个 eligible Case，不能作广泛性能声明。

## 关键 Trace 诊断

最清晰的反例是 `profile_v1_migration` 的 V2 Trial 002：Agent 已修改 `src/loader.py`，并且测试通过，随后仍继续：

1. 重复运行同一测试命令；
2. 重复读取 `tests/test_loader.py`，并重复 `glob`；
3. 尝试写入受保护的 `tests/test_loader.py`；
4. 读取二进制 `tests/__pycache__/test_loader.cpython-311.pyc` 后再尝试写入；
5. 第 19 次工具调用超过 `max_tool_calls=18`，由平台标记为 `agent_budget_exceeded`。

该 Trial 的平台测试、路径策略、Diff 和 Trace 均是有效的；失败来源不是模型或基础设施，而是 Agent 在“已经获得足够成功证据”后继续扩张行动。其 Trace 位于：

`.runtime/external-openai-v2-v3-benchmark-v2-rerun/baseline/profile_v1_migration/profile_v1_migration_trial_002/attempts/attempt_001/trace.jsonl`

V3 在相同 Case 的三条 Trial 均通过，但仍存在重复 glob、重复 bash 和少量被拒 bash 调用。因此 V4 解决的是一个**可观测的剩余风险**，不是对 V3 做没有证据的全面重写。

## V4 的单变量假设

### 假设

在 `targeted-context-verify-v3` 的原有行为基础上补充“成功停止 / 拒绝回退”规则，能在不降低平台有效通过率的前提下，降低复杂修复任务中的尾部工具调用、被拒工具调用和预算超限风险。

### V4 Prompt Profile 草案

`success-stop-verify-v4` 应在 V3 提示后追加以下语义：

- 平台指定测试首次成功后，立即给出最终结果，不再调用工具；
- 工具被拒绝后，将该路径视为不可用：不要重试相同受限操作、不要转而修改测试或其缓存文件；
- 不要读取或修改 `__pycache__`、`.pyc` 等构建产物；
- 只有测试失败且错误信息直接指向源码缺陷时，才继续一次有针对性的源码检查或修复。

这是一项 Prompt Profile 变更，不是平台的强制截断机制。平台现有路径策略、工具白名单、预算和独立测试仍保持不变，以便评估“Agent 是否更好地遵守策略”。

## V4.1：预实验后的受控修正

V3 → V4 预实验显示，纯 Prompt 的“成功后停止”并不稳定：`profile_v1_migration_trial_003` 在测试已经通过后仍继续发起 `read_file`、`bash` 和 `edit_file`，使 Candidate 达到 16 次工具调用、49,876 Token。这是 Trial Trace 中可复现的行为证据。

V4.1 保留模型、工具、Sandbox、Case、预算和 Evaluator；唯一新增的是 Profile 绑定的运行时结束条件：当且仅当平台提供的精确验证命令退出码为 0，Agent 立即以 `verification_passed` 结束，不再把成功结果送回模型开启下一轮调用。验证失败仍正常交给模型处理。

这让“成功即结束”成为 Trace 可审计的控制边界。后续实验必须以新版本名、Prompt Hash 和独立 Artifact 比较 V3 → V4.1。

## 明确不做的事情

- 不同时更换模型、温度、工具、预算或 Case；
- 不把 `write_file` 从工具集移除；这会混入工具能力变化，且需要另行评估；
- 不因为一次 V2 异常就降低全局工具预算；
- 不宣称 V4 一定更快、更省 Token；这些是待测结果；
- 不用本轮 3 Case 的点估计替代后续完整 Benchmark。

## 建议实验顺序

1. **实现前静态检查**：为 V4 Profile 增加单元测试，确保只有 `external-openai-v4` 使用新提示，V2/V3 文本不变。
2. **预实验（需模型调用确认）**：V3 vs V4，使用本轮 3 个复杂 Case，各 3 次，共 18 条真实 Trial，新建独立输出目录；冻结新的 Protocol，不与当前 Artifact 混跑。
3. **预实验判定**：
   - 任一版本出现模型或基础设施失败：先按脱敏诊断处理，结论为 `inconclusive`，不把它归因为 V4；
   - V4 出现 Agent/Trace/路径策略失败，或有效通过率低于 V3：停止，不扩大；
   - V4 的预算超限数或被拒工具调用没有下降：保留结果，可选择停止或重新提出假设；
   - 质量不回归、且尾部工具调用/拒绝调用呈改善趋势：进入完整 Benchmark。
4. **完整验证（需再次确认）**：在至少 8 个 Case 上继续每版本 3 次，以配对、交错执行和 Case 聚类置信区间得出结论；Gate 负责发布决策，统计报告负责性能措辞。

## 面试可讲的故事

平台没有把“一次模型成功”或“小样本均值”当作版本升级理由：它从 Trial → Trace 定位到成功后的冗余探索与受限路径碰撞，提出一个不改变模型/工具的单变量控制策略，并要求先通过独立预实验和完整 Benchmark 验证。这正是可观测性数据转化为可审计 Agent 演进决策的闭环。
