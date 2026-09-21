# 流水线 / Pipeline

`live2d_pipeline.py build` 是入口，`scripts/auto_build.py` 的 `run()` 按顺序驱动下面的阶段。阶段名与进度值通过 `LIVE2D_PROGRESS_FILE` 对外暴露；任何阶段抛异常都进入监督层的失败处置（见 [supervisor.md](supervisor.md)）。

`live2d_pipeline.py build` is the entry point; `run()` in `scripts/auto_build.py` drives the stages below in order. Stage names and progress are exposed through `LIVE2D_PROGRESS_FILE`; any exception hands over to the supervisor's failure handling (see [supervisor.md](supervisor.md)).

| 进度 | 阶段名 | 实现 |
| --- | --- | --- |
| 5 | `generating` | `auto_build.run` |
| 15 | `planning` | `live2d_pipeline.plan` |
| 25 | `decomposing` | `live2d_pipeline.remote_decompose` + `scripts/foreground.py` |
| 55 | `expressions` | `auto_build.edit_face` + `scripts/auto_expression.py` |
| 70 | `refining` | `scripts/face_assets.py` + `scripts/build_refinement_package.py` |
| 80 | `rigging` | `scripts/body_motion.py` + `integrations/psd2live/ProceduralMotion.kt` |
| 90 | `verifying` | `scripts/verify_body_motion.py` + `integrations/psd2live/BodyMotionSequence.java` |
| 92 | `verifying` | `visual_repair.evidence_images` + `Supervisor.review_model` |
| 93 | `repairing` | `auto_build.finish_visual_repair` + `visual_repair.repair_loop`，最多三轮 / at most three rounds |
| 96 | `uploading` | `adapters/anyi/worker.py`（仅队列适配器 / queue adapter only） |

## 1. 生成立绘 / Generate

提示词后固定追加"正面完整角色立绘，干净背景，四肢不裁切，适合 Live2D 拆层"，按 `--size`（默认 1024×1536）调用 `images.generate`。`IMAGE_BACKGROUND=transparent` 时先请求透明背景；供应商返回 4xx 就回退到普通生成，5xx 直接失败。给了 `--image` 时这一步只是拷贝。

The prompt gets a fixed suffix asking for a front-facing, uncropped, clean-background full-body portrait. With `IMAGE_BACKGROUND=transparent` a transparent background is requested first and a 4xx from the provider falls back to a plain request. With `--image` this stage is a copy.

## 2. 背景中和 / Neutralize the background

`foreground.neutralize_background` 把透明像素或"角落近白且与边框连通"的区域填成中性灰 `(210,210,210)`，同时输出 `foreground/input_mask.png`。

原因：See-through 会把**纯白背景当作人物**并入某个衣服图层（实测同一张图白底时 `topwear` 占画布 79%，灰底时 10%）。中性灰能干净地分开。

See-through folds a pure white background into a garment layer (79% of the canvas vs 10% on grey for the same image), so transparent or near-white backgrounds become neutral grey before decomposition, and a foreground mask is kept for stage 5.

## 3. 分析形象 / Plan

把中和后的图发给视觉模型，要求严格 JSON：`character_summary`、`regions`（脸、双眼、嘴的像素矩形，无法定位填 null）、`layers`、`repair_tasks`、`parameters`、`physics`、`acceptance_poses`、`risks`。流式接收，`finish_reason != stop` 视为不完整并失败；传输层中断按 `PLANNER_STREAM_RETRIES` 重发同一请求。

**只有 `regions` 被机器消费**：后面裁脸和表情编辑全靠它。矩形超出图像或缺失时按脸部矩形的固定比例推算，脸部矩形缺失时回退到拆层结果里的 `face` 图层外接框，再不行用画布中上部的固定比例。图层规划与补画清单写成 `layer_plan.md`，保留在构建目录供人工参考。`left_eye` / `right_eye` 明确指画面左右，避免定位审查与表情素材的坐标相反。

