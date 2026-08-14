# Execution Reliability Contract

## 同 Trial 互斥

每个逻辑 Trial 的 Job 目录包含 `.trial.lock`。Runner 使用非阻塞排他文件锁：

- 同一 `Case × Version × Trial` 同时只能有一个执行者；
- 第二个 Resume 明确报 `TRIAL LOCK ERROR`，不会竞争 Attempt 编号；
- 不同 Trial 不共享锁，仍可独立并行。

## Attempt 恢复

持有 Trial 锁的新 Runner 启动时会扫描遗留的 `running` Attempt：

- 将其 Attempt manifest 标记为 `aborted`；
- 保留已写入的 Trace、输入和 Result 证据，不覆盖目录；
- 后续 Retry 创建新的 Attempt ID。

这处理的是进程已经退出、文件锁已释放的孤儿 Attempt；活跃执行中的 Trial 仍由锁拒绝第二个 Runner。

## 原子 Artifact 发布

JSON 投影（Attempt manifest、Trial input/result、selected-attempt、summary、Protocol 和 Worker Result）统一通过 `write_json_atomically` 发布：

1. 同目录临时文件写入 JSON；
2. flush + `fsync` 临时文件；
3. `os.replace` 原子替换目标；
4. `fsync` 父目录。

Trace 是流式 append-only 证据，不使用覆盖式 JSON 发布。若运行中断，读者只能看到旧的完整 JSON 或尚未出现的新 JSON，不会把半截 JSON 当成有效 Artifact。

## 正式实验边界

正式模型实验仍默认 Docker。`--unsafe-trusted-host` 仅用于受信任的本地开发/测试；它不是正式 Benchmark 的安全或复现承诺。
