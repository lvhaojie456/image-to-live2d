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

## 1. 生成立绘 / Generate

提示词后固定追加"正面完整角色立绘，干净背景，四肢不裁切，适合 Live2D 拆层"，按 `--size`（默认 1024×1536）调用 `images.generate`。`IMAGE_BACKGROUND=transparent` 时先请求透明背景；供应商返回 4xx 就回退到普通生成，5xx 直接失败。给了 `--image` 时这一步只是拷贝。

The prompt gets a fixed suffix asking for a front-facing, uncropped, clean-background full-body portrait. With `IMAGE_BACKGROUND=transparent` a transparent background is requested first and a 4xx from the provider falls back to a plain request. With `--image` this stage is a copy.

## 2. 背景中和 / Neutralize the background

`foreground.neutralize_background` 把透明像素或"角落近白且与边框连通"的区域填成中性灰 `(210,210,210)`，同时输出 `foreground/input_mask.png`。

原因：See-through 会把**纯白背景当作人物**并入某个衣服图层（实测同一张图白底时 `topwear` 占画布 79%，灰底时 10%）。中性灰能干净地分开。

See-through folds a pure white background into a garment layer (79% of the canvas vs 10% on grey for the same image), so transparent or near-white backgrounds become neutral grey before decomposition, and a foreground mask is kept for stage 5.

## 3. 分析形象 / Plan

把中和后的图发给视觉模型，要求严格 JSON：`character_summary`、`regions`（脸、双眼、嘴的像素矩形，无法定位填 null）、`layers`、`repair_tasks`、`parameters`、`physics`、`acceptance_poses`、`risks`。流式接收，`finish_reason != stop` 视为不完整并失败；传输层中断按 `PLANNER_STREAM_RETRIES` 重发同一请求。

**只有 `regions` 被机器消费**：后面裁脸和表情编辑全靠它。矩形超出图像或缺失时按脸部矩形的固定比例推算，脸部矩形缺失时回退到拆层结果里的 `face` 图层外接框，再不行用画布中上部的固定比例。图层规划与补画清单写成 `layer_plan.md`，随精修包交给人。

Only `regions` is consumed by code; the rest becomes `layer_plan.md` for the artist. Missing or out-of-range boxes fall back to fixed ratios of the face box, then to the `face` layer's bbox from decomposition, then to a fixed canvas fraction.

## 4. 拆分图层 / Decompose

`remote_decompose` 把图 `scp` 到 `REMOTE_SSH_HOST`，连同 `scripts/remote_worker.py`、`scripts/infer_staged.py`、`scripts/models-manifest.json` 一起放进一个新的任务目录，用 `nohup` 起一个独立进程，然后每 10 秒 `ssh cat status.json` 轮询。任务目录与 `remote_job.json` 让中断后可以恢复而不是重跑。

远端三步（`infer_staged.py`）：LayerDiffusion 拆层（30 步、种子 42、`--resolution` 默认 1280）→ 释放显存 → Marigold 逐部件深度图（768）→ 导出 PSD（`tblr_split`）。输出约 27 个部件 PNG、同名 `_depth.png`、`input.psd`。完成后打成 `result.zip`（存储不压缩）连同逐文件 SHA-256 清单返回；本机校验 zip 哈希、拒绝路径穿越和符号链接、逐文件核对。

The image and the two small scripts go to the GPU host over `scp`; a detached process runs LayerDiffusion (30 steps, seed 42), then Marigold depth per part, then PSD export, and writes `status.json` polled every 10 s. The zip comes back with a per-file SHA-256 manifest and is verified before extraction.

### 泄漏裁剪 / Clip leaks

`foreground.clip_background` 检查每个图层，满足任一条即判定裹了背景：外接框超过画布 60%；按 `input/<part>_depth.png` 饱和（≥ 250）像素超过 30%；落在前景遮罩外的像素超过 30%。只对这些层重建：保留 深度 < 250 且在遮罩内 的像素，形态学开运算、保留 ≥ 最大连通域 2% 的区块、填洞，写出 `decomposition/input_clipped.psd`；其余层逐字节不变。

A layer is flagged when its bbox exceeds 60% of the canvas, or more than 30% of its pixels sit at saturated depth (≥ 250) or outside the foreground mask. Only flagged layers are rebuilt; every other layer is copied byte for byte.

## 5. 生成表情 / Expressions

按脸部矩形裁一块正方形（外扩 45%），做两次 `images.edit`：遮罩只露出双眼或嘴，提示词要求保持年龄、身份、画风、胡子、皱纹和所有未遮罩像素。得到 `expression_eyes.png`（闭眼）与 `expression_mouth.png`（张嘴）。

然后**测量**而不是套矩形（`auto_expression.py`）：张嘴图转灰度，在嘴部搜索框内按 `145 → 120 → 100` 三档暗部阈值找最大暗区（写实风格的胡子阴影会把第一档撑满），取凸包、填洞、膨胀一像素得到嘴腔轮廓；轮廓内 灰度 > 155 且在上 48% 的为牙，下半部 红 > 绿×1.3 的为舌；最暗点作为口腔填充采样。闭眼按拆层结果里睫毛图层的位置取一块羽化贴片。三档都失败时监督层可以让模型标一个嘴框重测。