Only `regions` is consumed by code; the rest becomes `layer_plan.md` in the build directory for the artist. `left_eye` and `right_eye` refer to image-left and image-right, not the character's anatomical sides. Missing or out-of-range boxes fall back to fixed ratios of the face box, then to the `face` layer's bbox from decomposition, then to a fixed canvas fraction.

## 4. 拆分图层 / Decompose

`remote_decompose` 把图 `scp` 到 `REMOTE_SSH_HOST`，连同 `scripts/remote_worker.py`、`scripts/infer_staged.py`、`scripts/models-manifest.json` 一起放进一个新的任务目录，用 `nohup` 起一个独立进程，然后每 10 秒 `ssh cat status.json` 轮询。任务目录与 `remote_job.json` 让中断后可以恢复而不是重跑。

远端三步（`infer_staged.py`）：LayerDiffusion 拆层（30 步、种子 42、`--resolution` 默认 1280）→ 释放显存 → Marigold 逐部件深度图（768）→ 导出 PSD（`tblr_split`）。输出约 27 个部件 PNG、同名 `_depth.png`、`input.psd`。完成后打成 `result.zip`（存储不压缩）连同逐文件 SHA-256 清单返回；本机校验 zip 哈希、拒绝路径穿越和符号链接、逐文件核对。

The image and the two small scripts go to the GPU host over `scp`; a detached process runs LayerDiffusion (30 steps, seed 42), then Marigold depth per part, then PSD export, and writes `status.json` polled every 10 s. The zip comes back with a per-file SHA-256 manifest and is verified before extraction.

### 泄漏裁剪 / Clip leaks

`foreground.clip_background` 检查每个图层，满足任一条即判定裹了背景：外接框超过画布 60%；按 `input/<part>_depth.png` 饱和（≥ 250）像素超过 30%；落在前景遮罩外的像素超过 30%。只对这些层重建：保留 深度 < 250 且在遮罩内 的像素，形态学开运算、保留 ≥ 最大连通域 2% 的区块、填洞，写出 `decomposition/input_clipped.psd`；其余层逐字节不变。

A layer is flagged when its bbox exceeds 60% of the canvas, or more than 30% of its pixels sit at saturated depth (≥ 250) or outside the foreground mask. Only flagged layers are rebuilt; every other layer is copied byte for byte.

## 5. 生成表情 / Expressions

按脸部矩形裁一块正方形（外扩 45%），做两次 `images.edit`：遮罩只露出双眼或嘴，提示词要求保持年龄、身份、画风、胡子、皱纹和所有未遮罩像素。得到 `expression_eyes.png`（闭眼）与 `expression_mouth.png`（张嘴）。模型返回后强制恢复遮罩外的原图像素，防止局部返修改变胡须、肤色或其它部位。两个编辑结果允许有不同分辨率，各自测量后配准到同一画布。

然后**测量**而不是套矩形（`auto_expression.py`）：张嘴图转灰度，在嘴部搜索框内按 `145 → 120 → 100` 三档暗部阈值找最大暗区（写实风格的胡子阴影会把第一档撑满），取凸包、填洞、膨胀一像素得到嘴腔轮廓；轮廓内 灰度 > 155 且在上 48% 的为牙，下半部 红 > 绿×1.3 的为舌；最暗点作为口腔填充采样。闭眼贴片覆盖同侧睫毛、眼白、虹膜图层的联合外接框；返修时小幅外扩并柔化边缘。三档都失败时监督层可以让模型标一个嘴框重测。

Instead of pasting rectangles, the open-mouth edit is measured: the largest dark blob near the mouth at thresholds 145 → 120 → 100, its convex hull as the cavity, bright upper pixels as teeth, red lower pixels as tongue. Closed-eye patches cover the union of eyelash, eye-white and iris bounds. Repair rounds expand and feather that coverage. Pixels outside the edit mask are restored from the source face; eye and mouth edits may have different resolutions and are registered independently.

