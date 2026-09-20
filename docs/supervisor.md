# 监督与视觉修复 / Supervisor and visual repairs

`scripts/supervisor.py` 调用配置的视觉模型，`scripts/visual_repair.py` 管理问题白名单、候选比较、回退与三轮上限，`auto_build.finish_visual_repair` 执行固定修复动作。模型的回答只作为数据，不执行任意代码或命令。

The supervisor calls your configured vision model. `visual_repair.py` validates its output and manages candidate selection, rollback and the three-round cap; `auto_build.finish_visual_repair` executes the fixed repair menu. Model output is data, never executable code.

## 模式 / Modes

| 模式 / Mode | 行为 / Behavior | 交付 / Delivery |
| --- | --- | --- |
| `act`（默认 / default） | 检查实际模型，最多三轮自动修复 / review exported models, repair up to three rounds | 结构与视觉均通过才 `complete` / both checks required |
| `shadow` | 检查并记录，不启动视觉修复循环 / review without repair rounds | 严重问题未解决则 `needs_review` / unresolved defects require a manual handoff |
| `off` | 不调用监督模型，规则层仍运行 / no supervisor calls, rules still run | 没有视觉确认，只能 `needs_review` / no visual pass, manual handoff only |

`SUPERVISOR_MODEL` 留空时使用 `PLANNER_MODEL`，旧配置 `ASTRA_MODEL` 仍可作别名。`off` 不要求监督模型名，但没有规划检查点时，规划阶段仍需 `PLANNER_MODEL`。局部表情编辑会使用 `IMAGE_MODEL` 并产生费用；整张人物原图重生成仍由使用者另行发起。

`SUPERVISOR_MODEL` falls back to `PLANNER_MODEL` (legacy `ASTRA_MODEL` remains supported). Mode `off` does not require a supervisor model; a fresh planning stage still needs a planner. Local expression repairs use `IMAGE_MODEL` and may incur charges. Full-portrait regeneration remains a separate user action.

## 证据与验收 / Evidence and acceptance

原有阶段关卡继续检查规划矩形、拆层结果和新生成的表情；最终验收改为**实际导出模型**。结构检查通过后提供以下图片：

- 原人物脸部，用于对照身份、年龄、肤色、胡须与画风。
- MOC 的双眼闭合、左右单眼、半开嘴、全开嘴、笑嘴组合、闭眼张嘴。
- 身体、呼吸、手臂、衣裙摆动极值。
- 连续身体帧与相同时间段的连续脸部近景，避免只看全身缩略图漏掉残影。

The final gate uses the exported MOC, including exact expression states, body extremes and consecutive body/face frames, compared with the original identity. Stage gates still review planning boxes, decomposition and newly generated expression edits. Source-art composites alone cannot pass the final gate.

`review_model` 返回 schemaVersion 2：

```json
{
  "schemaVersion": 2,
  "identityPreserved": true,
  "motionAdequate": true,
  "score": 0.8,
  "issues": [
    {"code": "eye_seam", "severity": "minor", "part": "eyes", "detail": "眼睑边缘仍可精修"}
  ],
  "explanation": "未见严重缺陷，保留细节精修项"
}
```

程序检查字段类型、有限的 0–1 分数与枚举，并自行计算 `passed`，不信任模型给出的成功标签。身份保持、动作足够可见、没有 major/critical 问题，三项必须同时成立。高分不能覆盖严重缺陷；无回答、非法字段或无法看清关键部位，不能发布为成功。

The code validates the schema and computes `passed` itself. Identity and visible motion must be preserved, and no major or critical issues may remain. A high score never overrides a serious defect. Missing or invalid evidence cannot produce a visual pass.

- **major / critical**：身份改变、闭眼漏眼球或明显重影、嘴部矩形色块、肢体破洞脱节、背景残留或遮挡错误。These block publication.
- **minor**：轻微线条生硬、自然度或物理手感。These may remain as notes for the artist.
- 正常眼下皱纹、胡须和皮肤纹理不是残影；呼吸和轻摆本就应细微。Normal wrinkles and subtle idle motion are not defects.

阶段关卡图片长边最多 384 px，最终证据拼图最多 1536 px。请求使用非流式 JSON、`temperature=0`；最终审查最多 2200 completion tokens，其它关卡通常 600。`SUPERVISOR_TIMEOUT_SECONDS` 默认 90 秒，失败重试一次。阶段审查失败通常可降级继续，但最终审查缺失会交付 `needs_review`。

Stage thumbnails are capped at 384 px; final evidence sheets at 1536 px. Calls use non-streaming JSON at temperature 0, a default 90-second timeout and one retry. The final review allows 2200 completion tokens. A missing final judgment yields a manual handoff, not success.

