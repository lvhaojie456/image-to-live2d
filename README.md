# image-to-live2d

[English](#english) · [中文](#中文)

一张立绘或一段提示词 → 一个能在 Cubism Core 里加载、会呼吸、倾斜、眨眼、口型可驱动的 Live2D 初版模型，外加一份可在 Cubism Editor 继续精修的分层工程。

One portrait or one prompt → a first-pass Live2D model that loads in Cubism Core, breathes, leans, blinks and exposes lip-sync, plus a layered project you can keep refining in Cubism Editor.

---

## 中文

### 它是什么

这是一条把单张角色图变成 Live2D 模型的自动化流水线。它不是 Cubism Editor 的替代品：产出的是**待精修的初版**，每个模型都附带分层 PSD、`.cmo3` 工程和验证报告，让美术从一个已经能动的起点开始，而不是从零开始拆层。

九个基础阶段由代码串联，可恢复的错误先按规则处理，导出后再做视觉验收与有次数上限的修复：

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
| 9 检查模型 | 官方 Cubism Core 求值 370 个姿态，渲染表情、极值和连续帧：三角形不翻转、脚底位移 < 0.25 px | 本机 JVM |

在此之上还有一个**监督层**：默认 `act` 会检查实际导出模型的表情、身体极值及连续脸部/身体帧，并对照原图检查身份。严重问题按固定菜单修复，最多 **3 轮**；每轮保留独立工程，修坏回退，连续两轮无改善停止。局部图像编辑会调用配置的图像模型并产生费用，整张人物重生成仍由使用者另行发起。视觉检查缺失或严重问题未解决时输出 `needs_review`，保留工程供人工精修，不能发布为可用形象。细节见 [docs/pipeline.md](docs/pipeline.md) 与 [docs/supervisor.md](docs/supervisor.md)。

### 产物

一次成功的构建输出目录里有：

- `body-motion/model/` — `.moc3`、一张 4096 纹理图集、`physics3`、`cdi3`、待机/呼吸/倾斜/手臂/裙摆/表情六组 `motion3`、`.cmo3` 工程。
- `cubism-ready/` — 分组好的 `cubism_refinement.psd`（33 层左右）、`expression_parts.psd`、四张表情合成图、`validation.json` 与精修说明。
- `build.json` — `status: complete`（结构与视觉均通过）或 `needs_review`（需要人工处理），以及每个阶段的记录。
- `visual-rounds/round-00..03/` — 每轮工程和导出模型；`visual-evidence/` 保存选中版本的表情/动作截图及 SHA-256。
- `visual-repair.json` / `REVIEW.md` — 修复、回退、选用版本和待人工处理的问题。

作为服务运行时（见 `adapters/`），上传运行资产、预览、验证报告和精修 ZIP。成功产物允许绑定；`needs_review` 只允许本人下载工程与静态预览，后端必须禁止其运行资产读取和绑定。ZIP 含检查证据与精修说明，不含脚本、原始供应商日志或每轮完整工程。

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
.venv/bin/python -m unittest discover -s tests   # 单元测试不需要 GPU 和密钥
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

同一输入的重跑可通过 `--reuse-plan`、`--reuse-decomposition`、`--reuse-expressions` 复用检查点，减少重复请求；自动修复仍可能产生新的图像编辑调用。`--supervisor-state work/job-state.json` 让同一任务的重试共用预算；队列适配器自动设置。进度通过 `LIVE2D_PROGRESS_FILE` 对外暴露，`repairing` 表示正在修复并复检。进程成功退出后仍须检查 `build.json.status`，`needs_review` 不代表视觉通过。

### 作为服务

`adapters/anyi/worker.py` 是一个只出站的轮询适配器：向任务队列领取任务、按租约续约、跑 `build`、逐文件带 SHA-256 上传、上报完成或带白名单诊断码的失败。它不需要在制作主机上开任何入站端口。队列一侧的六个 worker 接口写在 [docs/queue-protocol.md](docs/queue-protocol.md) 里，你可以用任何后端实现。服务端需支持新版视觉报告（`schemaVersion: 2`）和 `needs_review`；旧版后端只看结构检查会错误发布，必须配套升级。文件上传超时为 300 秒，提供 [256 MB Nginx location 示例](adapters/anyi/nginx-location.conf)。

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

Nine base stages run in order, followed by exported-model visual review and bounded repairs. Recoverable failures use rule-based fallbacks:

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
| 9 Verify | official Cubism Core evaluates 370 poses and renders expressions, extremes and sequences: no flipped triangles, feet drift < 0.25 px | local JVM |

The **supervisor** defaults to `act`: it checks expressions, body extremes and consecutive face/body frames from the exported model against the original identity. Major defects trigger a fixed repair menu for at most **3 rounds**. Every round is retained; regressions roll back, and two rounds without improvement stop the loop. Local expression edits use your image model and can incur charges. Full-portrait regeneration remains a separate user action. Missing visual evidence or unresolved major defects produce `needs_review`: a retained editing project, not a usable avatar. See [docs/pipeline.md](docs/pipeline.md) and [docs/supervisor.md](docs/supervisor.md).

### Outputs

A successful build directory contains:

- `body-motion/model/` — `.moc3`, one 4096 texture atlas, `physics3`, `cdi3`, six `motion3` groups (idle, breathing, lean, arms, skirt, expressions) and the `.cmo3` project.
- `cubism-ready/` — the grouped `cubism_refinement.psd` (about 33 layers), `expression_parts.psd`, four expression composites, `validation.json` and refinement notes.
- `build.json` — `status: complete` when structural and visual checks pass, or `needs_review` for a manual handoff, plus stage records.
- `visual-rounds/round-00..03/` — every candidate project and exported model; `visual-evidence/` contains selected expression/motion screenshots and SHA-256 hashes.
- `visual-repair.json` / `REVIEW.md` — repair decisions, rollbacks, selected round and issues for the artist.

The service adapter uploads referenced runtime assets, previews, validation and the editing ZIP. Successful models may be bound; `needs_review` permits owner-only project/static-preview downloads while the server must deny runtime access and binding. The ZIP includes review evidence and notes, but excludes scripts, raw provider logs and complete per-round workspaces.

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
.venv/bin/python -m unittest discover -s tests   # unit tests need no GPU or API key
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

Use `--reuse-plan`, `--reuse-decomposition` and `--reuse-expressions` for the same input to reduce repeated requests; repairs can still invoke paid image edits. Pass `--supervisor-state work/job-state.json` to share the budget across retries of one job; the queue adapter does this automatically. `LIVE2D_PROGRESS_FILE` exposes progress, including `repairing`. A successful process exit can still mean `needs_review`; read `build.json.status` before publishing.

### As a service

`adapters/anyi/worker.py` is an outbound-only polling adapter: it claims jobs from a queue, renews a lease, runs `build`, uploads each file with its SHA-256, and reports completion or a whitelisted failure diagnosis. No inbound port is needed on the build host. The six worker endpoints are specified in [docs/queue-protocol.md](docs/queue-protocol.md) and can be implemented on any backend. The server must support visual validation `schemaVersion: 2` and `needs_review`; upgrade a server that only checks geometry before connecting this adapter. File uploads allow 300 seconds; a [256 MB Nginx location example](adapters/anyi/nginx-location.conf) is included.

### Limitations

- A single image cannot reveal occluded pixels; closed eyes and the open mouth are measured from two edited images, not continuous keyforms.
- The `.cmo3` is produced by psd2live: it opens in Cubism Editor 5.3 and shows parameters, but with compatibility warnings, and save-and-reopen has not been fully validated.
- Verified on prompt-generated art and a small number of photos; beard shadows and dark backgrounds in realistic photos remain the main failure sources.
- For commercial release, evaluate Live2D's license terms yourself; this project contains no proprietary Live2D component.

### License

Code is MIT (`LICENSE`). `integrations/psd2live/ProceduralMotion.kt` derives from the GPL-3.0 engine and is distributed under GPL-3.0-only. Third-party components and weights are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
