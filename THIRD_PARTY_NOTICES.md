# 许可范围与第三方声明 / License scope and third-party notices

根目录的 MIT 许可只覆盖本仓库自己写的 Python 流水线、适配器、测试与文档。它不改变任何上游项目、模型权重、SDK 或用户素材的许可。

The root MIT license covers this repository's own Python pipeline, adapters, tests and documentation. It does not relicense any upstream project, model weights, SDK or user artwork.

| 组件 / Component | 许可 / License | 本仓库如何处理 / Treatment |
| --- | --- | --- |
| `integrations/psd2live/ProceduralMotion.kt` | **GPL-3.0-only** | 与 GPL 引擎一起编译的衍生扩展，按 [integrations/psd2live/LICENSE](integrations/psd2live/LICENSE) 分发，**不适用**根目录 MIT。Derivative extension compiled with the GPL engine; distributed under GPL-3.0-only, not the root MIT. |
| `integrations/psd2live/BodyMotionSequence.java` | MIT | 只调用官方 Cubism Core 与 Agent Kit 的 MIT 诊断渲染器；不含任何 SDK 二进制。Calls the user's own Cubism Core and the Kit's MIT rasterizer; contains no SDK binaries. |
| [psd2live](https://github.com/tsunehimatoi/psd2live)（tsunehimatoi） | GPL-3.0 | 不随仓库分发。运行时校验锁定提交 `5526f2e16b57e5f83d34f33730d6fa26d8bc8695` 与 Agent Kit 补丁哈希，不一致拒绝运行。Not bundled; the pinned commit and patch hashes are verified at run time. |
| [Live2D Agent Kit](https://github.com/Ariakage/live2d-agent-kit)（Ariakage） | MIT + GPL-3.0（其 psd2live 补丁与集成） | 不随仓库分发，通过 `LIVE2D_KIT_DIR` 指向本地检出。本项目复用其导出脚本、Core 校验脚本与 `ManifestExport.kt` 挂载点。Not bundled; referenced through `LIVE2D_KIT_DIR`. |
| [See-through](https://github.com/shitagaki-lab/see-through)（shitagaki-lab） | Apache-2.0 | 在远程 GPU 上运行，不随仓库分发。`scripts/patch_seethrough.py` 只把隐藏的在线调度器读取改为读本地文件。Runs on the remote GPU; not bundled. |
| See-through 拆层权重 `layerdifforg/seethroughv0.0.2_layerdiff3d` | Apache-2.0（模型页声明） | 按 `scripts/models-manifest.json` 锁定版本与文件大小下载到 GPU 机，不随仓库分发。Downloaded to the GPU host at the pinned revision; not bundled. |
| See-through 深度权重 `24yearsold/seethroughv0.0.1_marigold` | **模型页未声明许可**（2026-09-20 查看）。Marigold 上游为 Apache-2.0，但该发布本身没有 LICENSE 文件。 | 同上按清单下载。使用前请自行确认；本项目不对该权重的许可做任何保证。No license stated on the model page as of 2026-09-20; verify before use. |
| Live2D Cubism Core（`Live2DCubismCore.jar` + JNI 库） | Live2D 专有许可 | **不包含、不下载、不分发。** 由使用者从自己合法安装的 Cubism Editor 或 Cubism SDK 提供，通过 `CUBISM_CORE_DIR` 指向。商用或发布请自行查阅 Live2D 的许可条款。Never included; provided by the user's own legitimate installation. |
| Live2D Cubism Editor | Live2D 专有许可 | 仅 `open-cubism` 子命令会调用本机已安装的 Editor 打开工程；不是必需依赖。Optional; only opens local projects. |
| Python 依赖（openai、Pillow、psd-tools、numpy、scipy、opencv-python-headless） | 各自许可（Apache-2.0 / HPND / MIT / BSD） | 通过 `requirements.txt` 由使用者自行安装。Installed by the user via `requirements.txt`. |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe)（Google） | Apache-2.0 | **可选**，只在启用嘴部关键点回退时使用：`scripts/install_mouth_landmarks.py` 把它装进 `dependencies/mouth-landmarks/` 下的独立 venv（它固定 numpy 1.x 与自带 OpenCV，与本仓库的 numpy>=2.0 / opencv-python-headless 冲突），并按锁定 SHA-256 下载 Face Landmarker 模型 `face_landmarker.task`（Apache-2.0）。不随仓库分发，未安装时嘴部测量自动退回暗区阈值。Optional; installed into its own venv by the installer script, never bundled. |
| Face Landmarker 模型 `face_landmarker.task` | Apache-2.0（MediaPipe 模型页） | 由安装脚本从 Google 官方地址下载并校验 SHA-256 `64184e22…`，不随仓库分发。Downloaded at a pinned digest by the installer; not bundled. |

| JDK 21、Gradle | GPL-2.0 with Classpath Exception / Apache-2.0 | 由使用者自行安装。Installed by the user. |

生成结果的著作权归提供提示词或图片的使用者；本仓库不含任何示例角色、用户图片或生成产物。上游作者保留其原始代码的著作权。

Copyright in generated characters belongs to whoever supplied the prompt or image; this repository ships no example characters, user images or generated artifacts. Upstream authors retain copyright in their original code.
