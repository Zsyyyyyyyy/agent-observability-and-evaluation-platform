# 阶段 9：V3 → V4.1 受控复验清单

## 目的

验证 `bounded-success-stop-verify-v4-1` 是否能在测试成功后由运行时确定性结束，消除 V4 预实验中成功后的额外模型循环，同时不降低修复质量。

## 控制变量

- Baseline：`external-openai-v3` / `targeted-context-verify-v3`
- Candidate：`external-openai-v4.1` / `bounded-success-stop-verify-v4-1`
- 相同模型、采样参数、外部 Agent 代码、工具白名单、Sandbox、Case、Evaluator、预算和交错配对调度
- 唯一干预：V4.1 在**平台精确验证命令退出码为 0**后结束该 Trial；验证失败不截断

## 执行规模

三个高信息量 Case，各 3 次、两个版本：共 **18 条真实 Trial**。

```bash
cd "$(git rev-parse --show-toplevel)"
set -a; source .env; set +a
make external-v4-1-preexperiment
```

输出必须是新的目录 `.runtime/external-openai-v3-v4-1-preexperiment/`；不覆盖既有 V3/V4 结果。运行前由 Protocol schema v2 冻结 V4.1 的独立 Prompt Hash。

## 验收规则

1. 每条 V4.1 成功验证的 Trace 应以 `agent.stop: verification_passed_policy` 结束，且之后没有新的 `model.call` 或 `tool.call`。
2. Candidate 的平台有效通过率不得低于 Baseline，且不新增 Agent、Trace、路径策略、差异策略或预算失败。
3. 检查平均与 P95 Token/时延；重点核对原异常 Case `profile_v1_migration_trial_003` 的后续调用是否被截断。
4. 若出现 `model_failed` 或 `infra_failed`，将该配对标为外部受污染证据，不归因为 V4.1，也不自动重跑。
5. Gate 与统计报告分别输出：Gate 决定是否可晋级；小样本仍只能陈述为预实验，不作全量性能宣称。