## 6. 整理精修素材 / Refinement package

`face_assets.build_face_assets` 把测量出的 `mouth_open`、`tooth-t`、`tongue`、`lip_upper`、`lip_lower`、`eye_close-l/r` 从编辑图坐标配准到模型画布（中心正方形填充后缩放，逆向仿射一次采样），逐个校验哈希。

`build_refinement_package.build` 再把拆层结果整理成 Cubism 习惯：`prepare_rig_layers` 按固定语义顺序（后发 → 腿 → 鞋 → 手 → 下装 → 上衣 → 脖子 → 耳 → 脸 → 鼻 → 眼白 → 虹膜 → 睫毛 → 眉 → 嘴 → 前发 → 头饰）重排并清掉孤立碎像素；腿、鞋在画布中线一分为二成 `-l`/`-r`；手臂在袖口连线处切成 `arm-*` 与 `hand-*`（单一 `handwear` 层先按中线分左右再切袖口）；没有独立嘴层时从脸上按测量区域抠出 `mouth_close`。最后写出分组的 `cubism_refinement.psd` 与只含表情层的 `expression_parts.psd`，并把 PSD **读回来逐像素比对**，层数、画布、可见性、像素任一不符即失败。

Measured expression features are registered onto the model canvas with a single inverse-affine resample. Layers are reordered semantically, legs/shoes split at the midline, arms cut at the cuff into sleeve and hand, and the resulting PSD is read back and compared pixel by pixel before the stage passes.

## 7. 制作动作 / Rig

`body_motion.build_body_motion` 先核对 psd2live 的提交（`5526f2e…`）与 Agent Kit 锁文件里的补丁哈希，不一致拒绝运行。然后在 Agent Kit 的 `ManifestExport.kt` 挂载点插入 `integrations/psd2live/ProceduralMotion.kt`，通过 Gradle 的 `exportManifest` 任务导出。

引擎本身做网格剖分、变形器树、头部/身体参数、物理、眨眼与点头/摇头动作。扩展在此之上加三个子变形器（左臂、右臂、裙摆；没有裙子时改为衣摆）并按 `motion_recipe()` 测量出的肩线、胸口、髋部位置写呼吸与倾斜的关键形。`motion_document()` 用正弦函数生成 `loop_seconds`（8 秒）× `fps`（30）的循环，拆成 BodyIdle、Expressions、Breathing、BodyLean、Arms、Skirt 六个 `motion3`，并在 `model3.json` 里登记 `BodyMotion` / `ExpressionMotion` 参数组。

The engine commit and patch hashes are verified first. psd2live builds meshes, deformers, physics, blink and nod; the extension adds arm and garment warps with measured anchors and writes six procedural `motion3` loops.

## 8. 检查模型 / Verify

`verify_body_motion.run` 用 JDK 编译 Agent Kit 的 `render_core.java`、`validate_core.java` 与本仓库的 `BodyMotionSequence.java`，链接 `CUBISM_CORE_DIR` 里的官方 Core，渲染一张 `poses.tsv`：五个身体参数的全部极值组合、每个参数的单独极值、待机循环按 12 fps 采样的帧、四个单项动作各 24 帧，额外包含双眼闭合、左右单眼、半开嘴、全开嘴、笑嘴组合、闭眼张嘴七项，共 370 个姿态。检查：顶点有限、无翻转与退化三角形、**脚底位移 < 0.25 px**。同时验证 `ParamArm*` 与 `ParamSkirtSwing` 只影响它们该影响的网格。

失败时如果只是脚底位移或翻转（`verification_failure()` 判定为 tunable），按 `MOTION_SCALES = (1.0, 0.66, 0.33)` 缩小全部幅度重新导出重新校验，最多两次，结果记在 `validation.json` 的 `motionScale`。

