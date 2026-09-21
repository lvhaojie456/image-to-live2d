"""Tencent short-sentence recognition and speech output for the local companion."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
import uuid
import wave


class SpeechError(Exception): pass


def credentials():
    return os.getenv('TENCENT_ASR_SECRET_ID', '').strip(), os.getenv('TENCENT_ASR_SECRET_KEY', '').strip()


def configured(): return all(credentials())


def authorization(secret_id, secret_key, timestamp, payload, service, action):
    host = service + '.tencentcloudapi.com'
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')
    digest = lambda data: hashlib.sha256(data).hexdigest()
    canonical_headers = f'content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n'
    signed = 'content-type;host;x-tc-action'
    request = '\n'.join(['POST', '/', '', canonical_headers, signed, digest(payload)])
    scope = f'{date}/{service}/tc3_request'
    to_sign = '\n'.join(['TC3-HMAC-SHA256', str(timestamp), scope, digest(request.encode())])
    def sign(key, text): return hmac.new(key, text.encode(), hashlib.sha256).digest()
    key = sign(('TC3' + secret_key).encode(), date)
    key = sign(sign(key, service), 'tc3_request')
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f'TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders={signed}, Signature={signature}'


def request(service, action, version, body):
    secret_id, secret_key = credentials()
    if not secret_id or not secret_key: raise SpeechError('speech_not_configured')
    payload = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        timestamp = int(time.time())
        headers = {'Authorization': authorization(secret_id, secret_key, timestamp, payload, service, action),
                   'Content-Type': 'application/json; charset=utf-8', 'X-TC-Action': action,
                   'X-TC-Timestamp': str(timestamp), 'X-TC-Version': version,
                   'X-TC-Region': os.getenv('TENCENT_ASR_REGION', 'ap-beijing')}
        req = urllib.request.Request('https://' + service + '.tencentcloudapi.com', data=payload, headers=headers, method='POST')
        try:
            with opener.open(req, timeout=45) as response:
                result = json.load(response)['Response']
        except Exception as error:
            raise SpeechError('speech_connection_failed') from error
        error = result.get('Error')
        if not error: return result
        code = str(error.get('Code', ''))
        if attempt < 2 and code.startswith(('LimitExceeded', 'RequestLimitExceeded', 'InternalError')):
            time.sleep((.3, .9)[attempt]); continue
        raise SpeechError('speech_service_busy')
    raise SpeechError('speech_service_busy')


def validate_wav(encoded):
    if not isinstance(encoded, str) or len(encoded) > 3 * 1024 * 1024: raise SpeechError('audio_too_large')
    try:
        data = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE':
                raise SpeechError('invalid_audio')
            duration = audio.getnframes() / audio.getframerate()
            if not .25 <= duration <= 60: raise SpeechError('invalid_audio_duration')
            if len(audio.readframes(audio.getnframes())) != audio.getnframes()*2: raise SpeechError('invalid_audio')
    except SpeechError: raise
    except Exception as error: raise SpeechError('invalid_audio') from error
    return data


def transcribe(encoded):
    data = validate_wav(encoded)
    result = request('asr', 'SentenceRecognition', '2019-06-14', {
        'EngSerViceType':'16k_zh','SourceType':1,'VoiceFormat':'wav','ProjectId':0,'SubServiceType':2,
        'UsrAudioKey':str(uuid.uuid4()),'Data':base64.b64encode(data).decode(),'DataLen':len(data),
        'WordInfo':0,'FilterDirty':0,'FilterModal':0,'FilterPunc':0,'ConvertNumMode':1})
    text = result.get('Result')
    if not isinstance(text, str) or not text.strip(): raise SpeechError('no_speech_detected')
    return text.strip()[:1200]


class TencentSpeech:
    extension = '.mp3'
    def __init__(self, directory, voice=603006):
        self.directory = directory / 'speech'; self.directory.mkdir(mode=0o700, exist_ok=True)
        self.voice_id = int(voice)
        if self.voice_id not in {603006, 602005, 603004}: raise ValueError('Unsupported voice')
        self.voice = {603006:'沉稳男声',602005:'知性女声',603004:'温柔女声'}[self.voice_id]
        self.available = configured(); self.lock = threading.Lock()

    def synthesize(self, text):
        text = re.sub(r'\[\[.*?\]\]', '', text, flags=re.S).strip()
        if not text or len(text) > 150: raise SpeechError('invalid_speech_text')
        key = hashlib.sha256((str(self.voice_id)+'\0'+text).encode()).hexdigest()
        target = self.directory / (key + self.extension)
        with self.lock:
            if target.is_file(): return key
            result = request('tts', 'TextToVoice', '2019-08-23', {'Text':text,'SessionId':str(uuid.uuid4()),
                'VoiceType':self.voice_id,'Codec':'mp3','SampleRate':16000,'Volume':0,'Speed':0,'PrimaryLanguage':1})
            try: data = base64.b64decode(result['Audio'], validate=True)
            except Exception as error: raise SpeechError('invalid_speech_audio') from error
            if len(data) < 16: raise SpeechError('invalid_speech_audio')
            temporary = target.with_suffix('.tmp');temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(target)
            for old in sorted(self.directory.glob('*.mp3'), key=lambda p:p.stat().st_mtime)[:-80]:old.unlink()
        return key

    def clear(self):
        with self.lock:
            for path in self.directory.glob('*'):
                if path.is_file():path.unlink()
