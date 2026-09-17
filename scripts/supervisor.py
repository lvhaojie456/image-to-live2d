"""Gate reviews, failure diagnosis and bounded recovery actions for one build.

Three layers, cheapest first:
1. Rules (no model): deterministic recoveries decided by the build itself.
2. Model (this module): reviews thumbnails at stage gates and, on failure, picks
   one action from a fixed menu. Answers are JSON and validated against whitelists.
3. User: paid actions are never executed here; they become a suggestion the App
   shows and the user confirms.

Modes (LIVE2D_SUPERVISOR_MODE): off = no model calls; shadow (default) = call the
model, record its decisions, but only execute rule actions; act = execute the
model's free actions within budget.
"""
import base64
import io
import json
import os
import time
from pathlib import Path

from PIL import Image

MODES = ('off', 'shadow', 'act')
DIAGNOSIS_CODES = ('provider_unavailable', 'background_leak', 'face_not_located', 'expression_failed',
                   'rig_unstable', 'budget_exhausted')
SUGGESTIONS = ('retry', 'regenerate_image', 'new_input')
ACTIONS = ('retry_stage', 'replan_with_hint', 'clip_background', 'tune_motion', 'redo_expressions',
           'regenerate_image', 'give_up')
PAID_ACTIONS = {'regenerate_image'}
BUDGET = {'regenerate_image': 1, 'replan_with_hint': 2, 'tune_motion': 2, 'redo_expressions': 1,
          'retry_stage': 3, 'model_calls': 8}
DEFAULT_SUGGESTION = {'provider_unavailable': 'retry', 'background_leak': 'regenerate_image',
                      'face_not_located': 'new_input', 'expression_failed': 'retry',
                      'rig_unstable': 'regenerate_image', 'budget_exhausted': 'new_input'}
MESSAGES = {'provider_unavailable': '生成服务暂时不可用，请稍后重试。',
            'background_leak': '背景被并入了人物图层，自动裁剪后仍未通过检查，建议换一张纯色或透明背景的图片。',
            'face_not_located': '没有找到清晰的正面脸部，请换一张正面、无遮挡的图片。',
            'expression_failed': '表情素材生成不合格，请重试。',
            'rig_unstable': '动作检查未通过，建议换一张四肢完整、背景干净的图片。',
            'budget_exhausted': '自动修复次数已用完，请换一张图片或修改描述后重新提交。'}
THUMBNAIL = 512
SUMMARY_LIMIT = 200


def thumbnail_uri(image, size=THUMBNAIL):
    """PNG data URI of an image downscaled to at most `size` px on the long side."""
    if isinstance(image, (str, Path)):
        with Image.open(image) as opened:
            image = opened.convert('RGB')
    else:
        image = image.convert('RGB')
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'), image.size


def sanitize_text(value, limit=SUMMARY_LIMIT):
    if not isinstance(value, str):
        return ''
    cleaned = ''.join(ch for ch in value if ch >= ' ' and ch != '\x7f').strip()
    return cleaned[:limit].rstrip()


