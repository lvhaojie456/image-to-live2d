# image-to-live2d

[English](#english) · [中文](#中文)

一张立绘或一段提示词 → 一个能在 Cubism Core 里加载、会呼吸、倾斜、眨眼、口型可驱动的 Live2D 初版模型，外加一份可在 Cubism Editor 继续精修的分层工程。

One portrait or one prompt → a first-pass Live2D model that loads in Cubism Core, breathes, leans, blinks and exposes lip-sync, plus a layered project you can keep refining in Cubism Editor.

---

## 中文

### 它是什么

这是一条把单张角色图变成 Live2D 模型的自动化流水线。它不是 Cubism Editor 的替代品：产出的是**待精修的初版**，每个模型都附带分层 PSD、`.cmo3` 工程和验证报告，让美术从一个已经能动的起点开始，而不是从零开始拆层。

九个阶段，全部由代码串起来，任一阶段失败都有确定性的恢复动作：

| 阶段 | 做什么 | 在哪里算 |
| --- | --- | --- |
| 1 生成立绘 | 提示词 → 1024×1536 正面全身图，优先透明背景（给图片则跳过） | OpenAI 兼容图像模型 |
| 2 背景中和 | 透明/近白背景填成中性灰，算出前景遮罩 | 本机 |
| 3 分析形象 | 视觉模型给出脸/眼/嘴矩形、图层规划、补画清单（严格 JSON） | OpenAI 兼容视觉模型 |
| 4 拆分图层 | See-through 拆成约 27 个语义部件 + 逐部件深度图 | 远程 GPU（24 GB） |
| 5 泄漏裁剪 | 按深度图和遮罩找出裹了背景的图层，只重建这一层 | 本机 |
| 6 生成表情 | 两次遮罩编辑得到闭眼/张嘴，用 OpenCV **测量**嘴腔、牙、舌、眼皮 | 图像模型 + 本机 |
| 7 整理精修素材 | 重排顺序、左右拆分、袖手分离、表情配准，写出并回读校验 PSD | 本机 |
| 8 制作动作 | psd2live 引擎做网格/变形器/物理，加手臂与裙摆变形器和程序化待机循环 | 本机 JVM |
| 9 检查模型 | 官方 Cubism Core 渲染 363 个姿态：三角形不翻转、脚底位移 < 0.25 px | 本机 JVM |

在此之上还有一个**监督层**：一个视觉模型在规划、拆层、表情三个关卡各看一次缩略图，最后给初版打分；失败时从固定菜单里选下一步（重规划、裁背景、缩小动作幅度、重做表情），每单有预算，付费动作永远只变成建议。默认 `shadow` 模式只记录不干预。细节见 [docs/pipeline.md](docs/pipeline.md) 与 [docs/supervisor.md](docs/supervisor.md)。

### 产物

一次成功的构建输出目录里有：

- `body-motion/model/` — `.moc3`、一张 4096 纹理图集、`physics3`、`cdi3`、待机/呼吸/倾斜/手臂/裙摆/表情六组 `motion3`、`.cmo3` 工程。
- `cubism-ready/` — 分组好的 `cubism_refinement.psd`（33 层左右）、`expression_parts.psd`、四张表情合成图、`validation.json` 与精修说明。
- `build.json` — 每个阶段的记录、监督层的审查结果与评分。

作为服务运行时（见 `adapters/`），只发布运行时引用到的文件、预览图和精修 ZIP，不发布脚本与日志。

### 环境要求

流水线分布在两台机器上：

- **制作主机**：macOS 或 Windows（因为 Cubism Core 的 JNI 库只随 Cubism Editor 提供这两个平台的版本）。Python 3.11+（3.12 实测），JDK 21，Gradle 由 psd2live 自带。
- **GPU 主机**：任意 Linux，24 GB 显存的 NVIDIA 卡（4090 实测），可从制作主机免密 SSH 登录。See-through 的拆层推理只在这里跑。

本仓库**不包含**、也不会替你下载这些依赖，请分别获取并遵守各自许可（见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）：

| 依赖 | 放在哪 | 备注 |
| --- | --- | --- |
| [Live2D Agent Kit](https://github.com/Ariakage/live2d-agent-kit) | 制作主机，`LIVE2D_KIT_DIR` | 提供导出脚本、Core 校验脚本与 psd2live 补丁 |
| [psd2live](https://github.com/tsunehimatoi/psd2live) 提交 `5526f2e` | 制作主机，`LIVE2D_ENGINE_DIR` | 用 Agent Kit 的 `scripts/setup-psd2live.py --source-only` 安装；运行时校验提交与补丁哈希 |
| Cubism Core（`Live2DCubismCore.jar` + JNI 库） | 制作主机，`CUBISM_CORE_DIR` | 来自你自己安装的 Cubism Editor 5.x 的 `res/` 目录 |
| [See-through](https://github.com/shitagaki-lab/see-through) + 权重 | GPU 主机，`REMOTE_SEE_THROUGH` / `REMOTE_MODEL_ROOT` | 权重按 `scripts/models-manifest.json` 锁定版本，用 `scripts/download_models.py` 下载并校验；跑一次 `scripts/patch_seethrough.py` |
| 一个 OpenAI 兼容接口 | `.env` | 需要图像生成（最好支持透明背景）、带遮罩的图像编辑、能读图并返回 JSON 的视觉模型 |

### 安装

```bash
git clone https://github.com/lvhaojie456/image-to-live2d.git
cd image-to-live2d
bash setup_project.sh          # 建 .venv、装依赖、生成 .env
$EDITOR .env                   # 填入密钥、模型名、SSH 主机别名与本机工具路径
.venv/bin/python -m unittest discover -s tests   # 38 项，不需要 GPU 和密钥
```

GPU 主机上：把 See-through 检出到 `REMOTE_SEE_THROUGH`，在其 Python 环境里安装它的依赖，然后

```bash
python scripts/download_models.py scripts/models-manifest.json /path/to/models
python scripts/patch_seethrough.py /path/to/see-through
```

`scripts/remote_worker.py` 与 `scripts/infer_staged.py` 会在每次任务时自动复制到 GPU 主机，不需要预先部署。

### 用法

```bash
# 提示词 → 完整模型
.venv/bin/python live2d_pipeline.py build --prompt '正面站姿的全身老爷爷，穿灰色毛衣' --output outputs/grandpa

# 图片 → 完整模型
.venv/bin/python live2d_pipeline.py build --image /path/to/character.png --output outputs/character

# 单步子命令：generate / plan / remote-decompose / body-motion / checklist / open-cubism
.venv/bin/python live2d_pipeline.py --help
```

失败后重跑可以复用检查点，不重复付费：`--reuse-plan`、`--reuse-decomposition`、`--reuse-expressions`。进度通过 `LIVE2D_PROGRESS_FILE` 指向的 JSON 文件对外暴露。

### 作为服务

`adapters/anyi/worker.py` 是一个只出站的轮询适配器：向任务队列领取任务、按租约续约、跑 `build`、逐文件带 SHA-256 上传、上报完成或带白名单诊断码的失败。它不需要在制作主机上开任何入站端口。队列一侧的协议只有六个接口，写在 [docs/queue-protocol.md](docs/queue-protocol.md) 里，你可以用任何后端实现。

### 局限

- 单张图看不到被遮挡的像素；闭眼和张嘴是两张编辑图测量出来的，不是连续关键形。
- `.cmo3` 由 psd2live 生成，在 Cubism Editor 5.3 里能打开、显示参数，但会有兼容性提示，另存重开未完整验收。
- 只在提示词生成的立绘和少量真人照片上验证过；写实照片的胡子阴影、深色背景仍是失败的主要来源。
- 商用发布请自行评估 Live2D 的许可条款；本项目不含任何 Live2D 专有组件。

### 许可

代码为 MIT（`LICENSE`）。`integrations/psd2live/ProceduralMotion.kt` 是 GPL-3.0 引擎的衍生代码，按 GPL-3.0-only 分发。第三方组件与权重的许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## English

### What it is

An automated pipeline that turns a single character image into a Live2D model. It does not replace Cubism Editor: the output is a **first pass meant to be refined**, and every model ships with the layered PSD, the `.cmo3` project and a verification report, so an artist starts from something that already moves instead of cutting layers from scratch.

Nine stages, all driven by code, each with a deterministic recovery when it fails:

| Stage | What happens | Where |
| --- | --- | --- |
| 1 Generate | prompt → 1024×1536 front-facing full-body image, transparent background preferred (skipped when you supply an image) | OpenAI-compatible image model |
| 2 Neutralize background | transparent / near-white background filled with neutral grey; foreground mask computed | local |
| 3 Plan | a vision model returns face / eye / mouth boxes, a layer plan and repaint list as strict JSON | OpenAI-compatible vision model |
| 4 Decompose | See-through splits the image into ~27 semantic parts plus per-part depth maps | remote GPU (24 GB) |
| 5 Clip leaks | layers that swallowed the background are detected via depth + mask and rebuilt alone | local |
| 6 Expressions | two masked edits give closed eyes and an open mouth; OpenCV **measures** cavity, teeth, tongue and eyelids | image model + local |
| 7 Refinement package | reorder, split left/right, separate sleeves from hands, register expressions, write and read back the PSD | local |
| 8 Rig | psd2live builds meshes / deformers / physics; arm and skirt warps plus a procedural idle loop are added | local JVM |
| 9 Verify | the official Cubism Core renders 363 poses: no flipped triangles, feet drift < 0.25 px | local JVM |

On top sits a **supervisor**: a vision model reviews thumbnails at the planning, decomposition and expression gates and scores the result; on failure it picks the next step from a fixed menu (replan, clip background, shrink motion, redo expressions) within a per-job budget, and paid actions only ever become suggestions. The default `shadow` mode records without intervening. See [docs/pipeline.md](docs/pipeline.md) and [docs/supervisor.md](docs/supervisor.md).

### Outputs

A successful build directory contains:

- `body-motion/model/` — `.moc3`, one 4096 texture atlas, `physics3`, `cdi3`, six `motion3` groups (idle, breathing, lean, arms, skirt, expressions) and the `.cmo3` project.
- `cubism-ready/` — the grouped `cubism_refinement.psd` (about 33 layers), `expression_parts.psd`, four expression composites, `validation.json` and refinement notes.
- `build.json` — every stage's record plus the supervisor's gate results and score.

When run as a service (see `adapters/`), only runtime-referenced files, previews and the refinement ZIP are published; never scripts or logs.

### Requirements

The pipeline spans two machines:

- **Build host**: macOS or Windows, because the Cubism Core JNI library only ships with Cubism Editor for those platforms. Python 3.11+ (3.12 tested), JDK 21; Gradle comes with psd2live.
- **GPU host**: any Linux box with a 24 GB NVIDIA GPU (tested on a 4090), reachable from the build host over passwordless SSH. See-through inference runs only here.

This repository does **not** bundle or download the following; obtain each yourself and respect its license (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)):

| Dependency | Where | Notes |
| --- | --- | --- |
| [Live2D Agent Kit](https://github.com/Ariakage/live2d-agent-kit) | build host, `LIVE2D_KIT_DIR` | export scripts, Core validators and the psd2live patch |
| [psd2live](https://github.com/tsunehimatoi/psd2live) at `5526f2e` | build host, `LIVE2D_ENGINE_DIR` | install with the Kit's `scripts/setup-psd2live.py --source-only`; commit and patch hashes are verified at run time |
| Cubism Core (`Live2DCubismCore.jar` + JNI library) | build host, `CUBISM_CORE_DIR` | from the `res/` directory of your own Cubism Editor 5.x installation |
| [See-through](https://github.com/shitagaki-lab/see-through) + weights | GPU host, `REMOTE_SEE_THROUGH` / `REMOTE_MODEL_ROOT` | weights pinned in `scripts/models-manifest.json`, fetched and verified by `scripts/download_models.py`; run `scripts/patch_seethrough.py` once |
| an OpenAI-compatible API | `.env` | image generation (ideally with transparent background), masked image editing, and a vision model that returns JSON |

### Install

```bash
git clone https://github.com/lvhaojie456/image-to-live2d.git
cd image-to-live2d
bash setup_project.sh          # creates .venv, installs deps, writes .env
$EDITOR .env                   # keys, model names, SSH host alias, local tool paths
.venv/bin/python -m unittest discover -s tests   # 38 tests; no GPU or API key needed
```

On the GPU host: check out See-through at `REMOTE_SEE_THROUGH`, install its requirements in its Python environment, then

```bash
python scripts/download_models.py scripts/models-manifest.json /path/to/models
python scripts/patch_seethrough.py /path/to/see-through
```

`scripts/remote_worker.py` and `scripts/infer_staged.py` are copied to the GPU host per job; nothing needs to be pre-deployed.

### Usage

```bash
# prompt → full model
.venv/bin/python live2d_pipeline.py build --prompt 'full-body elderly man, front view, grey sweater' --output outputs/grandpa

# image → full model
.venv/bin/python live2d_pipeline.py build --image /path/to/character.png --output outputs/character

# single steps: generate / plan / remote-decompose / body-motion / checklist / open-cubism
.venv/bin/python live2d_pipeline.py --help
```

Reruns after a failure reuse checkpoints so nothing is paid for twice: `--reuse-plan`, `--reuse-decomposition`, `--reuse-expressions`. Progress is exposed through the JSON file named by `LIVE2D_PROGRESS_FILE`.

### As a service

`adapters/anyi/worker.py` is an outbound-only polling adapter: it claims jobs from a queue, renews a lease, runs `build`, uploads each file with its SHA-256, and reports completion or a whitelisted failure diagnosis. No inbound port is needed on the build host. The queue side is six endpoints, specified in [docs/queue-protocol.md](docs/queue-protocol.md), and can be implemented on any backend.

### Limitations

- A single image cannot reveal occluded pixels; closed eyes and the open mouth are measured from two edited images, not continuous keyforms.
- The `.cmo3` is produced by psd2live: it opens in Cubism Editor 5.3 and shows parameters, but with compatibility warnings, and save-and-reopen has not been fully validated.
- Verified on prompt-generated art and a small number of photos; beard shadows and dark backgrounds in realistic photos remain the main failure sources.
- For commercial release, evaluate Live2D's license terms yourself; this project contains no proprietary Live2D component.

### License

Code is MIT (`LICENSE`). `integrations/psd2live/ProceduralMotion.kt` derives from the GPL-3.0 engine and is distributed under GPL-3.0-only. Third-party components and weights are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