Instead of pasting rectangles, the open-mouth edit is measured: the largest dark blob near the mouth at thresholds 145 → 120 → 100, its convex hull as the cavity, bright upper pixels as teeth, red lower pixels as tongue. Closed eyes become a feathered patch positioned by the eyelash layer.

## 6. 整理精修素材 / Refinement package

`face_assets.build_face_assets` 把测量出的 `mouth_open`、`tooth-t`、`tongue`、`lip_upper`、`lip_lower`、`eye_close-l/r` 从编辑图坐标配准到模型画布（中心正方形填充后缩放，逆向仿射一次采样），逐个校验哈希。

`build_refinement_package.build` 再把拆层结果整理成 Cubism 习惯：`prepare_rig_layers` 按固定语义顺序（后发 → 腿 → 鞋 → 手 → 下装 → 上衣 → 脖子 → 耳 → 脸 → 鼻 → 眼白 → 虹膜 → 睫毛 → 眉 → 嘴 → 前发 → 头饰）重排并清掉孤立碎像素；腿、鞋在画布中线一分为二成 `-l`/`-r`；手臂在袖口连线处切成 `arm-*` 与 `hand-*`（单一 `handwear` 层先按中线分左右再切袖口）；没有独立嘴层时从脸上按测量区域抠出 `mouth_close`。最后写出分组的 `cubism_refinement.psd` 与只含表情层的 `expression_parts.psd`，并把 PSD **读回来逐像素比对**，层数、画布、可见性、像素任一不符即失败。

Measured expression features are registered onto the model canvas with a single inverse-affine resample. Layers are reordered semantically, legs/shoes split at the midline, arms cut at the cuff into sleeve and hand, and the resulting PSD is read back and compared pixel by pixel before the stage passes.

## 7. 制作动作 / Rig

`body_motion.build_body_motion` 先核对 psd2live 的提交（`5526f2e…`）与 Agent Kit 锁文件里的补丁哈希，不一致拒绝运行。然后在 Agent Kit 的 `ManifestExport.kt` 挂载点插入 `integrations/psd2live/ProceduralMotion.kt`，通过 Gradle 的 `exportManifest` 任务导出。

引擎本身做网格剖分、变形器树、头部/身体参数、物理、眨眼与点头/摇头动作。扩展在此之上加三个子变形器（左臂、右臂、裙摆；没有裙子时改为衣摆）并按 `motion_recipe()` 测量出的肩线、胸口、髋部位置写呼吸与倾斜的关键形。`motion_document()` 用正弦函数生成 `loop_seconds`（8 秒）× `fps`（30）的循环，拆成 BodyIdle、Expressions、Breathing、BodyLean、Arms、Skirt 六个 `motion3`，并在 `model3.json` 里登记 `BodyMotion` / `ExpressionMotion` 参数组。

The engine commit and patch hashes are verified first. psd2live builds meshes, deformers, physics, blink and nod; the extension adds arm and garment warps with measured anchors and writes six procedural `motion3` loops.

## 8. 检查模型 / Verify

`verify_body_motion.run` 用 JDK 编译 Agent Kit 的 `render_core.java`、`validate_core.java` 与本仓库的 `BodyMotionSequence.java`，链接 `CUBISM_CORE_DIR` 里的官方 Core，渲染一张 `poses.tsv`：五个身体参数的全部极值组合、每个参数的单独极值、待机循环按 12 fps 采样的帧、四个单项动作各 24 帧，共 363 个姿态。检查：顶点有限、无翻转与退化三角形、**脚底位移 < 0.25 px**。同时验证 `ParamArm*` 与 `ParamSkirtSwing` 只影响它们该影响的网格。

失败时如果只是脚底位移或翻转（`verification_failure()` 判定为 tunable），按 `MOTION_SCALES = (1.0, 0.66, 0.33)` 缩小全部幅度重新导出重新校验，最多两次，结果记在 `validation.json` 的 `motionScale`。

363 poses are rendered through the official Core: every extreme combination of the five body parameters, each parameter alone, the idle loop at 12 fps and 24 frames of each single motion. Finite vertices, no flipped or degenerate triangles, feet drift below 0.25 px. Drift-only failures shrink all amplitudes 1.0 → 0.66 → 0.33 and retry.

## 9. 交付 / Deliver

单机使用时输出目录本身就是产物（README 里有清单）。作为服务时 `adapters/anyi/worker.py` 的 `collect_delivery` 只挑运行时引用到的文件复制到 `runtime/`，**删掉待机动作里的 `ParamMouthOpenY` / `ParamMouthForm` 曲线**（否则和聊天口型打架），把中性姿态的外接框写进 `model3.json` 的 `AnyiBounds`，附 `preview.png`、`preview.gif`、`validation.json` 和 `project.zip`（`cubism-ready/` + `body-motion/model/` 里的 png/psd/cmo3/moc3/json/md）。

## 检查点与重试 / Checkpoints

同一输入图的 `layer_plan.json`、`decomposition/input.psd`、`expressions/*.png` 都是检查点。`--reuse-plan`、`--reuse-decomposition`、`--reuse-expressions` 让重跑跳过付费步骤；适配器在重试时自动传入最近一次尝试的检查点，只有收到 `regenerate_image` 提示时才全部作废。

Plan, decomposition PSD and expression edits are checkpoints. Reruns pass them back with `--reuse-*`; the adapter does this automatically and only discards them on a `regenerate_image` hint.
