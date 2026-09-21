#!/usr/bin/env python3
"""Serve a Live2D companion on localhost; provider credentials never enter the browser."""
import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from live2d_pipeline import client, env, load_env
from adapters.local_chat import tencent_speech

STATIC = Path(__file__).parent / 'web'
RUNTIME_FILES = {'live2dcubismcore.min.js', 'pixi.min.js', 'cubism4.min.js'}
MAX_BODY = 12_000
MAX_MESSAGE = 1200


class RequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def safe_asset(root, relative):
    if not relative or '\\' in relative or any(part in {'', '.', '..'} for part in relative.split('/')):
        raise RequestError(404, 'file_not_found')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise RequestError(404, 'file_not_found')
    return path


def model_assets(model):
    document = json.loads(model.read_text())
    refs = document.get('FileReferences', {})
    names = {model.name, refs['Moc'], *refs['Textures']}
    for key in ('Physics', 'Pose', 'DisplayInfo'):
        if refs.get(key): names.add(refs[key])
    for item in refs.get('Expressions', []): names.add(item['File'])
    for entries in refs.get('Motions', {}).values():
        for item in entries:
            names.add(item['File'])
            if item.get('Sound'): raise ValueError('External motion audio is unsupported')
    for name in names: safe_asset(model.parent, name)
    return names


class Conversation:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / 'conversation.json'
        self.lock = threading.Lock()
        self.turn_lock = threading.Lock()
        self.generation = 0
        self.name = '小忆'
        self.messages = []
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            self.name = saved.get('name', self.name)
            self.messages = saved.get('messages', [])[-80:]

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'name': self.name, 'messages': self.messages}, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            return {'name': self.name, 'messages': list(self.messages), 'generation': self.generation}

    def clear(self):
        with self.lock:
            self.generation += 1
            self.messages = []
            self.save()

    def begin(self, text, request_id):
        with self.lock:
            previous = next((m for m in self.messages if m.get('requestId') == request_id and m['role'] == 'assistant'), None)
            if any(m.get('requestId') == request_id and m['role'] == 'user' and m['content'] != text for m in self.messages):
                raise RequestError(409, 'request_conflict')
            if previous: return None, previous, None
            if not any(m.get('requestId') == request_id and m['role'] == 'user' for m in self.messages):
                self.messages.append({'id': secrets.token_hex(12), 'role': 'user', 'content': text,
                                      'requestId': request_id, 'createdAt': time.time()})
                self.messages = self.messages[-80:]
                self.save()
            context = [{'role': m['role'], 'content': m['content']} for m in self.messages[-24:]]
            prompt = (
                f'你是名叫{self.name}的卡通AI伙伴，用自然、温和、轻松的中文与用户聊天。'
                '你的外观是蓬松黑发、格纹衣领、浅色上衣和米黄色裤子的卡通青年。'
                '你是AI形象，不是参考照片中的真人；不要编造真人经历、身份、记忆，或声称看见摄像头。'
                '记住本次提供的聊天上下文。每次通常回复一到三句、最多120个中文字，紧贴用户的话，适当提问。'
                '不要用Markdown、角色名标签、括号动作或舞台说明，回答会被朗读。'
                '页面确实支持眨眼、点头、轻摆手臂和视线跟随；用户可以点相应按钮。'
                '不要承诺页面不支持的能力，不要声称已经修改文件或操作外部服务。'
            )
            return self.generation, None, [{'role': 'system', 'content': prompt}, *context]

    def finish(self, generation, request_id, text):
        with self.lock:
            if generation != self.generation: return None
            result = {'id': secrets.token_hex(12), 'role': 'assistant', 'content': text,
                      'requestId': request_id, 'createdAt': time.time()}
            self.messages.append(result)
            self.messages = self.messages[-80:]
            self.save()
            return result


