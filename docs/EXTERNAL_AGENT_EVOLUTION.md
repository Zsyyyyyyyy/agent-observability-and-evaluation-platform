# External Agent Evolution Runbook

阶段 6 用同一个框架无关的外部 Agent 完成一次可归因的 `v2 → v3` 演进实验。两版共享模型、工具白名单、Case、预算和平台 Gate；唯一变量是 `examples/external_openai_agent.py` 中的 Prompt Profile。

| Version | Profile | Intervention |
|---|---|---|
| `external-openai-v1` | `direct-repair-v1` | 直接检查、修复、验证 |
| `external-openai-v2` | `observe-plan-act-verify-v2` | 先观察和规划，再做一次最小修改并验证 |
| `external-openai-v3` | `targeted-context-verify-v3` | 先用一次定向上下文定位，再走最短正确工具路径 |

## 运行顺序

如果已有的 v1/v2 Artifact 尚未进入 Evolution Catalog，先只读重建报告并复用同一 Gate：

```bash
set -a; source .env; set +a
PYTHONPATH=src:. python3.11 scripts/run_experiment.py \
  --report-only --unsafe-trusted-host --adapter external-command \
  --external-command '["python3.11", "'"'$(pwd)'"'/examples/external_openai_agent.py"]' \
  --agents baseline:external-openai-v1,candidate:external-openai-v2 \
  --trials 3 --output-dir .runtime/external-openai-v1-v2 \
  --evolution-catalog .runtime/evolution-catalog.json \
  --manifest benchmarks/smoke-case-design.yaml \
  --manifest benchmarks/normalize-case-design.yaml \
  --manifest benchmarks/parse-port-case.yaml
```

然后执行真实的 v2/v3 对照：

```bash
make external-evolution
```

最后对新的 `experiment.json` 复用默认 Gate：

```bash
make gate RUNTIME=.runtime/external-openai-v2-v3
```

Gate 不需要为 v3 重写。它会生成新的不可变 `GateDecision`，并把 `policy_version` 绑定到本次结果。控制台 Timeline 会保留 v1 → v2 → v3，并根据 benchmark fingerprint 标出严格可比性。

## 显式版本谱系

Catalog 是 Artifact 的可重建索引，不应从目录名猜测父版本、版本状态或改动语义。外接 Agent 的已审阅谱系保存在 `configs/external-openai-lineage.json`，并明确区分：V4 是被 Gate 拦截的历史分支；V4.1 通过 8 Case Benchmark 的 Gate，但仍是等待人工任命的 `candidate`，不是自动晋升的 `champion`。

```bash
make apply-external-lineage
```

该命令只写 `.runtime/evolution-catalog.json`，不会改写任何 Trial、Trace、Experiment 或 Gate Artifact，也不会调用模型。后续重新索引实验时，Catalog 会保留这份显式谱系。

## 已冻结的正反对照

- V3 → V4.1：8 Case × 3 Trial 的正向实验为 `PROMOTE`，详见
  [`EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK.md)。
- V3 → V3-negative：同样规模的负向对照为 `HOLD`；两版本正确性相同，
  但负向版本额外两次模型完成使平均 Token 超过预算阈值。该实验对每条
  已选 Attempt 校验运行时源码 Hash，详见
  [`EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_V2.md)。

这两个实验分别证明 Gate 的放行与阻断路径。后续扩充 Benchmark 时，必须
在新的完整 Case 集上重跑相关对照，不能将新增 Case 与旧结果直接拼接。

## 扩展至 11 Case 的正式结论

8 Case 结论之后，Benchmark 新增了库存预留原子性、游标修订完整性和 Webhook
签名规范化三个确定性 Case。新的完整 Case 集上已完成两组独立的 66 Trial 实验：

- V3 → V4.1：`PROMOTE`，保持 11/11 Case 的 all-pass@3；平均 Token 减少
  66.3%，并且耗时、Token、工具调用的 Case 聚类 Bootstrap 区间均支持下降。
  详见 [`EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK_V2.md)。
- V3 → V3-negative：`HOLD`，同样保持 11/11 Case 的 all-pass@3，但受控的
  两次终止后冗余模型调用使平均 Token 上升 49.9%。详见
  [`EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_BENCHMARK_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_BENCHMARK_V2.md)。

两组运行都通过运行时源码 Hash 校验和严格可比性校验。V4.1 可作为当前
11-Case 基准下的推荐候选；V3-negative 仅作为 Gate 拒绝能力的可复现实验对照，
不是可发布候选。

## 成功标准

- 新实验产生 18 条 v2/v3 Trial（3 Case × 3 Trial × 2 Version）；
- `experiment.json` 包含 `evolution_experiment_id` 和 `evolution_catalog`；
- Catalog 中出现 `external-openai-v3`，并将 v2/v3 Experiment 标为 `strict`；
- Gate 只读取平台生成的评测证据，不读取 Agent 自报结论；
- Console 能查看 v2/v3 的 Trace、工具名称、Diff、指标 delta 与 Gate；
- 失败或模型超时保留为 Artifact，不覆盖历史 Experiment。
