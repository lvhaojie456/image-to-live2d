# 监督层 / Supervisor

`scripts/supervisor.py`。三层，从便宜到贵、从确定到不确定：

```text
校验失败 / a stage fails
  │
  ├─ 1 规则层（无模型调用，始终生效）Rules, no model, always on
  │     传输错误重试；背景泄漏裁剪；动作幅度回退 1.0 → 0.66 → 0.33；嘴部阈值 145/120/100 回退
  │
  ├─ 2 监督层（本模块，事件驱动）This module, event-driven
  │     关卡审查：规划 / 拆层 / 表情 各审一次，收尾打分
  │     失败诊断：失败包 → 诊断码 + 菜单动作 + 一句给用户的话
  │     预算：见下表
  │
  └─ 3 使用者确认 / The user
        付费动作从不自动执行，只变成建议
```

## 模式 / Modes

`LIVE2D_SUPERVISOR_MODE`：

| 模式 | 调模型 | 执行模型选的动作 | 用途 |
| --- | --- | --- | --- |
| `off` | 否 | 否 | 纯规则层；不需要 `PLANNER_MODEL` |
| `shadow`（默认） | 是 | 否，只记录 | 观察"模型建议 = 人工判断"的比例 |
| `act` | 是 | 是，免费动作、预算内 | 观察期达标后开启 |

模型：`SUPERVISOR_MODEL`，留空则与 `PLANNER_MODEL` 相同。每次调用非流式、`temperature 0`、`response_format json_object`、`max_tokens` 600、超时 `SUPERVISOR_TIMEOUT_SECONDS`（默认 90 秒）、失败重试一次后放弃；缩略图长边不超过 384 px。**任何一次调用失败都不阻断构建。**

## 关卡 / Gates

| 时机 | 送什么 | 问什么 | `shadow` | `act` |
| --- | --- | --- | --- | --- |
| 规划后 | 输入图 + 画上脸(红)/眼(蓝)/嘴(绿)矩形 | 框是否落对；需要时给修正框（缩略图坐标，换算回原图并校验在界内） | 记录 | 用修正框覆盖 `layer_plan.json` 的 `regions` |
| 拆层后 | 图层缩略表 + 规则层报告（各层画布占比、已裁剪的层） | 是否仍有背景泄漏、错分、缺失 | 记录 | 记录（裁剪由规则层决定） |
| 表情后（仅新生成时） | 原脸 / 闭眼 / 张嘴 三张裁剪 | 是否闭眼、是否张嘴、是否保持身份 | 记录 | 不合格则用更严格提示词重做一次（预算 1 次） |
| 嘴部三档阈值都失败 | 张嘴编辑图 | 嘴唇外缘矩形 | 用该框重测 | 同 |
| 校验通过后 | 表情合成表 + 姿态表 | 0–1 评分与问题列表 | 写入 `validation.visualReview`，不阻断 | 同 |

审查记录写在尝试目录 `supervisor/gate-<stage>.json`，含模型名、缩略图尺寸、耗时、原始回答。

## 失败处置 / Failure handling

`auto_build.handle_failure` 先按规则定诊断码：传输类异常且发生在生成/规划/拆层/表情阶段 → `provider_unavailable`；绑定/校验阶段且规则层裁过背景 → `background_leak`，否则 `rig_unstable`；精修/表情阶段 → `expression_failed`；预算耗尽 → `budget_exhausted`。

非传输类失败再问模型。送出的**失败包**只有：阶段名、异常类名、去掉路径的 160 字短消息、数值指标（脚底位移、翻转数、退化数、泄漏层名）、剩余预算、最近 8 条动作历史，加 `neutral.png` 与图层缩略表。不含本机路径、密钥、供应商原始响应。

模型回答 `{"diagnosis_code", "action", "explanation", "confidence"}`。诊断码与动作都必须在白名单内，否则整条回答作废；置信度 ≥ 0.6 才覆盖规则层的诊断码与说明。

| 动作 | 成本 | 需确认 | `act` 模式下做什么 |
| --- | --- | --- | --- |
| `retry_stage` | 时间 | 否 | 变成建议 `retry` |
| `replan_with_hint` | 一次规划调用 | 否 | 删除本次 `layer_plan.json` 检查点，下次重试重新规划 |
| `clip_background` | 无 | 否 | 变成建议 `retry`（裁剪本身由规则层在每次构建自动做） |
| `tune_motion` | 无 | 否 | 变成建议 `retry`（回退本身由规则层自动做） |
| `redo_expressions` | 两次图像编辑 | 否 | 在表情关卡内执行 |
| `regenerate_image` | 一次图像生成 | **是** | 只变成建议 `regenerate_image`，由使用者确认后带 `hint` 重试 |
| `give_up` | 无 | 否 | 无建议 |

## 预算 / Budget

每单预算跨尝试共享，存在 `--supervisor-state` 指向的 JSON（适配器传任务目录下的 `supervisor-state.json`）：

| 项 | 次数 |
| --- | --- |
| `model_calls` | 8 |
| `replan_with_hint` | 2 |
| `tune_motion` | 2 |
| `redo_expressions` | 1 |
| `regenerate_image` | 1 |
| `retry_stage` | 3 |

## 对外输出 / What leaves the machine

只有三样会上报给队列：白名单诊断码、白名单建议、≤ 200 字的一句中文摘要（去控制字符）。

| `diagnosis_code` | 默认摘要 | 默认建议 |
| --- | --- | --- |
| `provider_unavailable` | 生成服务暂时不可用，请稍后重试。 | `retry` |
| `background_leak` | 背景被并入了人物图层，自动裁剪后仍未通过检查，建议换一张纯色或透明背景的图片。 | `regenerate_image` |
| `face_not_located` | 没有找到清晰的正面脸部，请换一张正面、无遮挡的图片。 | `new_input` |
| `expression_failed` | 表情素材生成不合格，请重试。 | `retry` |
| `rig_unstable` | 动作检查未通过，建议换一张四肢完整、背景干净的图片。 | `regenerate_image` |
| `budget_exhausted` | 自动修复次数已用完，请换一张图片或修改描述后重新提交。 | `new_input` |

## 隐私 / Privacy

送给模型的图片不超过流水线本来就发给图像/规划模型的范围（输入图及其派生图层的缩略图）。审查与诊断记录留在任务目录，不上传。

Images sent to the supervisor never exceed what the image and planner models already saw. Review logs stay in the job directory and are never uploaded.
