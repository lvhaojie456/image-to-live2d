#!/usr/bin/env python3
"""Outbound worker connecting the local Live2D pipeline to Anyi's durable queue."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from live2d_pipeline import load_env


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


class Api:
    def __init__(self,base,token):
        parsed=urllib.parse.urlsplit(base)
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost'}):
            raise ValueError('Use HTTPS or localhost for ANYI_API_URL')
        if parsed.query or parsed.fragment or parsed.username or parsed.password or len(token)<32:
            raise ValueError('Invalid worker API configuration')
        self.base=base.rstrip('/')
        self.token=token
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None
        self.opener=urllib.request.build_opener(NoRedirect())

    def call(self,path,body=None,lease=None,binary=False,method=None,headers=None):
        if not path.startswith('/internal/live2d/') or path.startswith('//'):
            raise ValueError('Unexpected worker endpoint')
        request_headers={'Authorization':'Bearer '+self.token,**(headers or {})}
        if lease: request_headers['X-Live2d-Lease']=lease
        if isinstance(body,dict):
            body=json.dumps(body).encode()
            request_headers['Content-Type']='application/json'
        request=urllib.request.Request(self.base+path,data=body,headers=request_headers,
                                       method=method or ('POST' if body is not None else 'GET'))
        with self.opener.open(request,timeout=90) as response:
            data=response.read()
        return data if binary else json.loads(data)

    def upload(self,job,name,path):
        boundary='AnyiWorker'+uuid.uuid4().hex
        payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="artifact"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n').encode()+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        return self.call(f'/internal/live2d/jobs/{job["id"]}/artifacts?name='+urllib.parse.quote(name,safe=''),
                         payload,job['leaseToken'],headers={'Content-Type':'multipart/form-data; boundary='+boundary,
                                                          'X-Content-SHA256':digest(path)})


def safe_reference(root,relative):
    if not isinstance(relative,str) or not relative or any(p in {'.','..'} for p in relative.split('/')):
        raise ValueError('Invalid model file reference')
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise ValueError('Model file reference is outside the export')
    return path


def collect_delivery(workspace,destination):
    """Only publish referenced data assets; never publish source programs or local logs."""
    build=json.loads((workspace/'build.json').read_text())
    motion=workspace/'body-motion'
    core=json.loads((motion/'core-report.json').read_text())
    poses=json.loads((motion/'verification/sequence-checks.json').read_text())
    material=json.loads((workspace/'cubism-ready/validation.json').read_text())
    if build['status']!='complete' or not core['passed'] or not poses['passed'] or not material['psd_roundtrip_passed']:
        raise ValueError('Generation verification did not pass')
    model=motion/'model'
    manifest=json.loads((model/'MotionCharacter.model3.json').read_text())
    refs=manifest['FileReferences']
    with Image.open(workspace/'cubism-ready/neutral.png') as preview:
        bounds=preview.convert('RGBA').getchannel('A').getbbox()
        if bounds: manifest['AnyiBounds']=list(bounds)
    names=[refs['Moc'],*refs['Textures']]
    names += [refs[k] for k in ['Physics','Pose','DisplayInfo'] if refs.get(k)]
    names += [e['File'] for e in refs.get('Expressions',[])]
    for group in refs.get('Motions',{}).values():
        for item in group:
            if item.get('Sound'): raise ValueError('Audio assets are not supported')
            names.append(item['File'])
    destination.mkdir(parents=True,exist_ok=True)
    runtime=destination/'runtime'
    runtime.mkdir()
    for name in sorted(set(names)):
        source=safe_reference(model,name)
        target=runtime/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    # Chat drives the mouth; idle must not compete with it.
    for group in ['Idle','BodyIdle']:
        for entry in refs.get('Motions',{}).get(group,[]):
            path=runtime/entry['File']
            document=json.loads(path.read_text())
            curves=[c for c in document['Curves'] if c.get('Id') not in {'ParamMouthOpenY','ParamMouthForm'}]
            document['Curves']=curves
            document['Meta']['CurveCount']=len(curves)
            document['Meta']['TotalSegmentCount']=sum((len(c['Segments'])-2)//3 for c in curves)
            document['Meta']['TotalPointCount']=sum(1+(len(c['Segments'])-2)//3 for c in curves)
            path.write_text(json.dumps(document,indent=2))
    (runtime/'model.model3.json').write_text(json.dumps(manifest,indent=2))
    shutil.copy2(workspace/'cubism-ready/neutral.png',destination/'preview.png')
    shutil.copy2(motion/'verification/body-idle.gif',destination/'preview.gif')
    validation={'corePassed':True,'motionPassed':True,'psdPassed':True,'refinementRequired':True,
                'editorCompatibility':'unverified','poseCount':poses['poseCount'],
                'feetMaxDisplacementPixels':poses['feetMaxDisplacementPixels']}
    (destination/'validation.json').write_text(json.dumps(validation,indent=2))
    with zipfile.ZipFile(destination/'project.zip','w',zipfile.ZIP_DEFLATED,compresslevel=4) as archive:
        for folder in [workspace/'cubism-ready',model]:
            for path in sorted(folder.rglob('*')):
                if path.is_file() and path.suffix.lower() in {'.png','.psd','.cmo3','.moc3','.json','.md'} and not path.is_symlink():
                    archive.write(path,str(path.relative_to(workspace)))
        archive.writestr('REFINEMENT.md','Editable draft. CMO3 is generated by psd2live and can show Cubism 5.3 compatibility warnings. PSD is included. Check eyelids, lips, overlaps and physics in Cubism before publishing.\n')
    return {str(p.relative_to(destination)):p for p in sorted(destination.rglob('*')) if p.is_file()}


def process(api,job,workdir,python):
    uuid.UUID(job['id'])
    root=workdir/job['id']
    root.mkdir(parents=True,exist_ok=True)
    root.chmod(0o700)
    attempt=root/('attempt-'+uuid.uuid4().hex[:12])
    progress_path=root/'progress.json'
    progress={'stage':'preparing','progress':1}
    progress_path.write_text(json.dumps(progress))
    lost=threading.Event()
    done=threading.Event()
    def heartbeat():
        last_ok=time.monotonic()
        while not done.wait(15):
            try:
                if progress_path.exists(): progress.update(json.loads(progress_path.read_text()))
                api.call(f'/internal/live2d/jobs/{job["id"]}/heartbeat',progress,job['leaseToken'])
                last_ok=time.monotonic()
            except urllib.error.HTTPError as error:
                if error.code in {401,404,409}: lost.set(); return
            except (OSError,ValueError):
                pass
            if time.monotonic()-last_ok>75: lost.set(); return
    thread=threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    child=None
    try:
        source=None
        if job.get('inputPath'):
            source=root/'input.png'
            data=api.call(job['inputPath'],lease=job['leaseToken'],binary=True)
            if len(data)>8*1024*1024: raise ValueError('Input too large')
            source.write_bytes(data)
        previous=[]
        for old in root.glob('attempt-*'):
            generated=old/'01_generated.png'
            if source is None and generated.is_file(): source=generated
            if (old/'01_input_white.png').exists(): previous.append(old)
        command=[str(python),'-u',str(ROOT/'live2d_pipeline.py'),'build','--output',str(attempt)]
        command += ['--image',str(source)] if source else ['--prompt',job['prompt']]
        # Checkpoints belong to this immutable server job and never to another user.
        if previous:
            latest=max(previous,key=lambda p:p.stat().st_mtime)
            if (latest/'layer_plan.json').is_file(): command+=['--reuse-plan',str(latest/'layer_plan.json')]
            if (latest/'decomposition/input.psd').is_file(): command+=['--reuse-decomposition',str(latest/'decomposition/input.psd')]
            if all((latest/'expressions'/name).is_file() for name in ['expression_eyes.png','expression_mouth.png']):
                command+=['--reuse-expressions',str(latest/'expressions')]
        env={**os.environ,'LIVE2D_PROGRESS_FILE':str(progress_path)}
        env.pop('ANYI_WORKER_TOKEN',None)
        with (root/'worker-output.txt').open('w') as output:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            while child.poll() is None:
                if lost.wait(1):
                    raise RuntimeError('Worker lease lost')
            if child.returncode: raise RuntimeError('Generation failed; local output retained for retry')
        if lost.is_set(): raise RuntimeError('Worker lease lost')
        progress.update(stage='uploading',progress=96)
        progress_path.write_text(json.dumps(progress))
        delivery=collect_delivery(attempt,root/('delivery-'+uuid.uuid4().hex[:12]))
        for name,path in delivery.items():
            if lost.is_set(): raise RuntimeError('Worker lease lost')
            api.upload(job,name,path)
        api.call(f'/internal/live2d/jobs/{job["id"]}/complete',{},job['leaseToken'])
        print('Completed Live2D job '+job['id'],flush=True)
    except BaseException:
        if child and child.poll() is None:
            import signal
            os.killpg(child.pid,signal.SIGTERM)
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL)
                child.wait()
        if not lost.is_set():
            try: api.call(f'/internal/live2d/jobs/{job["id"]}/fail',{},job['leaseToken'])
            except Exception: pass
        print('Live2D job failed: '+job['id']+'; inspect the private worker directory',flush=True)
        raise
    finally:
        done.set()
        thread.join(timeout=95)


def main():
    load_env(Path(os.environ['ANYI_WORKER_ENV_FILE']).expanduser() if os.environ.get('ANYI_WORKER_ENV_FILE') else ROOT/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--work-dir',type=Path,default=ROOT/'work/anyi-jobs')
    parser.add_argument('--python',type=Path,default=Path(sys.executable))
    args=parser.parse_args()
    api=Api(os.environ['ANYI_API_URL'],os.environ['ANYI_WORKER_TOKEN'])
    while True:
        try:
            job=api.call('/internal/live2d/jobs/claim',{})['job']
            if job: process(api,job,args.work_dir.resolve(),args.python.expanduser().absolute())
        except KeyboardInterrupt: return
        except Exception as error:
            print('Worker request failed ('+type(error).__name__+')',flush=True)
            if args.once: raise SystemExit(1)
        if args.once: return
        time.sleep(10)


if __name__=='__main__': main()
