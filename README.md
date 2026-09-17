# 安忆 Live2D 制作端

本目录包含图生 Live2D 流水线及安忆队列适配器，源自本机已实测的 `ni-qu` 项目。后续安忆接入以本目录代码为准，API 密钥、虚拟环境、模型权重和官方 Cubism SDK 均不随仓库分发。

## 环境

- 制作主机：macOS、Python 3.12、JDK 21、单独安装的 Cubism Editor 5.3。
- GPU：可 SSH 访问的 24 GB 4090，已安装 See-through 和固定版本权重；见 `scripts/models-manifest.json`。推理端与制作主机可以分离。
- 绑定引擎：Live2D Agent Kit 提供的 psd2live，固定提交 `5526f2e16b57e5f83d34f33730d6fa26d8bc8695`。参考其 `scripts/setup-psd2live.py --directory /path/to/psd2live --source-only`；制作时再次校验锁文件和补丁哈希。
- 原流程默认图像模型 `gpt-image-2.5-sunburst`、规划模型 `gpt-6-astra`，都可通过环境变量配置。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

在本机权限为 600 的 `.env` 中配置 `.env.example` 列出的供应商/SSH 变量，以及：

```dotenv
ANYI_API_URL=https://api.anyibj.cn
ANYI_WORKER_TOKEN=与安忆服务端LIVE2D_WORKER_TOKEN相同的随机密钥
LIVE2D_JAVA_HOME=/path/to/jdk21/Contents/Home
LIVE2D_KIT_DIR=/path/to/live2d-agent-kit
LIVE2D_ENGINE_DIR=/path/to/psd2live
CUBISM_CORE_DIR=/Applications/Live2D Cubism 5.3/res
```

```bash
.venv/bin/python scripts/anyi_worker.py
# 领取并完成一项任务后退出；没有待办任务则直接退出
.venv/bin/python scripts/anyi_worker.py --once
```

制作端仅向安忆 API 发起出站连接；无需在 Mac 或 4090 开放公网 HTTP 端口。供应商密钥仅存在于制作主机环境中；安忆客户端不接触制作端密钥。工作目录默认 `work/anyi-jobs/`，包含用户图片和诊断输出，目录权限为 700，应按运维保留期清理和备份，禁止公开托管。

常驻部署可用 `ANYI_WORKER_ENV_FILE` 指向代码目录之外的 600 权限环境文件。生产 Mac 使用 launchd 管理进程；需要 Mac 在线且不休眠。Astra 规划采用流式响应、默认 low 推理强度，可通过 `ASTRA_REASONING_EFFORT` 调整；流在传输层被对端中断或网关返回 5xx 时按 `ASTRA_STREAM_RETRIES`（默认 1 次）重新发起同一请求。

## 任务协议

服务端保存任务状态。制作端领取 120 秒租约，每 15 秒续约。过期任务最多自动重新领取 3 次；每次租约有独立产物命名空间，旧制作进程不能发布新任务的结果。取消在下次续约时终止本地子进程；已提交的远程 GPU 计算可能完成收尾，但不会被发布。

失败重试可在同一不可变任务目录内复用已生成立绘、Astra 计划、PSD 和表情检查点。可恢复的部分阶段会被重新计算；尚不承诺所有异常都能自动修复。

生成步骤与单机一致：提示词或图片 → Astra 规划 → See-through 拆层 → 表情素材 → 精修 PSD → 基础绑定和身体动作 → Core 及动作采样检查。检查通过后只上传白名单运行资产、预览、验证报告及精修 ZIP。运行包待机循环去除口型曲线，避免与 App 对话口型互相覆盖；精修 ZIP 保留原始模型与表情动作。

分层 PSD 是可精修素材。第三方导出的 `.cmo3` 已在 Cubism 5.3 显示人物和参数，但仍有格式兼容提示，另存/重开未完整验收。App 运行使用已验证的 `.moc3`，不会把 Core 加载成功解释为人工美术精修已完成。

## 本地检查

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python live2d_pipeline.py build --prompt '偏现实的全身老爷爷，正面站姿'
.venv/bin/python live2d_pipeline.py build --image /path/to/character.png
```

`--output /new/empty/directory` 指定输出目录。制作端通过 `LIVE2D_PROGRESS_FILE` 读取阶段进度。

GPL-3.0 的 psd2live 适配代码保持独立目录及许可标记；See-through、Agent Kit、模型权重与官方 Cubism SDK 分别遵循上游许可证，本仓库不捆绑这些依赖。