def rectangle(value, width, height):
    """A [x,y,w,h] box fully inside width x height, or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(v == v and abs(v) != float('inf') for v in (x, y, w, h)):
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return [x, y, w, h]


class Supervisor:
    def __init__(self, workspace, state_path=None, mode=None, client_factory=None, model=None):
        self.workspace = Path(workspace)
        self.log_dir = self.workspace / 'supervisor'
        self.mode = (mode or os.environ.get('LIVE2D_SUPERVISOR_MODE') or 'shadow').lower()
        if self.mode not in MODES:
            raise ValueError('LIVE2D_SUPERVISOR_MODE must be one of ' + ', '.join(MODES))
        self.state_path = Path(state_path) if state_path else None
        self.state = {'used': {}, 'history': []}
        if self.state_path and self.state_path.is_file():
            try:
                loaded = json.loads(self.state_path.read_text())
                if isinstance(loaded.get('used'), dict) and isinstance(loaded.get('history'), list):
                    self.state = loaded
            except (OSError, ValueError):
                pass
        self.client_factory = client_factory
        self.model = model or os.environ.get('SUPERVISOR_MODEL') or os.environ.get('ASTRA_MODEL', 'gpt-6-astra')
        self._api = None

    # ----- budget -------------------------------------------------------
    def budget_left(self):
        return {k: v - self.state['used'].get(k, 0) for k, v in BUDGET.items()}

    def can(self, action):
        return action in BUDGET and self.budget_left()[action] > 0

    def consume(self, action, note=''):
        if not self.can(action):
            return False
        self.state['used'][action] = self.state['used'].get(action, 0) + 1
        self.state['history'].append({'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'action': action, 'note': note})
        self._save_state()
        return True

    def _save_state(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))

    def _log(self, name, payload):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    # ----- model --------------------------------------------------------
    def _client(self):
        if self._api is None:
            if self.client_factory is not None:
                self._api = self.client_factory()
            else:
                from live2d_pipeline import client
                self._api = client().with_options(timeout=60.0, max_retries=0)
        return self._api

    def ask(self, name, system, text, images, max_tokens=600):
        """One non-streaming JSON request. Returns a dict, or None when the model was not
        called (mode off, budget) or the call/answer was unusable. Never raises."""
        record = {'model': self.model, 'mode': self.mode, 'images': [], 'text': text}
        if self.mode == 'off':
            record['skipped'] = 'mode_off'
            self._log(name + '.json', record)
            return None
        if not self.consume('model_calls', name):
            record['skipped'] = 'model_call_budget'
            self._log(name + '.json', record)
            return None
        content = [{'type': 'text', 'text': text}]
        for label, image in images:
            uri, size = thumbnail_uri(image)
            record['images'].append({'label': label, 'size': list(size)})
            content.append({'type': 'text', 'text': f'[{label}]'})
            content.append({'type': 'image_url', 'image_url': {'url': uri}})
        started = time.time()
        try:
            response = self._client().chat.completions.create(
                model=self.model, reasoning_effort=os.environ.get('SUPERVISOR_REASONING_EFFORT', 'low'),
                max_completion_tokens=max_tokens, temperature=0, response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': content}])
            raw = response.choices[0].message.content or ''
            record['seconds'] = round(time.time() - started, 1)
            record['raw'] = raw[:4000]
            answer = json.loads(raw)
            if not isinstance(answer, dict):
                raise ValueError('answer is not an object')
            record['answer'] = answer
            self._log(name + '.json', record)
            return answer
        except Exception as error:  # provider, transport, JSON: all degrade to "no answer"
            record['seconds'] = round(time.time() - started, 1)
            record['error'] = type(error).__name__
            self._log(name + '.json', record)
            return None

    # ----- gates ----------------------------------------------------------
    def review_regions(self, image, regions):
        """Planning gate: are the face / eye / mouth boxes on the right features?
        Returns corrected regions in image pixels when the model proposes valid ones."""
        from PIL import ImageDraw
        with Image.open(image) as opened:
            drawn = opened.convert('RGB')
        width, height = drawn.size
        draw = ImageDraw.Draw(drawn)
        for key, colour in (('face', 'red'), ('left_eye', 'blue'), ('right_eye', 'blue'), ('mouth', 'green')):
            x, y, w, h = regions[key]
            draw.rectangle((x, y, x + w, y + h), outline=colour, width=max(2, width // 250))
        scale = max(width, height) / min(THUMBNAIL, max(width, height))
        system = ('你是 Live2D 制作流水线的质检员。只根据图片回答，输出严格 JSON，不要 Markdown。'
                  '结构：{"ok":true|false,"issues":["string"],"regions":{"face":[x,y,w,h]|null,'
                  '"left_eye":[x,y,w,h]|null,"right_eye":[x,y,w,h]|null,"mouth":[x,y,w,h]|null},"explanation":"一句中文"}。'
                  'left_eye 指画面右侧（角色自身左眼）。regions 只在需要修正时给出，坐标用缩略图像素，原点左上；不需要修正时填 null。')
        answer = self.ask('gate-planning', system, '红框应为脸、蓝框为双眼、绿框为嘴。判断框是否落在正确部位，需要修正时给出新框。',
                          [('input', drawn)])
        if not answer:
            return None
        corrected = {}
        for key in ('face', 'left_eye', 'right_eye', 'mouth'):
            box = rectangle((answer.get('regions') or {}).get(key), THUMBNAIL, THUMBNAIL)
            if box:
                candidate = rectangle([v * scale for v in box], width, height)
                if candidate:
                    corrected[key] = [round(v) for v in candidate]
        return {'ok': bool(answer.get('ok')), 'issues': [sanitize_text(i) for i in answer.get('issues', []) if isinstance(i, str)][:6],
                'regions': corrected, 'explanation': sanitize_text(answer.get('explanation'))}

    def review_layers(self, sheet, check_report):
        """Decomposition gate on the layer contact sheet plus the rule-layer report."""
        summary = ', '.join(f"{l['name']} {l['canvas_share']:.0%}" for l in check_report['layers'][:24])
        system = ('你是 Live2D 拆层质检员。图为 See-through 拆出的图层缩略表，每格的浅灰底是缩略图底板，不是图层内容。'
                  '只根据图片回答，输出严格 JSON，不要 Markdown。'
                  '结构：{"ok":true|false,"background_leak":true|false,"misassigned":["string"],"missing":["string"],"explanation":"一句中文"}。')
        clipped = [c['name'] for c in check_report.get('clipped', [])]
        text = ('各图层外接框占画布比例：' + summary + '。背景是否仍被并入某个图层？有无部件明显错分或缺失？'
                + ('规则层已裁掉 ' + ', '.join(clipped) + ' 并入的背景，图中是裁剪后的结果。' if clipped else ''))
        answer = self.ask('gate-decomposition', system, text, [('layers', sheet)])
        if not answer:
            return None
        return {'ok': bool(answer.get('ok')), 'background_leak': bool(answer.get('background_leak')),
                'misassigned': [sanitize_text(i, 60) for i in answer.get('misassigned', []) if isinstance(i, str)][:8],
                'missing': [sanitize_text(i, 60) for i in answer.get('missing', []) if isinstance(i, str)][:8],
                'explanation': sanitize_text(answer.get('explanation'))}

    def review_expressions(self, original, eyes, mouth):
        """Expressions gate: closed eyes and open mouth edits against the original crop."""
        system = ('你是 Live2D 表情素材质检员。三张图依次是原始脸部、闭眼编辑、张嘴编辑。只根据图片回答，输出严格 JSON，不要 Markdown。'
                  '结构：{"ok":true|false,"eyes_closed":true|false,"mouth_open":true|false,"identity_preserved":true|false,'
                  '"issues":["string"],"explanation":"一句中文"}。')
        answer = self.ask('gate-expressions', system, '闭眼图双眼是否自然闭合？张嘴图是否自然张开并露出口腔？两张图是否保持了原人物的身份、年龄与画风？',
                          [('original', original), ('eyes_closed', eyes), ('mouth_open', mouth)])
        if not answer:
            return None
        return {'ok': bool(answer.get('ok')), 'eyes_closed': bool(answer.get('eyes_closed')),
                'mouth_open': bool(answer.get('mouth_open')), 'identity_preserved': bool(answer.get('identity_preserved')),
                'issues': [sanitize_text(i) for i in answer.get('issues', []) if isinstance(i, str)][:6],
                'explanation': sanitize_text(answer.get('explanation'))}

    def locate_mouth(self, crop):
        """Fallback when no darkness threshold isolates the mouth: ask for the box on the edit."""
        width, height = crop.size
        scale = max(width, height) / min(THUMBNAIL, max(width, height))
        system = ('你是图像标注员。只根据图片回答，输出严格 JSON，不要 Markdown。'
                  '结构：{"mouth":[x,y,w,h]|null,"explanation":"一句中文"}。坐标用缩略图像素，原点左上，框要紧贴嘴唇外缘；找不到嘴填 null。')
        answer = self.ask('mouth-locate', system, '标出这张脸上嘴巴（双唇外缘）的矩形。', [('mouth_edit', crop)])
        if not answer:
            return None
        box = rectangle(answer.get('mouth'), THUMBNAIL, THUMBNAIL)
        if not box:
            return None
        scaled = rectangle([v * scale for v in box], width, height)
        return [round(v) for v in scaled] if scaled else None

    def visual_review(self, sheets):
        """Final non-blocking quality score on the expression and pose sheets."""
        system = ('你是 Live2D 初版模型的视觉质检员。只根据图片回答，输出严格 JSON，不要 Markdown。'
                  '结构：{"score":0到1的小数,"issues":["string"],"explanation":"一句中文"}。'
                  '评分依据：闭眼自然、张嘴自然、无背景残留、身体姿态无撕裂或错位、部件顺序正确。')
        answer = self.ask('visual-review', system, '给这个自动生成的初版打分并列出问题。', sheets)
        if not answer:
            return None
        try:
            score = float(answer.get('score'))
        except (TypeError, ValueError):
            return None
        if not 0 <= score <= 1:
            return None
        return {'score': round(score, 2), 'issues': [sanitize_text(i) for i in answer.get('issues', []) if isinstance(i, str)][:8],
                'explanation': sanitize_text(answer.get('explanation'))}

    # ----- failure handling -----------------------------------------------
    def failure_packet(self, stage, error, metrics=None):
        """Only stage, error class, a short message and numeric metrics; no paths or provider text."""
        message = sanitize_text(str(error), 160)
        for token in (str(self.workspace), str(Path.home())):
            message = message.replace(token, '…')
        return {'stage': stage, 'error_class': type(error).__name__, 'error_message': message,
                'metrics': metrics or {}, 'budget_left': self.budget_left(),
                'history': self.state['history'][-8:]}

    def diagnose(self, packet, images=(), allowed=ACTIONS):
        """Ask the model for a diagnosis and an action from the menu. Returns a validated
        decision or None. The caller decides whether the action may execute."""
        system = ('你是 Live2D 生成流水线的值班工程师。根据失败包和图片判断原因，并从菜单里选一个下一步动作。'
                  '输出严格 JSON，不要 Markdown。结构：{"diagnosis_code":"' + '|'.join(DIAGNOSIS_CODES) + '",'
                  '"action":"' + '|'.join(allowed) + '","explanation":"一句中文，面向用户，不含技术术语","confidence":0到1}。'
                  '动作含义：retry_stage 重跑当前阶段；replan_with_hint 重新分析形象；clip_background 裁掉并入人物的背景；'
                  'tune_motion 减小动作幅度后重新绑定；redo_expressions 重做表情；regenerate_image 重新生成人物图（需用户确认）；give_up 放弃并告知用户。'
                  '预算不足的动作不要选。')
        answer = self.ask('diagnosis-' + packet['stage'], system, json.dumps(packet, ensure_ascii=False), list(images))
        if not answer:
            return None
        code = answer.get('diagnosis_code'); action = answer.get('action')
        if code not in DIAGNOSIS_CODES or action not in allowed:
            self._log('diagnosis-' + packet['stage'] + '-rejected.json', {'answer': answer})
            return None
        try:
            confidence = float(answer.get('confidence', 0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {'diagnosis_code': code, 'action': action, 'explanation': sanitize_text(answer.get('explanation')),
                'confidence': max(0.0, min(1.0, confidence))}

    def write_diagnosis(self, code, suggestion=None, summary=None, action=None, packet=None):
        """Persist what the worker will report to the server. Codes and suggestions are whitelisted."""
        if code not in DIAGNOSIS_CODES:
            code = 'provider_unavailable'
        suggestion = suggestion if suggestion in SUGGESTIONS else DEFAULT_SUGGESTION[code]
        summary = sanitize_text(summary) or MESSAGES[code]
        payload = {'diagnosisCode': code, 'suggestion': suggestion, 'summary': summary, 'action': action,
                   'packet': packet, 'budget_left': self.budget_left()}
        self._log('diagnosis.json', payload)
        return payload