## 修复菜单 / Repair menu

| 问题类型 / Issue | 执行动作 / Action |
| --- | --- |
| `eye_residual`, `eye_seam` | 复核眼部定位、局部重画闭眼、扩大完整眼部覆盖并柔化边缘 / relocate, redraw and adjust eye coverage |
| `mouth_artifact`, `mouth_shape` | 复核嘴部定位、局部重画、重新测量嘴腔牙舌唇缘 / relocate, redraw and remeasure mouth parts |
| `identity_drift` | 从原脸重新编辑相关眼口，强制恢复遮罩外原像素 / redraw from the original and restore unmasked pixels |
| `background_leak`, `layer_order` | 原图重新拆层、深度和前景裁剪、重新整理 / redecompose, clip and rebuild |
| `joint_gap`, `motion_weak` | 按手臂 alpha 重估支点和固定范围，调整幅度并重绑 / re-anchor and rebind with bounded motion |
| `other` | 保留阻断并交人工 / manual handling, no arbitrary action |

每轮独立保存，重新导出、检查结构，再复查视觉。候选仅在严重问题减少或评分改善时替换较好版本；之前保持的身份或可见动作不能退化。连续两轮无改善（包括重画、重绑失败）停止。初始模型结构不合格仍走 `failed`，不能当作可下载的合格精修起点。

Each candidate is exported and checked again. Only an improvement may replace the best valid version; preserved identity or visible motion cannot regress. Two stagnant or failed rounds stop the loop. A structurally invalid initial model remains a failed build rather than an eligible editing handoff.

## 预算与检查点 / Budget and checkpoints

| 项目 / Item | 上限 / Limit |
| --- | --- |
| 监督模型实际请求 `model_calls`（包括重试 / including retries） | 24 |
| 视觉修复轮数 `visual_repairs` | 3 |
| 规则层幅度回退 `tune_motion` | 2（1.0 → 0.66 → 0.33） |
| 失败诊断删除规划检查点 `replan_with_hint` | 2 |

24 次只计监督模型请求，不含原始规划和图像生成/编辑。三轮限制包含这些修复动作产生的图像调用；不会自动重生成整张人物。旧诊断菜单保留 `retry_stage`、`redo_expressions`、`regenerate_image` 项，但它们在失败诊断里产生建议，不能绕过视觉修复轮数限制。

The 24-request limit counts supervisor requests, not original planning or image calls. Local image edits are bounded by the three repair rounds. Legacy failure suggestions do not start an unbounded repair loop or regenerate the entire portrait automatically.

`--supervisor-state` 保存同一任务跨尝试预算；队列适配器自动传入 `<job-id>/supervisor-state.json`。写入先落临时文件再原子替换，损坏状态会报错，不能悄悄重置预算。单独 CLI 不指定该路径时只在当前构建内计数。

Use `--supervisor-state` to persist one job's budget across attempts. The adapter supplies it automatically. Writes are atomic; corrupted state fails instead of silently resetting the allowance. Without the option, a standalone CLI build only keeps its own in-memory budget.

## 失败诊断 / Failure diagnosis

结构检查或其它阶段失败时，规则层先判断 `provider_unavailable`、`background_leak`、`face_not_located`、`expression_failed`、`rig_unstable`、`budget_exhausted`。非传输类失败可再由监督模型细化诊断；回答必须使用白名单动作和代码，置信度至少 0.6 才覆盖规则诊断。

`retry_stage`、`clip_background`、`tune_motion`、`redo_expressions` 诊断动作产生 `retry` 建议；`replan_with_hint` 在 act 模式下删除本次规划检查点，下次重试再规划。`regenerate_image` 只产生建议，由用户携带重试 hint 发起。视觉修复循环直接执行的动作以上表为准。

Failure diagnosis remains separate from the final visual gate. It returns a whitelisted code, suggestion and short summary. An act-mode replan decision can remove a plan checkpoint for the next attempt; full-image regeneration remains an explicit retry hint.

## 输出与隐私 / Output and privacy

原始模型回答和供应商日志保留在制作端的 `supervisor/`，不上传。每轮完整模型留在 `visual-rounds/`。被选中版本的 `REVIEW.md`、`visual-repair.json` 和视觉证据会加入私有精修 ZIP；标准化视觉报告会写入上传的 `validation.json`。失败接口只带白名单诊断码、建议和最多 200 字摘要。

Raw provider logs and responses stay local, as do complete per-round workspaces. The private editing ZIP includes normalized repair decisions, artist notes and selected evidence; uploaded validation includes the normalized visual judgment. The failure endpoint only receives whitelisted diagnostics and a summary of at most 200 characters.
