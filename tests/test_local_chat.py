from http.cookies import SimpleCookie
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid
import base64
import io
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters.local_chat.server import Companion, Conversation, RequestError, Speech, ThreadingHTTPServer, handler_for, safe_asset
from adapters.local_chat import tencent_speech


class Stream:
    def __init__(self, text='你好，我在这里。'): self.text=text; self.closed=False
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
    def close(self): self.closed=True
    def __iter__(self):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=self.text),finish_reason=None)])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None),finish_reason='stop')])


class LocalChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_history_keeps_context_idempotently_and_clear_fences_late_reply(self):
        store=Conversation(self.root/'data');request=str(uuid.uuid4())
        generation,previous,messages=store.begin('我叫小林',request)
        self.assertIsNone(previous);self.assertEqual(messages[-1]['content'],'我叫小林')
        reply=store.finish(generation,request,'小林，你好。')
        _,duplicate,_=store.begin('我叫小林',request);self.assertEqual(duplicate,reply)
        self.assertEqual(len(store.snapshot()['messages']),2)
        with self.assertRaises(RequestError):store.begin('不同的内容',request)
        generation,_,messages=store.begin('记住我了吗',str(uuid.uuid4()))
        self.assertIn('小林',json.dumps(messages,ensure_ascii=False))
        store.clear();self.assertIsNone(store.finish(generation,str(uuid.uuid4()),'迟到的回复'))
        self.assertEqual(Conversation(self.root/'data').messages,[])
        self.assertEqual(store.path.stat().st_mode&0o777,0o600)

    def test_http_blocks_cross_site_and_private_paths_and_streams_reply(self):
        model=self.root/'model';runtime=self.root/'runtime';model.mkdir();runtime.mkdir()
        (model/'model.model3.json').write_text(json.dumps({'FileReferences':{'Moc':'model.moc3','Textures':['atlas.png']}}))
        (model/'model.moc3').write_bytes(b'MOC3fixture');(model/'atlas.png').write_bytes(b'png')
        (model/'secret.env').write_text('private-data')
        for name in ['live2dcubismcore.min.js','pixi.min.js','cubism4.min.js']:(runtime/name).write_text('')
        calls=[]
        def create(**kwargs):calls.append(kwargs);return Stream()
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        app=Companion(model/'model.model3.json',runtime,self.root/'chat',client,'test-chat')
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(app));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base) as r:cookie=r.headers['Set-Cookie'].split(';')[0]
        def request(path,body=None,headers=None,auth=True):
            h={'Cookie':cookie} if auth else {}
            h.update(headers or {})
            if body is not None:h.update({'Content-Type':'application/json','X-Live2d-Client':'local'})
            try:
                with urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=h),timeout=5) as r:
                    return r.status,r.read()
            except HTTPError as e:return e.code,e.read()
        self.assertEqual(request('/api/state',auth=False)[0],401)
        self.assertEqual(request('/api/state',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(request('/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(request('/model/secret.env')[0],404)
        self.assertEqual(request('/model/../secret.env')[0],404)
        key=str(uuid.uuid4());body={'text':'你好','requestId':key}
        status,stream=request('/api/chat',body);self.assertEqual(status,200)
        events=[json.loads(line) for line in stream.splitlines()]
        self.assertEqual([e['type'] for e in events],['start','delta','done'])
        self.assertEqual(events[-1]['message']['content'],'你好，我在这里。')
        self.assertEqual(request('/api/chat',body)[0],200);self.assertEqual(len(calls),1)
        self.assertEqual(request('/api/clear',{})[0],200)
        self.assertEqual(json.loads(request('/api/state')[1])['messages'],[])
        self.assertNotIn(app.session,request('/api/state')[1].decode())

    def test_paths_cannot_escape_via_symlinks(self):
        assets=self.root/'assets';assets.mkdir();(self.root/'secret').write_text('secret')
        (assets/'escape').symlink_to(self.root/'secret')
        for path in ['../secret','escape','/secret','a\\b']:
            with self.assertRaises(RequestError):safe_asset(assets,path)

    def test_tts_disables_speech_markup_and_never_uses_a_shell(self):
        speech=Speech(self.root);speech.available=True
        commands=[]
        def run(args,**kwargs):
            commands.append(args);self.assertNotIn('shell',kwargs)
            if args[0]=='say':
                source=Path(args[args.index('-f')+1]);self.assertNotIn('[[',source.read_text())
                Path(args[args.index('-o')+1]).write_bytes(b'aiff')
            else:Path(args[-1]).write_bytes(b'RIFFwav')
        with patch('adapters.local_chat.server.subprocess.run',run):
            key=speech.synthesize('你好[[slnc 99999]]');self.assertEqual(len(commands),2)
            self.assertEqual(speech.synthesize('你好[[slnc 99999]]'),key);self.assertEqual(len(commands),2)
        self.assertEqual(list(speech.directory.glob('*.txt')),[])
        speech.clear();self.assertEqual(list(speech.directory.glob('*')),[])

    def test_asr_accepts_only_bounded_mono_16k_pcm_wav_and_returns_text(self):
        def wav(rate=16000,channels=1,seconds=1):
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as file:
                file.setnchannels(channels);file.setsampwidth(2);file.setframerate(rate)
                file.writeframes(b'\0\0'*int(rate*seconds)*channels)
            return base64.b64encode(buffer.getvalue()).decode()
        valid=wav()
        with patch.object(tencent_speech,'request',return_value={'Result':'你好，小林。'}) as request:
            self.assertEqual(tencent_speech.transcribe(valid),'你好，小林。')
            args=request.call_args.args;self.assertEqual(args[:3],('asr','SentenceRecognition','2019-06-14'))
            self.assertEqual(args[3]['VoiceFormat'],'wav');self.assertEqual(args[3]['Data'],valid)
        for bad in [wav(rate=24000),wav(channels=2),wav(seconds=.1),wav(seconds=61),'not base64',None]:
            with self.assertRaises(tencent_speech.SpeechError):tencent_speech.validate_wav(bad)

    def test_cloud_speech_caches_by_voice_and_text_without_exposing_credentials(self):
        speech=tencent_speech.TencentSpeech(self.root)
        with patch.object(tencent_speech,'request',return_value={'Audio':base64.b64encode(b'ID3'+b'a'*30).decode()}) as request:
            key=speech.synthesize('你好');self.assertEqual(speech.synthesize('你好'),key);self.assertEqual(request.call_count,1)
            self.assertEqual(request.call_args.args[3]['VoiceType'],603006)
            self.assertTrue((speech.directory/(key+'.mp3')).exists())
        speech.clear();self.assertEqual(list(speech.directory.iterdir()),[])


if __name__=='__main__':unittest.main()
