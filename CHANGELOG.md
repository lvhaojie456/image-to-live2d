# Changelog / 更新记录

## 2026-09-22 — 暗光照片的嘴部测量 / Mouth measurement on dim photos

同步安忆制作端提交 [18c8553](https://github.com/lvhaojie456/anyi-beijing/commit/18c8553)：嘴部测量从单一阈值组改为三级回退，并新增 `photo_too_dark` 诊断码。保留本仓库的通用配置与 `image_model()` 选择逻辑，未带入安忆的模型名与路径。

Ports `18c8553` from the Anyi pipeline: the mouth measurement becomes a three-tier fallback and gains a `photo_too_dark` diagnosis code. The generic configuration and `image_model()` selection are kept; no Anyi-specific model name or path is carried over.

### 新增 / Added

- 测量第二级：同一组 145/120/100 阈值，但先把整帧按「嘴周皮肤中位灰 = 175」归一化（`face_brightness` / `normalised_gray`）。暗光照片在此恢复；牙齿与舌头的阈值同步用归一化后的帧。
- 测量第三级（可选）：人脸关键点的内唇 20 点环，由 `scripts/mouth_landmarks.py` 在独立 venv 里跑（`scripts/install_mouth_landmarks.py` 建环境并按 SHA-256 下载 `face_landmarker.task`）。接受判据是占**规划嘴框**的 5%–150%，避免路人或插画脸把测量带偏；未安装时自动退回前两级。
- 诊断码 `photo_too_dark`（建议 `new_input`）：嘴部测不出且嘴周皮肤中位灰 < 120 时由规则层强制，覆盖模型的 `face_not_located` 判断。测量证据写入 `mouth-measurement.json`，与兜底定位都失败时也保留。
- 二级与三级测量、辅助器降级、安装脚本 `--check`、证据保留与 `dark_photo_failure` 判定的测试。

### 修改 / Changed

- 监督层白名单、默认建议与文案新增 `photo_too_dark`；三方一致性测试覆盖。
- 文档同步：`docs/pipeline.md` 的两级回退与中英说明、`docs/supervisor.md` 的码清单、`docs/queue-protocol.md` 的服务端过滤白名单。

### 兼容性 / Compatibility

- 辅助器是**可选**依赖：不安装时行为与之前一致（暗区阈值），不会报错。它需要独立 venv，因为 MediaPipe 固定 numpy 1.x 与本仓库的 numpy>=2.0 冲突。
- 新增诊断码对旧服务端是纯增量；`adapters/anyi/worker.py` 只是转发字段，未改动。

### 验证 / Validation

- `python -m unittest discover -s tests`：59 项通过（Python 3.11/3.12，无 GPU、无 API key、无 Cubism Core，全部外部调用为 mock）。本地用与 CI 相同的依赖集（不装 MediaPipe）跑通，以确认辅助器缺失时自动降级。本地用与 CI 相同的依赖集（不装 MediaPipe）跑通，以确认辅助器缺失时自动降级。
- 安忆侧的线上回放：失败的暗光照片在归一化后 145 一档通过，轮廓贴住口腔；插画与提示词任务的既有路径未变。

## 2026-09-20 — 导出模型视觉修复 / Exported-model visual repairs

同步安忆制作端提交 [2a794f9](https://github.com/lvhaojie456/anyi-beijing/commit/2a794f9) 的视觉修复与新版交付协议。保留独立仓库的 `adapters/anyi/` 目录、`LLM_*` / `PLANNER_*` 通用配置、旧变量别名、许可与中英文说明。

Ports the visual-repair and delivery changes from Anyi commit `2a794f9`, retaining the standalone adapter layout, neutral configuration names and legacy aliases.

### 新增 / Added

- 实际导出模型的表情、身体极值、连续身体帧和连续脸部帧验收，结构采样从 363 增至 370。
- 最多三轮视觉修复：按问题重做眼口素材、拆层或绑定；每轮保留工程，身份/动作退化回退，连续两轮无改善停止。
- `needs_review` 人工处理交付，包含精修工程、问题说明和所选版本的证据。结构和视觉均通过才允许发布为可用形象。
- `adapters/anyi/nginx-location.conf` 通用示例：Live2D 路径 256 MB / 300 秒。需安装在实际加载的 Nginx server 中；此仓库不操作已有线上服务。
- 视觉验收、候选回退、跨重试预算、不同尺寸表情配准、模型配置和上传超时测试。

### 修改 / Changed

- 默认监督模式 `shadow` → `act`。最多 24 次实际监督模型请求（包含重试），预算原子写入并跨同一任务重试共享。局部表情返修会调用图像模型并计费；原图重生成仍需使用者另行发起。
- 表情编辑恢复遮罩外原图像素；闭眼遮罩覆盖睫毛、眼白和虹膜；不同分辨率的眼口编辑分别配准到模型画布。
- `adapters/anyi/worker.py` 输出 schemaVersion 2 视觉报告，上传超时 90 → 300 秒，控制请求仍为 90 秒。
- README、流水线、监督与队列协议文档同步为中英文说明。

The default supervisor mode is now `act`. Repairs use the configured image model and may incur charges. Uploads allow 300 seconds; control requests retain 90 seconds. The adapter emits versioned visual validation and preserves manual-handoff projects.

### 兼容性 / Compatibility

队列后端须配套支持 schemaVersion 2、`repairing` 和 `needs_review`。必须独立验证视觉结论，对人工处理结果禁止绑定及运行文件读取，只开放本人精修工程/静态预览/验证报告下载。旧版仅检查结构的后端不能安全接入。已有成功模型无需重新生成。

The queue backend must implement schemaVersion 2, the `repairing` stage and the `needs_review` terminal state. It must recompute visual acceptance and deny manual-handoff runtime access and binding. Existing successful models do not need regeneration.

### 验证 / Validation

- 本地 49 项单元测试通过；适配器单独运行测试也通过，不依赖其它测试预先修改 Python 导入路径。
- 使用已验证的真实产物，与新版安忆后端在隔离本地数据库联调：成功样本正常发布/绑定，人工样本为 `needs_review`、绑定返回 409、运行资产返回 404，两者私有 ZIP 下载通过，未登录下载返回 401。
- 联调复用已有产物，本次未重复执行付费生成；人工 Cubism 精修与另存重开兼容性仍需单独验收。

49 local unit tests passed. Local HTTP integration with an isolated Anyi backend verified successful publication/binding and a private, unbindable manual handoff using previously validated real artifacts. This synchronization did not rerun paid generation or Cubism GUI acceptance.
