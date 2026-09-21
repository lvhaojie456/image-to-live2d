# 本机互动伙伴 / Local interactive companion

`adapters/local_chat/server.py` 把已有 Live2D 运行模型接成一个可聊天的本机页面。它使用项目已配置的 OpenAI 兼容接口生成文字回复，浏览器负责动作和音频口型，不需要重新生成角色，也不写入安忆线上账号。

The local adapter turns an existing exported model into a chat companion. It uses the configured OpenAI-compatible endpoint for replies and drives animation in the browser. It does not regenerate the character or write to Anyi production accounts.

## 能做什么 / Features

- 文字聊天，回复逐步显示；保存最近 80 条本机消息，最多 24 条作为下一轮上下文。
- 中文语音朗读、重播、静音和中断，可选择 macOS 系统语音或腾讯云男声；从实际播放 PCM 的 RMS 驱动嘴部，暂停或结束后闭嘴。
- 点按录音、停止识别、取消录音；识别文字先填入输入框，确认后发送。首次需单独同意录音发往腾讯云。
- 鼠标或触摸拖动的视线跟随；点击形象、眨眼、点头、打招呼按钮触发回应。
- 半身/全身切换、改名字、清空本机历史与语音缓存，支持桌面及手机尺寸。

Text replies stream into the conversation. Installed macOS speech or Tencent TTS produces reply audio; Web Audio measures its actual amplitude to drive the mouth. Optional microphone input uses Tencent sentence recognition, places the transcript in the composer for review, and never sends it as a chat message automatically. Gaze follows the pointer, clicks trigger gestures, and the page supports replay, stop, mute, rename and local history deletion.

## 运行 / Run

在已有 Python 环境中运行，不新增 Python 依赖：

```bash
python adapters/local_chat/server.py \
  --model /path/to/export/model.model3.json \
  --runtime /path/to/installed/js-runtime \
  --data work/companion-chat \
  --env-file .env \
  --chat-model your-chat-model
```

`--model` 指向本次实际导出的 model3 清单；`--runtime` 需要你自行提供 `live2dcubismcore.min.js`、`pixi.min.js`、`cubism4.min.js`，项目不会捆绑这些库。使用你已有的 SDK/应用资源并遵守其许可。

`--env-file` 使用已有 `LLM_API_KEY` / `LLM_API_BASE_URL`，旧变量别名仍适用。模型优先级为 `--chat-model` → `CHAT_MODEL` → `AI_MODEL` → `PLANNER_MODEL` → `ASTRA_MODEL`；建议明确选择对话模型以控制延迟。

命令输出本机 URL，并写到 `--data/server.json`。默认随机端口，可通过 `--port` 固定。只监听 `127.0.0.1`，用输出 URL 打开；不提供公网发布或其它设备访问。

Point `--model` at your exported model3 manifest and supply your installed Cubism/Pixi JavaScript runtimes. The configured API key stays server-side. The server prints a localhost URL and records it in the private data directory; it never binds to an external interface.

## 麦克风与云端男声 / Microphone and cloud voice

本机私有文件（权限 600）可配置 `TENCENT_ASR_SECRET_ID`、`TENCENT_ASR_SECRET_KEY` 和可选 `TENCENT_ASR_REGION=ap-beijing`。只将键名和占位符写入仓库，实际凭据通过 `--speech-env-file /path/to/private-speech.env` 读取。

```bash
LOCAL_TTS_PROVIDER=tencent LOCAL_TTS_VOICE=603006 \
  python adapters/local_chat/server.py \
  --model /path/to/model.model3.json --runtime /path/to/js-runtime \
  --data work/companion-chat --env-file .env \
  --speech-env-file /path/to/private-speech.env --chat-model your-chat-model
```

腾讯语音输出支持白名单 `603006`（沉稳男声）、`602005`（知性女声）、`603004`（温柔女声）；默认用 603006。`LOCAL_TTS_PROVIDER` 未指定时仍用系统语音。麦克风使用浏览器 MediaRecorder，转换为 16 kHz 单声道 PCM WAV，再由本机服务调用腾讯云识别；单次最多 60 秒，取消或结束后立即停止麦克风轨道。原始录音只在内存处理，不落盘。

Provide Tencent credentials in a private file via `--speech-env-file`. Set `LOCAL_TTS_PROVIDER=tencent` for cloud output (default male voice 603006); otherwise macOS system speech remains the output provider. Microphone recording is converted to 16 kHz mono WAV and transcribed by Tencent. Recording stops after at most 60 seconds; the raw audio is not stored, and the transcript needs a separate Send action.

## 语音与隐私 / Speech and privacy

语音输出默认用 macOS 的 `Tingting` 中文音色，可通过 `--voice` 选择已安装的系统音色（`say -v '?'` 查看）。调用 `say` 和 `afconvert` 生成 24 kHz 单声道 WAV，在电脑本地完成；没有克隆照片人物的声音。没有这两个命令的平台可以配置腾讯云语音输出，否则语音回复选项关闭。麦克风入口仅在已配置识别服务且浏览器支持录音时出现；此版本为逐句语音交互，不是实时视频通话。

聊天内容会发送到配置的模型服务。配置云端语音时，录音发送至腾讯云识别，回复文字发送至腾讯云合成。首次使用麦克风需同意说明和浏览器授权。原照片不会作为聊天输入发送；角色描述是普通卡通伙伴，不推断或冒充照片人物的身份和经历。聊天记录存在 `conversation.json`，语音缓存最多 80 个 WAV；清空按钮删除两者。数据目录权限 700，记录与音频文件 600。

The default voice is the installed Chinese system voice `Tingting`, not a clone of the photographed person. Platforms without `say`/`afconvert` can use configured cloud speech. Tencent handles recordings for recognition and reply text for synthesis; first microphone use requires consent and browser permission. This is sentence-based voice interaction, not a real-time video call. Chat text goes to your configured model provider; conversation files and generated speech stay local and can be cleared.

## 接口和验证 / Protocol and validation

- `/api/chat`：同源 POST，UUID 请求标识，NDJSON `start` / `delta` / `done`；不完整回复不上下文，失败可重试。同一请求不会重复写历史，同一人物一次只处理一轮。
- `/api/speech`：生成语音后返回同源 `/audio/<sha256>.wav` 或 `.mp3`。
- `/api/transcribe`：只接受最多 60 秒、16 kHz 单声道 16-bit PCM WAV 的 Base64 请求，返回文字，不自动写入聊天历史。
- `/api/state`、`/api/profile`、`/api/clear`：读取历史、改名和清空。
- Session cookie 为 HttpOnly / SameSite=Strict；校验 Host、Origin 和 POST 客户端头。模型只开放清单中已校验的文件路径，不开放任意项目文件。浏览器拿不到供应商密钥。
- 清空操作增加会话代数，正在生成的旧回复不能重新写回已清空的历史。

本地 55 项测试通过，其中新增测试覆盖历史幂等、上下文与清空后的过期回复、同源限制、路径隔离、无 shell 的语音调用、音频格式/时长校验与云端语音缓存。真实两轮对话、语音非零口型、停止闭嘴、眨眼、视线跟随、刷新恢复和清空已通过桌面及手机布局验证；浏览器用合成测试语音作为模拟麦克风输入，真实经过腾讯云识别、对话模型、腾讯云男声合成和音频口型。验收历史已清空。

The tests cover idempotent history, clearing during generation, origin/path restrictions and speech subprocess arguments. Browser verification covers real multi-turn replies, audio-driven lips, stopping playback, gestures, gaze, history restoration and mobile layout.
