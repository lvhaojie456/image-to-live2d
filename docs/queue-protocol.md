# 队列协议 / Queue protocol

`adapters/anyi/worker.py` 把流水线接到一个持久化任务队列后面。制作主机**只出站**：没有入站端口、没有 webhook。队列一侧需要实现下面六个接口，用什么后端都可以。

The adapter runs the pipeline behind a durable job queue. The build host is **outbound only**: no inbound port, no webhook. Implement the six endpoints below on any backend. This version requires schemaVersion 2 visual validation and the `needs_review` terminal state; update a backend that previously published on structural checks alone before connecting the adapter.

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

`stage` ∈ `preparing | generating | planning | decomposing | expressions | refining | rigging | verifying | repairing | uploading`；`progress` 为 0–99 的整数。服务端应延长租约、记录进度且不允许进度倒退。`repairing` 为修复并复检，通常进度 93；之后的复查可能上报 verifying / 92，后端应保留最大进度。

### `POST /internal/live2d/jobs/:id/artifacts?name=<相对路径>`

multipart 单文件字段 `file`，请求头 `X-Content-SHA256: <hex>`。服务端必须重新计算哈希并在不符时拒绝（422），同名文件重复上传时哈希相同返回成功、不同返回冲突（409）。

适配器上传的文件名固定为：

| 名称 | 内容 |
| --- | --- |
| `runtime/model.model3.json` | 运行时清单，含 `AnyiBounds`（中性姿态外接框） |
| `runtime/<引用的每个文件>` | `.moc3`、纹理、`physics3`、`cdi3`、各 `motion3`；待机动作已去掉口型曲线 |
| `preview.png` / `preview.gif` | 中性姿态与待机循环 |
| `validation.json` | `schemaVersion: 2`、`visualPassed`、结构三项 `*Passed`、动作指标、`visualReview`、`reviewSummary`、`repair` |
| `project.zip` | 精修工程及验收：PSD、cmo3、moc3、json、md，`REVIEW.md`、`visual-repair.json` 和 `visual-evidence/` |

建议的服务端上限：单个运行文件 32 MB，`project.zip` 240 MB，单任务总计 350 MB、100 个文件；`runtime/` 下只接受 `.moc3`、`.png`、`.json` 等白名单类型，并检查文件头。

### `POST /internal/live2d/jobs/:id/complete`

请求体空。服务端先检查四个必备文件（`runtime/model.model3.json`、`preview.png`、`project.zip`、`validation.json`）、运行清单所有引用的完整性与路径安全性，以及 `corePassed` / `motionPassed` / `psdPassed` 全为 true。然后**独立校验视觉报告**，不能仅信任 worker 的成功标签。

```json
{
  "schemaVersion": 2,
  "visualPassed": true,
  "corePassed": true,
  "motionPassed": true,
  "psdPassed": true,
  "refinementRequired": true,
  "editorCompatibility": "unverified",
  "poseCount": 370,
  "feetMaxDisplacementPixels": 0.00023,
  "motionScale": 1.0,
  "backgroundClipped": [],
  "visualReview": {
    "schemaVersion": 2,
    "passed": true,
    "identityPreserved": true,
    "motionAdequate": true,
    "score": 0.8,
    "issues": [],
    "explanation": "未见严重缺陷"
  },
  "reviewSummary": "",
  "repair": {"schemaVersion": 2, "passed": true, "selectedRound": "round-01", "reason": "passed"}
}
```

- 检查 schemaVersion、布尔字段类型、有限的 0–1 score、问题列表的 code / severity / part 白名单与非空 detail。具体字段见 [supervisor.md](supervisor.md)。
- 只有身份保持、动作可见、没有 major/critical 问题时，计算结果才为通过；该结果必须同时等于 `visualReview.passed` 和 `visualPassed`。
- 自洽且通过 → `succeeded`；自洽但不通过 → `needs_review`，保存不超过 200 字的 `reviewSummary` 或安全的默认文案。
- `visualReview: null` 只在 `visualPassed: false` 时有效，交付为 `needs_review`。
- 缺少新版报告、类型/枚举错误、成功标志矛盾或结构不合格返回 422，不改变任务为成功。当前适配器会把该错误转入 fail 处理。

