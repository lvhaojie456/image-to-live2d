"""Provision the mouth-landmark helper: its own venv, plus the pinned model.

MediaPipe pins numpy 1.x and ships opencv-contrib, so it cannot share the
pipeline venv (numpy 2.x, opencv-python-headless). This writes a second venv
under `dependencies/mouth-landmarks` inside this repository and downloads the
Face Landmarker bundle at a pinned SHA-256. Safe to re-run; `--check` only reports.

    python scripts/install_mouth_landmarks.py [--root <dependencies>] [--check]
"""
import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

MEDIAPIPE_PIN = 'mediapipe==0.10.21'
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
             'face_landmarker/float16/1/face_landmarker.task')
MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
MODEL_BYTES = 3758596
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'dependencies'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--check', action='store_true', help='只检查，不安装')
    args = parser.parse_args(argv)
    target = args.root / 'mouth-landmarks'
    venv, model = target / 'venv', target / 'model/face_landmarker.task'
    report = {'venv': str(venv), 'model': str(model), 'mediapipe': MEDIAPIPE_PIN,
              'model_sha256': MODEL_SHA256}
    if args.check:
        report['venv_ready'] = (venv / 'bin/python').is_file()
        report['model_ready'] = model.is_file() and digest(model) == MODEL_SHA256
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['venv_ready'] and report['model_ready'] else 1
    if not (venv / 'bin/python').is_file():
        subprocess.run([str(args.python), '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv / 'bin/pip'), 'install', '--upgrade', 'pip'], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run([str(venv / 'bin/pip'), 'install', MEDIAPIPE_PIN], check=True)
    if not model.is_file() or digest(model) != MODEL_SHA256:
        model.parent.mkdir(parents=True, exist_ok=True)
        partial = Path(str(model) + '.partial')
        with urllib.request.urlopen(MODEL_URL, timeout=300) as response, partial.open('wb') as out:
            out.write(response.read())
        if digest(partial) != MODEL_SHA256:
            partial.unlink(missing_ok=True)
            raise SystemExit('下载校验失败：模型 SHA-256 不匹配')
        partial.replace(model)
    report['venv_ready'] = True
    report['model_ready'] = model.is_file() and digest(model) == MODEL_SHA256
    report['model_bytes'] = model.stat().st_size
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
