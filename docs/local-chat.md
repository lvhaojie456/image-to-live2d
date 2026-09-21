# 本机互动伙伴 / Local interactive companion

`adapters/local_chat/server.py` 把已有 Live2D 运行模型接成一个可聊天的本机页面。它使用项目已配置的 OpenAI 兼容接口生成文字回复，浏览器负责动作和音频口型，不需要重新生成角色，也不写入安忆线上账号。

The local adapter turns an existing exported model into a chat companion. It uses the configured OpenAI-compatible endpoint for replies and drives animation in the browser. It does not regenerate the character or write to Anyi production accounts.

## 能做什么 / Features

- 文字聊天，回复逐步显示；保存最近 80 条本机消息，最多 24 条作为下一轮上下文。
- 中文语音朗读、重播、静音和中断，可选择 macOS 系统语音或腾讯云男声；从实际播放 PCM 的 RMS 驱动嘴部，暂停或结束后闭嘴。
- 点按录音、停止识别、取消录音；识别文字先填入输入框，确认后发送。首次需单独同意录音发往腾讯云。
- 角色占据主要画面的互动场景，聊天侧栏可收起；近景/全身切换，桌面和手机布局。
- 头、脸、左右肩、左右手六个区域：点触、连续摸头、拉手跟随与松手恢复；连续戳戳会改变反应。
- 可点击或拖放茶和礼物；眨眼、点头、招呼、活动身体，配合表情、语音和小特效。
- 待机变化和返回欢迎；触摸反馈不打断聊天语音，支持减少动态效果偏好和键盘入口。
- 最近 60 秒内的交互事件可带入下一轮聊天；改名和清空本机历史与语音缓存。

Text replies stream into the conversation. Installed macOS speech or Tencent TTS produces reply audio; Web Audio measures its actual amplitude to drive the mouth. Optional microphone input uses Tencent sentence recognition, places the transcript in the composer for review, and never sends it as a chat message automatically. Gaze follows the pointer. Continuous head rubbing, region-specific taps, hand dragging/recovery and draggable tea/gift props drive native model parameters. A collapsible chat panel supports replay, stop, mute, rename and local history deletion.

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

## 角色互动 / Character interactions

| 操作 | 反馈 |
| --- | --- |
| 点头发 / 按住左右揉 | 倾头、闭眼、放松；按住时持续跟随 |
| 戳脸 / 连续戳几次 | 转头躲闪、眯眼；第三次切换回应 |
| 拍肩 | 转头、身体倾斜、回应 |
| 拉动左右手后松开 | 对应手臂与身体跟随，缓动恢复 |
| 点道具 / 拖到角色身上 | 茶或礼物出现在手边，表情与回应改变；拖到空白处不触发 |
| 右侧动作按钮 | 摸头、眨眼、招呼、左右活动；可重复触发并切换台词 |
| “触摸位置” | 查看区域；区域随实际网格移动 |
| 聚焦角色后按 1 / 2 / 3 / 4 | 摸头 / 戳脸 / 碰手 / 活动，Esc 取消持续拖动 |

`web/interactions.mjs` 是独立、可测试的动作调度器，包含动作关键帧、缓动、打断混合、点按/持续拖动、连续点击反应、待机调度和参数限幅。`web/app.js` 将它接到 Cubism 的 `beforeModelUpdate`，口型仍只跟随实际播放音频。动作会即时反馈，不等待模型服务。触摸语音不会插入正在播放的聊天语音。

服务优先读取模型旁的 `interaction.json`；否则尝试交付包 `cubism-ready/authoring-manifest.json` 或构建目录 `materials/motion-manifest.json`。按图层 bbox 推导近景/全身范围和六个触摸区域，在浏览器中绑定匹配网格，随当前顶点位置更新。没有这些元数据时只能使用居中人物的默认区域，其他排版应提供配置：

```json
{
  "bounds": [0.3, 0.01, 0.7, 0.98],
  "nearBounds": [0.3, 0.01, 0.7, 0.65],
  "zones": [
    {"id": "head", "label": "头发 · 按住揉一揉", "rect": [0.42, 0.01, 0.58, 0.1]},
    {"id": "cheek", "label": "脸颊 · 轻戳", "rect": [0.44, 0.1, 0.56, 0.17]}
  ]
}
```

矩形是 `[left, top, right, bottom]`，除以原始画布宽高后的比例。区域 ID 支持 `head`、`cheek`、`shoulder`、`hand-l`、`hand-r`。可附 `mesh`（原生 ArtMesh ID）和 `meshRect`（该网格原始 bbox）启用随网格移动；旧版 Handwear 命名会通过初始 bbox 匹配。`nearBounds` 应包含手部，避免近景下无法拉手。

当前动作复用模型已有的眼口、头部、身体和手臂摆动参数。道具是手边的场景叠加物，还没有弯肘持杯、走路、换姿势等专用关键形；这些需要补画、关节绑定并在 Cubism 精修。此适配器不改变模型的 `.cmo3`，动作编排保存在项目代码中。

触摸事件仅传固定 ID，后端转换成白名单中文描述，作为本轮相关上下文；不接受客户端注入任意系统提示词。语音识别后仍需点击发送。互动计数是本次页面会话的计数，刷新重置，不用于推断人物的真实情绪。

This scene follows the interaction principles described in the [Live2D interview with the Azur Lane team](https://www.live2d.com/business/interview/azurlane/): gaze, intentional motion timing and animations within the rig's actual range. It contains no game artwork or extracted game assets. Actions are implemented using the current model's parameters; articulated arms, new poses and actual prop holding require new keyforms.

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

Python 测试覆盖历史幂等、上下文与清空后的过期回复、同源限制、路径隔离、无 shell 的语音调用、音频格式/时长校验、云端语音缓存，以及图层区域推导、交互上下文白名单和重试一致性。Node 测试覆盖全部动作的参数限幅与结束恢复、连续点击、持续摸头、拖动越界/取消、待机避让和减少动态效果。

```bash
python -m unittest discover -s tests -v
node --test tests/test_interactions.mjs
```

真实浏览器验收包括触摸、拉手恢复、空白不触发、道具拖放、六个区域跟随网格、真实聊天/男声口型和手机/桌面布局。聊天测试使用独立数据目录，已有用户会话保留。麦克风链路此前已用模拟输入通过腾讯云识别、对话与男声合成验证；本轮场景更新没有更换录音链路。

Tests cover history idempotency, clearing during generation, origin/path restrictions, speech validation, touch profile geometry, whitelisted interaction context, motion bounds, drag/release behavior, idle suppression and reduced motion. Browser verification uses a separate conversation directory and real configured chat/TTS services.
