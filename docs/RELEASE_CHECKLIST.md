# 独立发布前检查清单

发布单位是 `study/Regression` 本身，而不是父级教学仓库。目标是让面试官在 3 分钟内理解定位，在 10 分钟内完成验证。

## 发布前必须完成

- [ ] 在干净终端运行 `make test`。
- [ ] Docker 可用时运行 `make docker-test && make failure-suite`。
- [ ] 用已有 Artifact 运行：

  ```bash
  make experiment-report RUNTIME=.runtime/repeated-experiment-v1-v2
  make gate RUNTIME=.runtime/repeated-experiment-v1-v2
  ```

- [ ] 确认 `README.md` 中的 48 Trial 数据、Gate 结论与 [全量实验报告](./EXPERIMENT_REPORT_FULL_CORE_V1_V2.md) 一致。
- [ ] 确认 `.env`、`.runtime/`、数据库、Trace 和任何模型密钥不进入 Git。
- [ ] 录制 3–5 分钟演示，顺序使用 [DEMO_SCRIPT.md](./DEMO_SCRIPT.md)。

## 仓库首页最低内容

- [ ] 一句话定位：面向 Coding Agent 的可观测、评测与版本晋级平台。
- [ ] 两张 Console 截图：Gate 总览、Case 并列对比。
- [ ] 架构图和 Quick Start。
- [ ] 真实数据：8 Case × 3 Trial × 2 Versions；v1 22/24、v2 23/24。
- [ ] 诚实限制：本地单机 MVP、模型超时率持平、成功样本成本仍有权衡。
- [ ] CI 说明：离线单测 + Docker 隔离 + Failure Suite，不调用真实模型。

## GitHub 发布建议

```bash
cd <repository-root>
git init
git add .
git status
```

在 `git status` 中再次确认没有 `.env`、`.runtime/` 与实际模型输出，再创建首个提交并推送到新的 GitHub 仓库。不要把父级仓库中的 `s01`–`s20` 一并上传；`s20-replay` 仅作为只读接入样例，README 已明确边界。

## 发布后人工检查

- [ ] GitHub README 的两张图片可加载。
- [ ] Actions 中 `verify` Workflow 可见；它不需要模型 Secret。
- [ ] 按 README 的 `make test`、`make docker-test`、`make manifest-check` 能复现。
- [ ] 将仓库链接、演示视频链接和项目名填入简历。
