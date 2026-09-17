"""Export a decomposed PSD with a separately installed, pinned Agent Kit engine."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live2d_pipeline import load_env
from inspect_psd import inspect
from prepare_rig_layers import prepare


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--psd', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--kit', type=Path, default=ROOT / 'work/third_party/live2d-agent-kit')
    p.add_argument('--engine', type=Path, default=ROOT / 'work/third_party/psd2live')
    p.add_argument('--java-home', type=Path)
    p.add_argument('--core', type=Path, default=Path('/Applications/Live2D Cubism 5.3/res'))
    a = p.parse_args()
    load_env()
    java_home = a.java_home or Path(os.environ.get('LIVE2D_JAVA_HOME', ''))
    if not (java_home / 'bin/java').is_file():
        raise SystemExit('Set --java-home to a JDK 21 installation')
    output = a.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Choose a new, empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    inspect(a.psd.resolve(), output / 'layers')
    prepare(output/'layers', output/'rig_layers')
    env = os.environ.copy()
    env.update(JAVA_HOME=str(java_home.resolve()), PSD2LIVE_DIR=str(a.engine.resolve()),
               CUBISM_CORE_DIR=str(a.core.resolve()), VALIDATOR_JAVA=str(java_home.resolve()/'bin/java'))
    env['PATH'] = str(java_home.resolve()/'bin') + os.pathsep + env['PATH']
    kit = a.kit.resolve()
    with (output / 'export.out').open('w') as stream:
        subprocess.run(['bash', str(kit/'scripts/export-model.sh'),
                        str(output/'rig_layers/manifest.json'), str(output/'model')],
                       env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
    subprocess.run(['bash', str(kit/'scripts/validate_core.sh'),
                    str(output/'model/E2ECharacter.moc3'), str(output/'core-report.json')],
                   env=env, check=True)
    subprocess.run([sys.executable, str(kit/'scripts/validate.py'), '--model',
                    str(output/'model/E2ECharacter.model3.json'), '--core-report',
                    str(output/'core-report.json')], env=env, check=True)
    identity = {}
    for name, path in [('kit', kit), ('engine', a.engine.resolve())]:
        identity[name] = subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    (output/'source-versions.json').write_text(json.dumps(identity,indent=2))
    print('Export and Core checks complete:', output)


if __name__ == '__main__':
    main()
