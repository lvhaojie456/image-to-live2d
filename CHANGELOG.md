# Changelog / 更新记录

## 2026-09-21 — 连续眼口关键形 / Continuous eye and mouth keyforms

### 修复 / Fixed

- 待机眨眼的余弦窗口原先覆盖多个周期，导致一次眨眼内重复闭合并在窗口边缘跳变；改为一次平滑闭合/张开脉冲。
- 新增可选 `LIVE2D_CONTINUOUS_EXPRESSIONS=true`，用于本次卡通人物的连续眼口绑定：局部清除脸底图的眼口残留，按闭眼素材测量曲线与颜色生成平滑眼睑线，眼白闭合与虹膜遮挡协同，避免半透明完整眼形叠加。
- 连续嘴形保留原始上下唇的厚度和颜色，牙舌恢复独立语义并受口腔遮罩限制，避免所有嘴部素材一起被压成一条硬缝；嘴角参数具备可见变化。
- 眼口局部网格加密，保留原图脸型、发型、上衣和米黄色裤子。

### 新增 / Added

- 精确慢速眼睛、嘴巴开合与嘴形组合采样，结构验证由 370 增至 555 个姿态；输出 `eyes-slow.gif` 和 `mouth-slow.gif`。
- 视觉审查增加慢速眼口序列，头部取景包含头发，避免裁图把发梢误报为模型缺损。身体序列使用共同取景框，避免逐帧居中抵消真实位移。
- 眨眼连续性、原脸无关像素与 alpha 保留、上下唇分区不重叠测试。

### 兼容性 / Compatibility

连续表情模式默认关闭，须在新构建中显式启用；它会重建眼口底图与关键形，适合有独立眼白和唇部素材的卡通人物。既有工程仍走原绑定路径。旧模型需重新制作素材和导出，不能仅替换 motion3 文件得到新眼口效果。全局眨眼曲线修复对新导出的待机动作生效。

The optional continuous-expression mode rebuilds facial bases and keyforms, preserves neutral lips, and clips independent teeth/tongue to the cavity. Existing projects keep the legacy binding path unless the mode is explicitly enabled. New verification renders slow expression sweeps, while the idle blink fix removes repeated pulses.

### 验证 / Validation

51 项单元测试通过。本次真实卡通人物重新导出后通过官方 Core 和 555 个动作采样（零翻转、零退化）；慢速眼口和固定取景身体视觉审查通过，身体动作幅度已增强。桌面 1120×840、手机 390×760 WebGL 均加载成功，眼口参数控制生效，无脚本错误。关闭新模式重导出的旧工程 MOC 与原版 SHA-256 完全一致。没有修改安忆线上服务、用户数据或生产 worker。

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
