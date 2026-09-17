"""Detached inference worker. Status and output belong to exactly one job."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile


def write_status(job, **state):
    tmp = job / 'status.tmp'
    tmp.write_text(json.dumps({'updated_at': time.time(), **state}))
    tmp.replace(job / 'status.json')


def run(job):
    spec = json.loads((job / 'spec.json').read_text())
    write_status(job, state='running', pid=os.getpid())
    try:
        env = os.environ.copy()
        env.update(spec['env'])
        with (job / 'inference.out').open('ab', buffering=0) as stream:
            result = subprocess.run(spec['command'], cwd=spec['cwd'], env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Inference exit code {result.returncode}')
        out = job / 'output'
        psds = list(out.rglob('*.psd'))
        if not psds:
            raise RuntimeError('Inference returned success but produced no PSD')
        files = {}
        for path in out.rglob('*'):
            if path.is_file():
                files[path.relative_to(out).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        (out / 'artifacts.json').write_text(json.dumps(files, indent=2))
        archive = job / 'result.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as z:
            for path in out.rglob('*'):
                if path.is_file():
                    z.write(path, path.relative_to(out))
        write_status(job, state='complete', psd_count=len(psds),
                     archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    except Exception as exc:
        write_status(job, state='failed', error=str(exc))


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
