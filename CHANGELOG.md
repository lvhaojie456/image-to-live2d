# Changelog / 更新记录

## 2026-09-21 — 本机互动伙伴 / Local interactive companion

### 新增 / Added

- `adapters/local_chat`：已有 Live2D 运行模型的本机聊天界面，文字回复流式显示，本地保存上下文，支持改名和清空。
- 系统或腾讯云中文男声输出、回复重播和停止；浏览器从真实音频波形计算嘴部开合，静音、暂停或结束时闭嘴。
- 麦克风录音、结束转写和取消，首次使用提供单独同意说明；录音最多 60 秒，经腾讯云转写后先进入输入框，用户确认再发。
- 视线跟随、点击形象、眨眼、点头、打招呼和全身/近景切换；桌面与手机布局。
- 同源会话、私有文件路径限制、请求幂等、清空后阻止旧回复写回，以及 macOS 无 shell 语音调用测试。

### 使用与边界 / Scope

- 只监听 localhost，使用现有 OpenAI 兼容接口及 `CHAT_MODEL`，供应商密钥不进入浏览器；用户照片与产物不入 Git。
- 语音可用安装的 macOS `say` / `afconvert` 或腾讯云白名单音色，无声音克隆。麦克风录音转为 16 kHz 单声道 WAV 后发至腾讯云，原始录音不落盘。支持逐句语音交互，未实现实时视频通话。
- 未改动安忆后端、生产 worker、账号或部署配置。

### 验证 / Validation

- 55 项单元测试通过。真实两轮对话记住称呼和上文话题；音频实际驱动嘴部、停止闭嘴、点击眨眼、视线跟随、刷新恢复与清空均通过浏览器验证。
- 浏览器模拟麦克风播放合成测试句，真实走腾讯云识别 → 确认发送 → 对话回复 → 腾讯云男声 → 音频口型；录音取消、停止朗读与清空通过。
- 1200×840 与 390×844 页面加载，无脚本错误和移动端横向溢出；验收对话已清空。

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
