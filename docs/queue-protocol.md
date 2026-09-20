# 队列协议 / Queue protocol

`adapters/anyi/worker.py` 把流水线接到一个持久化任务队列后面。制作主机**只出站**：没有入站端口、没有 webhook。队列一侧需要实现下面六个接口，用什么后端都可以。

The adapter runs the pipeline behind a durable job queue. The build host is **outbound only**: no inbound port, no webhook. Implement the six endpoints below on any backend.

## 鉴权与租约 / Auth and leases

- 所有请求带 `Authorization: Bearer <WORKER_TOKEN>`；令牌至少 32 字节，只存在于队列服务端与制作主机的 `.env`。
- 领取任务时服务端发一个 `leaseToken`，之后针对该任务的每个请求都必须带 `X-Live2d-Lease: <leaseToken>`。
- 租约 120 秒；适配器每 15 秒续约一次。续约连续 75 秒失败，或服务端回 401/404/409，适配器视为**租约丢失**：终止本地子进程，不再上传，不调用 `fail`。
- 租约过期的任务可以被再次领取（`attempts + 1`），最多 3 次；每次领取有独立的产物命名空间，旧进程晚到的上传不会污染新一轮。
- 适配器只接受 HTTPS 的 `ANYI_API_URL`（或 `http://127.0.0.1` / `localhost` 用于本地测试），拒绝重定向。

## 接口 / Endpoints

### `POST /internal/live2d/jobs/claim`

请求体空。返回：

```json
{"job": null}
```

或

```json
{
  "job": {
    "id": "0533712f-e198-4867-abcd-664a0f65fac6",
    "prompt": "…",
    "leaseToken": "…",
    "retryHint": null,
    "inputPath": "/internal/live2d/jobs/<id>/input"
  }
}
```

- `id` 必须是 UUID；`prompt` 与 `inputPath` 至少一个有意义（有图时 `inputPath` 非空）。
- `retryHint` 只在**第一次**领取时下发一次，目前唯一值 `regenerate_image`：适配器收到后放弃该任务全部旧检查点。服务端应在下发后立即清空，避免租约丢失重领时重复付费。
- 适配器没有任务时每 10 秒问一次。

### `GET /internal/live2d/jobs/:id/input`

返回源图字节（`application/octet-stream`）。适配器拒绝超过 8 MB 的输入。

### `POST /internal/live2d/jobs/:id/heartbeat`

```json
{"stage": "decomposing", "progress": 25}
```

`stage` ∈ `preparing | generating | planning | decomposing | expressions | refining | rigging | verifying | uploading`；`progress` 为 0–99 的整数。服务端应延长租约、记录进度且不允许进度倒退。

### `POST /internal/live2d/jobs/:id/artifacts?name=<相对路径>`

multipart 单文件字段 `file`，请求头 `X-Content-SHA256: <hex>`。服务端必须重新计算哈希并在不符时拒绝（422），同名文件重复上传时哈希相同返回成功、不同返回冲突（409）。

适配器上传的文件名固定为：

| 名称 | 内容 |
| --- | --- |
| `runtime/model.model3.json` | 运行时清单，含 `AnyiBounds`（中性姿态外接框） |
| `runtime/<引用的每个文件>` | `.moc3`、纹理、`physics3`、`cdi3`、各 `motion3`；待机动作已去掉口型曲线 |
| `preview.png` / `preview.gif` | 中性姿态与待机循环 |
| `validation.json` | `corePassed`、`motionPassed`、`psdPassed`、`poseCount`、`feetMaxDisplacementPixels`、`motionScale`、`backgroundClipped`、`visualReview` |
| `project.zip` | 精修工程：PSD、cmo3、moc3、json、md |

建议的服务端上限：单个运行文件 32 MB，`project.zip` 240 MB，单任务总计 350 MB、100 个文件；`runtime/` 下只接受 `.moc3`、`.png`、`.json` 等白名单类型，并检查文件头。

### `POST /internal/live2d/jobs/:id/complete`

请求体空。服务端在这里做最终校验后才把任务标为成功：四个必备文件齐全（`runtime/model.model3.json`、`preview.png`、`project.zip`、`validation.json`）；`model3.json` 里引用的每个文件都已上传且路径是安全的相对路径（无绝对路径、无 `..`、无外部 URL）；`validation.json` 三项 `*Passed` 都为 `true`。任一不符返回 422，任务保持运行态等待租约过期。

### `POST /internal/live2d/jobs/:id/fail`

```json
{"diagnosisCode": "background_leak", "suggestion": "regenerate_image", "summary": "背景被并入了人物图层……"}
```

三个字段都可选。服务端必须按白名单过滤：`diagnosisCode` ∈ `provider_unavailable | background_leak | face_not_located | expression_failed | rig_unstable | budget_exhausted`；`suggestion` ∈ `retry | regenerate_image | new_input`；`summary` ≤ 200 字。除此之外不要把任何制作端文本透给终端用户。

## 状态机 / Job state machine

```text
queued ──claim──▶ running ──complete──▶ succeeded
                    │  ▲                    (产物不可变；不允许重试)
                    │  └── claim again (lease expired, attempts < 3)
                    ├──fail──▶ failed ──retry(hint?)──▶ queued
                    └──cancel─▶ cancelled ──retry──▶ queued
```

- 成功的任务不可变：只有 `failed` / `cancelled` 允许重试。这也是客户端可以永久缓存产物的前提。
- 用户取消时服务端把任务标为 `cancelled`，适配器在下一次续约收到 409 后终止本地进程；已提交到 GPU 的推理可能自行跑完，但不会被发布。
- 同一对象同时只允许一个活动任务是队列一侧的策略，与适配器无关。

## 本地目录 / Local layout

```text
<work-dir>/<job-id>/
├── input.png                 服务端源图（如有）
├── progress.json             当前阶段，供心跳线程读取
├── supervisor-state.json     跨尝试的监督预算
├── worker-output.txt         子进程输出
├── attempt-<hex>/            每次尝试一个构建目录（见 pipeline.md）
└── delivery-<hex>/           本次上传的确切文件集
```

目录权限 700；包含用户图片与诊断记录，按你的保留策略清理，禁止公开托管。