响应为 `{"ok":true,"status":"succeeded"}` 或 `{"ok":true,"status":"needs_review"}`。同一租约重复完成时返回相同状态。没有视觉报告的旧 worker 不能向新版后端发布；旧版只检查结构的后端必须升级，避免误发布人工处理工程。

The backend must independently validate the versioned visual report and recompute the verdict from identity, motion and issue severity. A valid visual failure (or an unavailable review represented by null/false) becomes `needs_review`. Missing or contradictory reports are rejected with 422. Completion is idempotent for the same lease and returns the terminal status.

### `POST /internal/live2d/jobs/:id/fail`

```json
{"diagnosisCode": "background_leak", "suggestion": "regenerate_image", "summary": "背景被并入了人物图层……"}
```

三个字段都可选。服务端必须按白名单过滤：`diagnosisCode` ∈ `provider_unavailable | background_leak | face_not_located | expression_failed | rig_unstable | budget_exhausted | photo_too_darkble | budget_exhausted`；`suggestion` ∈ `retry | regenerate_image | new_input`；`summary` ≤ 200 字。除此之外不要把任何制作端文本透给终端用户。

## 状态机 / Job state machine

```text
queued ──claim──▶ running ──complete, visual pass──▶ succeeded
                    │                        (immutable; no retry)
                    ├──complete, visual fail──▶ needs_review
                    │                              └──retry──▶ queued
                    ├──fail──▶ failed ──retry(hint?)──▶ queued
                    ├──cancel─▶ cancelled ──retry──▶ queued
                    └──claim again (lease expired, attempts < 3)
```

- 成功任务不可变；`failed` / `cancelled` / `needs_review` 可以按后端策略重试，预算不重置。`needs_review` 应在 UI 显示“需要人工处理”，并保留工程下载。
- `needs_review` **禁止绑定、运行资产读取和运行缓存清单**，只能本人读取 `project.zip`、`preview.png`、`validation.json`，响应 `Cache-Control: private, no-store`。因为这种任务可重试，不能使用成功模型的 immutable 缓存。
- 用户取消时服务端标记 `cancelled`，适配器在下一次续约收到 409 后终止本地进程；GPU 推理可能自行跑完，但不会被发布。
- 同一对象同时只允许一个活动任务是队列一侧策略，与适配器无关。

A manual-handoff result must remain private, downloadable and unbindable. Deny its runtime files and cache manifest; allow only owner access to the project ZIP, static preview and validation with `private, no-store`. Successful immutable models may retain the existing cache policy. Retrying a job does not reset its repair budget.

## 上传代理 / Upload proxy

适配器文件上传超时为 **300 秒**，领取/心跳等控制请求仍为 90 秒。代理要给 multipart 请求留出额外空间：[nginx-location.conf](../adapters/anyi/nginx-location.conf) 提供 `/internal/live2d/` 的 **256 MB** 和 300 秒配置，后端本身仍限制 ZIP 240 MB、单个运行资产 32 MB。

把示例 include 到**实际加载的** API server 块，调整 proxy_pass 指向你的队列后端。`nginx -T` 确认它生效，修改前备份，`nginx -t` 成功后 reload。`sites-enabled` 可能是独立旧文件，只改 `sites-available` 不一定生效。本仓库不包含安忆服务器地址或自动修改生产服务的脚本。

Uploads use a 300-second timeout; control requests retain 90 seconds. Include the generic Nginx location in the actively loaded API server block and point it at your backend. Verify with `nginx -T`, back up the config, validate with `nginx -t`, then reload. The 256 MB proxy limit leaves multipart overhead above the API's 240 MB ZIP limit. This repository does not deploy or modify an existing server.

## 本地目录 / Local layout

```text
<work-dir>/<job-id>/
├── input.png                 服务端源图（如有）
├── progress.json             当前阶段，供心跳线程读取
├── supervisor-state.json     跨尝试的监督预算
├── worker-output.txt         子进程输出
├── attempt-<hex>/            每次尝试一个构建目录（见 pipeline.md）
│   ├── visual-rounds/       各轮独立工程、模型和证据
│   ├── visual-repair.json   修复与选择记录
│   └── REVIEW.md            人工精修说明
└── delivery-<hex>/           本次上传的确切文件集
```

目录权限 700；包含用户图片与诊断记录，按你的保留策略清理，禁止公开托管。