class Speech:
    """Use the installed macOS Chinese voice; Web Audio drives lips from decoded PCM."""
    extension = '.wav'
    def __init__(self, directory, voice='Tingting'):
        self.directory = directory / 'speech'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.voice = voice
        self.available = bool(shutil.which('say') and shutil.which('afconvert'))
        self.lock = threading.Lock()

    def synthesize(self, text):
        if not self.available: raise RequestError(503, 'speech_unavailable')
        text = re.sub(r'\[\[.*?\]\]', '', text, flags=re.S).strip()
        if not text or len(text) > 500: raise RequestError(400, 'invalid_speech_text')
        key = hashlib.sha256((self.voice + '\0' + text).encode()).hexdigest()
        target = self.directory / (key + '.wav')
        with self.lock:
            if target.is_file(): return key
            source = self.directory / (key + '.txt')
            intermediate = self.directory / (key + '.aiff')
            temporary = self.directory / (key + '.tmp.wav')
            try:
                source.write_text(text, encoding='utf-8'); source.chmod(0o600)
                subprocess.run(['say', '-v', self.voice, '-r', '185', '-f', str(source), '-o', str(intermediate)],
                               check=True, timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@24000', '-c', '1', str(intermediate), str(temporary)],
                               check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                temporary.chmod(0o600); temporary.replace(target)
                # A bounded local cache. Conversation clearing removes it too.
                for old in sorted(self.directory.glob('*.wav'), key=lambda p: p.stat().st_mtime)[:-80]: old.unlink()
            except (subprocess.SubprocessError, OSError) as error:
                raise RequestError(503, 'speech_failed') from error
            finally:
                for path in (source, intermediate, temporary): path.unlink(missing_ok=True)
        return key

    def clear(self):
        with self.lock:
            for path in self.directory.glob('*'):
                if path.is_file(): path.unlink()


class Companion:
    def __init__(self, model, runtime, data, chat_client, chat_model, voice='Tingting'):
        self.model, self.runtime = model.resolve(), runtime.resolve()
        self.assets = model_assets(self.model)
        for name in RUNTIME_FILES: safe_asset(self.runtime, name)
        self.conversation = Conversation(data)
        self.speech = tencent_speech.TencentSpeech(data, os.getenv('LOCAL_TTS_VOICE', '603006')) if os.getenv('LOCAL_TTS_PROVIDER') == 'tencent' else Speech(data, voice)
        self.asr_lock = threading.Lock()
        self.client, self.chat_model = chat_client, chat_model
        self.session = secrets.token_urlsafe(32)


def handler_for(companion):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args): pass

        @property
        def origin(self): return f'http://127.0.0.1:{self.server.server_port}'

        def authorize(self, mutation=False):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                raise RequestError(403, 'invalid_host')
            origin = self.headers.get('Origin')
            if origin and origin != self.origin: raise RequestError(403, 'invalid_origin')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site': raise RequestError(403, 'invalid_origin')
            cookie = SimpleCookie()
            try: cookie.load(self.headers.get('Cookie', ''))
            except Exception: raise RequestError(401, 'session_required')
            supplied = cookie.get('live2d_session')
            if not supplied or not secrets.compare_digest(supplied.value, companion.session):
                raise RequestError(401, 'session_required')
            if mutation and self.headers.get('X-Live2d-Client') != 'local':
                raise RequestError(403, 'client_header_required')

        def send_headers(self, status, mime, size=None):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if size is not None: self.send_header('Content-Length', str(size))

        def json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_headers(status, 'application/json; charset=utf-8', len(body)); self.end_headers(); self.wfile.write(body)

        def body(self, limit=MAX_BODY):
            if self.headers.get('Transfer-Encoding'): raise RequestError(400, 'invalid_body')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise RequestError(415, 'json_required')
            try: length = int(self.headers.get('Content-Length', '0'))
            except ValueError: raise RequestError(400, 'invalid_body')
            if not 0 < length <= limit: raise RequestError(413, 'body_too_large')
            try: data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError): raise RequestError(400, 'invalid_json')
            if not isinstance(data, dict): raise RequestError(400, 'invalid_json')
            return data

        def do_GET(self):
            try:
                path = unquote(urlsplit(self.path).path)
                if path == '/':
                    if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                        raise RequestError(403, 'invalid_host')
                    body = (STATIC / 'index.html').read_bytes()
                    self.send_headers(200, 'text/html; charset=utf-8', len(body))
                    self.send_header('Set-Cookie', f'live2d_session={companion.session}; HttpOnly; SameSite=Strict; Path=/')
                    self.end_headers(); self.wfile.write(body); return
                self.authorize()
                if path == '/api/state':
                    return self.json(200, {**companion.conversation.snapshot(), 'speechAvailable': companion.speech.available,
                                           'modelPath': '/model/' + companion.model.name, 'voice': companion.speech.voice,
                                           'asrAvailable': tencent_speech.configured(),
                                           'speechProvider': 'tencent' if isinstance(companion.speech,tencent_speech.TencentSpeech) else 'system'})
                if path.startswith('/model/') and path[7:] in companion.assets:
                    file = safe_asset(companion.model.parent, path[7:])
                elif path.startswith('/runtime/') and path[9:] in RUNTIME_FILES:
                    file = safe_asset(companion.runtime, path[9:])
                elif path in {'/app.js', '/app.css'}:
                    file = STATIC / path[1:]
                elif re.fullmatch(r'/audio/[a-f0-9]{64}\.(?:wav|mp3)', path):
                    file = safe_asset(companion.speech.directory, path.rsplit('/', 1)[1])
                else: raise RequestError(404, 'file_not_found')
                body = file.read_bytes()
                self.send_headers(200, mimetypes.guess_type(file.name)[0] or 'application/octet-stream', len(body))
                self.end_headers(); self.wfile.write(body)
            except RequestError as error: self.json(error.status, {'error': error.code})
            except (BrokenPipeError, ConnectionResetError): pass

        def do_POST(self):
            try:
                self.authorize(mutation=True)
                path = urlsplit(self.path).path
                body = self.body(3*1024*1024+1024 if path == '/api/transcribe' else MAX_BODY)
                if path == '/api/transcribe':
                    if not companion.asr_lock.acquire(blocking=False):raise RequestError(409,'speech_busy')
                    try:return self.json(200,{'text':tencent_speech.transcribe(body.get('audio'))})
                    finally:companion.asr_lock.release()
                if path == '/api/chat': return self.chat(body)
                if path == '/api/speech':
                    text = body.get('text')
                    if not isinstance(text, str): raise RequestError(400, 'invalid_speech_text')
                    key = companion.speech.synthesize(text)
                    return self.json(200, {'audio': '/audio/' + key + companion.speech.extension})
                if path == '/api/clear':
                    companion.conversation.clear(); companion.speech.clear()
                    return self.json(200, {'ok': True})
                if path == '/api/profile':
                    name = body.get('name')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 20 or any(ord(c)<32 for c in name):
                        raise RequestError(400, 'invalid_name')
                    with companion.conversation.lock:
                        companion.conversation.name = name.strip(); companion.conversation.save()
                    return self.json(200, {'ok': True, 'name': name.strip()})
                raise RequestError(404, 'not_found')
            except RequestError as error: self.json(error.status, {'error': error.code})
            except tencent_speech.SpeechError as error:
                code = str(error)
                status = 413 if code=='audio_too_large' else 400 if code.startswith('invalid_') else 503
                self.json(status, {'error': code})
            except (BrokenPipeError, ConnectionResetError): pass

        def chat(self, body):
            text = body.get('text'); request_id = body.get('requestId')
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE:
                raise RequestError(400, 'invalid_message')
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', request_id):
                raise RequestError(400, 'invalid_request_id')
            if not companion.conversation.turn_lock.acquire(blocking=False): raise RequestError(409, 'chat_busy')
            stream = None
            try:
                generation, previous, messages = companion.conversation.begin(text.strip(), request_id)
                self.send_headers(200, 'application/x-ndjson; charset=utf-8')
                self.send_header('Connection', 'close'); self.end_headers(); self.close_connection = True
                def emit(event):
                    self.wfile.write((json.dumps(event, ensure_ascii=False) + '\n').encode()); self.wfile.flush()
                if previous: emit({'type': 'done', 'message': previous}); return
                emit({'type': 'start'})
                pieces = []
                finish = None
                try:
                    stream = companion.client.chat.completions.create(model=companion.chat_model,
                        messages=messages, stream=True, max_completion_tokens=550, reasoning_effort='low')
                    with stream:
                        for chunk in stream:
                            if generation != companion.conversation.generation: emit({'type':'cancelled'}); return
                            if not chunk.choices: continue
                            choice = chunk.choices[0]
                            if choice.delta.content:
                                pieces.append(choice.delta.content); emit({'type':'delta', 'text':choice.delta.content})
                            if choice.finish_reason: finish = choice.finish_reason
                    reply = ''.join(pieces).strip()
                    if not reply or finish != 'stop': raise ValueError('incomplete_reply')
                    result = companion.conversation.finish(generation, request_id, reply)
                    emit({'type':'done', 'message':result} if result else {'type':'cancelled'})
                except (BrokenPipeError, ConnectionResetError): raise
                except Exception as error:
                    print('Chat failed (' + type(error).__name__ + ')', flush=True)
                    emit({'type':'error', 'error':'chat_unavailable'})
            finally:
                if stream is not None: stream.close()
                companion.conversation.turn_lock.release()
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True, help='Path to an exported .model3.json')
    parser.add_argument('--runtime', type=Path, required=True, help='Directory containing installed Cubism/Pixi JS runtimes')
    parser.add_argument('--data', type=Path, required=True, help='Private local conversation/audio directory')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--speech-env-file', type=Path, help='Optional private Tencent speech credentials')
    parser.add_argument('--chat-model')
    parser.add_argument('--voice', default='Tingting')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    os.umask(0o077)
    if args.env_file: load_env(args.env_file.expanduser())
    if args.speech_env_file: load_env(args.speech_env_file.expanduser())
    model = args.chat_model or env('CHAT_MODEL', 'AI_MODEL', 'PLANNER_MODEL', 'ASTRA_MODEL')
    if not model: parser.error('Set CHAT_MODEL or pass --chat-model')
    companion = Companion(args.model, args.runtime, args.data, client().with_options(timeout=75,max_retries=1), model, args.voice)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(companion))
    url = f'http://127.0.0.1:{server.server_port}/'
    (args.data/'server.json').write_text(json.dumps({'url':url,'pid':os.getpid()}))
    print('LOCAL_CHAT_URL ' + url, flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
