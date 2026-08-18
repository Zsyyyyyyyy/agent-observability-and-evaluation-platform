# 阶段 7：统计可信度增强

阶段 7 的目标是提高版本比较的证据质量，不是执行发布审计，也不自动改变 Gate 或版本状态。

## 实验协议

- 对象：同一框架无关的 `external_openai_agent.py`，比较 `external-openai-v2` 与 `external-openai-v3`；
- 固定变量：模型、工具白名单、Case、Fixture、Evaluator、预算和每 Case 重复次数；
- 样本：8 Case × 3 Trial × 2 Version = 48 条真实 Trial；
- 统计单位：同一 Case、同一 Trial 编号的 v2/v3 配对；模型或基础设施失败仍计入可靠性，不纳入策略效率配对；
- 产物：`.runtime/external-openai-v2-v3-statistical/`，历史 18 Trial 实验保留且不覆盖。

## 统计方法与边界

对 latency、Token、tool calls 计算 Candidate − Baseline 差异。区间使用 **按 Case 聚类 Bootstrap**：每次有放回地重采样 Case，并带回该 Case 的全部有效配对 Trial；固定种子 `20260812`、2,000 次重采样，报告 95% percentile interval。

这避免把同一个 Case 的三次重复错误当作三个独立任务。区间只用于稳定性诊断：

- latency 区间完全小于 0：`observed_latency_improvement`；
- 区间跨 0：`inconclusive`；
- 少于 8 个有效 Case：`limited_coverage`；
- 没有有效配对：`not_available`。

不报告 p-value，不将统计区间直接加入 Promotion Gate；正确性、失败率、Trace 与策略违规仍由既有 Gate 判定。

## 执行

在已加载模型环境变量的终端中：

```bash
cd study/Regression
make external-statistical-evolution
make gate RUNTIME=.runtime/external-openai-v2-v3-statistical
make console RUNTIME=.runtime/external-openai-v2-v3-statistical
```

实验执行完成后，Console 的 Evidence 区域应显示样本量、Case 胜负、95% 区间和结论等级。未运行真实实验前，代码和报告结构可由单元测试验证，但不应预先写出结果结论。
