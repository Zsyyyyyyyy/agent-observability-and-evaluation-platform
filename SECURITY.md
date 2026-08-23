# Security Model

Regression Lab 是本地、单用户、面向受信任开发者的 Agent 回归工具，不是多租户服务或任意代码执行沙箱。

## 信任边界

- `external-command` 会执行用户明确配置的本地 Agent，因此只能接入可信代码。平台默认只传递基础运行变量、`AGENT_*`、OpenAI-compatible 配置和平台拥有的 `REGRESSION_*` Trial 身份，不会复制完整的 `os.environ`。
- 默认 Docker Sandbox 隔离的是平台执行的测试命令；它不自动容器化外部 Agent 进程。
- `--unsafe-trusted-host` 会在宿主机执行测试，仅适用于明确可信的 Fixture 和 Agent。
- Studio 固定监听 `127.0.0.1`，不应通过端口转发或反向代理作为共享控制面部署。

需要其他 Provider 变量的 Agent 应在自己的可信入口中加载专用配置，不应要求平台全量继承环境。运行真实 Agent 时使用最小权限、可撤销的 API Key。公开演示前使用脱敏导出器，不要直接上传 `.runtime/`。

## 漏洞报告

请优先使用仓库的私有 Security Advisory 报告路径逃逸、命令执行边界、密钥泄漏、Artifact 完整性绕过或 Gate 错误晋级问题。报告中不要附带真实密钥或包含用户源码的完整 Runtime。
