"""Run the upstream See-through stages, waiting only for each stage's models."""
import argparse
import gc
import json
from pathlib import Path
import time


def wait_model(model, manifest):
    entries = [e for e in manifest if e['model'] == model.name]
    if not entries:
        raise RuntimeError('No pinned model manifest')
    while True:
        missing = [e['path'] for e in entries if not (model/e['path']).is_file()
                   or (model/e['path']).stat().st_size != e['size']]
        if not missing:
            return
        print('Waiting for verified model files:', model.name, missing[:3], flush=True)
        time.sleep(20)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--image', required=True)
    p.add_argument('--save-dir', required=True)
    p.add_argument('--models', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--resolution', type=int, default=1280)
    p.add_argument('--group-offload', action='store_true')
    a = p.parse_args()
    import torch
    from utils import inference_utils as upstream
    from utils.torch_utils import seed_everything
    out = Path(a.save_dir)
    out.mkdir(parents=True, exist_ok=True)
    models = Path(a.models)
    manifest = json.loads(Path(a.manifest).read_text())
    stages = {}
    def stage(name, fn):
        print('STAGE START:', name, flush=True)
        start = time.time()
        fn()
        stages[name] = {'seconds': time.time()-start, 'complete': True}
        (out/'stages.json').write_text(json.dumps(stages, indent=2))
        print('STAGE COMPLETE:', name, stages[name], flush=True)
    seed_everything(42)
    wait_model(models/'layer', manifest)
    stage('layerdiff', lambda: upstream.apply_layerdiff(a.image, str(models/'layer'),
          num_inference_steps=30, seed=42, save_dir=str(out), resolution=a.resolution,
          group_offload=a.group_offload))
    # Release generation model before allocating the depth model.
    upstream.layerdiff_pipeline = None
    gc.collect()
    torch.cuda.empty_cache()
    wait_model(models/'depth', manifest)
    stage('depth', lambda: upstream.apply_marigold(a.image, str(models/'depth'),
          seed=42, save_dir=str(out), resolution=768, group_offload=a.group_offload))
    stage('psd_export', lambda: upstream.further_extr(str(out/Path(a.image).stem),
          rotate=False, save_to_psd=True, tblr_split=True))


if __name__ == '__main__':
    main()