370 poses are evaluated through official Core: every extreme combination of the five body parameters, each parameter alone, the idle loop at 12 fps, 24 frames of each single motion, and seven exact expression states. Selected poses are rendered by the Java2D diagnostic renderer. Finite vertices, no flipped or degenerate triangles, feet drift below 0.25 px. Drift-only failures shrink all amplitudes 1.0 → 0.66 → 0.33 and retry.

### 导出模型的视觉修复 / Exported-model visual repairs

`author_candidate` 在 `visual-rounds/round-00/` 构建初始工程，结构合格才开始视觉检查。证据包括原人物脸部、实际 MOC 的表情组、身体极值、连续身体帧和连续脸部近景。模型及证据图的 SHA-256 写入 `visual-evidence/hashes.json`。

`repair_loop` 最多三轮：按问题类型只重做相关眼/嘴素材、重新拆层清理，或调整关节支点与动作范围；每轮重新导出和检查。结构失败、身份变差、动作消失的版本不替换较好版本。连续两轮无改善停止。规则层不会把动作幅度降到零，视觉验收还要确认部件仍有可见动作。

选中版本复制到标准 `face-assets/`、`cubism-ready/`、`body-motion/`、`visual-evidence/`，其眼口素材与定位结果成为后续检查点。每轮目录保留，过程记在 `visual-repair.json`，问题写入 `REVIEW.md`。完整菜单、严重程度与预算见 [supervisor.md](supervisor.md)。

`author_candidate` builds each round in its own directory. Once structural checks pass, the supervisor compares the source identity with actual exported-model expressions, extreme poses and consecutive body/face frames. At most three rounds repair the affected assets or rig; regressions roll back and two stagnant rounds stop the loop. The selected model occupies the standard output directories while every candidate and its evidence remain available locally.

## 9. 交付 / Deliver

单机使用时输出目录本身就是产物（README 里有清单）。`build.json.status` 为 `complete` 表示结构与视觉均通过；为 `needs_review` 表示结构合格但需要人工处理。两者构建进程都可正常退出，调用者必须检查该字段。作为服务时 `adapters/anyi/worker.py` 的 `collect_delivery` 只挑运行时引用到的文件复制到 `runtime/`，**删掉待机动作里的 `ParamMouthOpenY` / `ParamMouthForm` 曲线**（否则和聊天口型打架），把中性姿态的外接框写进 `model3.json` 的 `AnyiBounds`，附 `preview.png`、`preview.gif`、`validation.json` 和 `project.zip`（`cubism-ready/` + `body-motion/model/` 里的 png/psd/cmo3/moc3/json/md，另含 `REVIEW.md`、`visual-repair.json` 和选中模型的视觉证据）。`collect_delivery` 会拒绝“complete 但缺少新版视觉通过报告”的产物。

Both output states retain an editable project, but only `complete` is eligible for runtime publication. The adapter writes schemaVersion 2 validation; the backend maps a failed or unavailable visual check to `needs_review` and restricts that result to a private manual handoff.

## 检查点与重试 / Checkpoints

同一输入图的 `layer_plan.json`、`decomposition/input.psd`、`expressions/*.png` 都是检查点。`--reuse-plan`、`--reuse-decomposition`、`--reuse-expressions` 减少重复请求；若规划修正了脸部裁剪范围，原表情检查点必须重做以免错位。自动修复仍可能调用图像模型；适配器在重试时自动传入最近一次尝试的检查点，只有收到 `regenerate_image` 提示时才全部作废。

Plan, decomposition PSD and expression edits are checkpoints. Reruns pass them back with `--reuse-*`; the adapter does this automatically and only discards them on a `regenerate_image` hint.

使用 `--supervisor-state` 让同一任务的重试共享三轮修复与 24 次监督请求预算，适配器默认这样做。选中模型的眼口与定位检查点会更新，失败候选仍留在各轮目录。

Use `--supervisor-state` to share the three-repair / 24-supervisor-request budget across attempts of one job; the adapter passes it automatically. Reusing checkpoints reduces repeated generation but does not disable visual review or paid local repairs.
