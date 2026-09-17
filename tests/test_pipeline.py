import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live2d_pipeline import unpack_result
from scripts.auto_build import motion_recipe


class PipelineTests(unittest.TestCase):
    def test_auto_motion_recipe_uses_bbox_endpoints_and_keeps_feet_in_falloff(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            manifest = {
                'width': 1000, 'height': 1000,
                'layers': [
                    {'name': 'topwear', 'bbox': [400, 180, 600, 450]},
                    {'name': 'bottomwear', 'bbox': [360, 430, 640, 600]},
                    {'name': 'legwear-l', 'bbox': [520, 560, 650, 950]},
                    {'name': 'legwear-r', 'bbox': [350, 560, 480, 950]},
                    {'name': 'arm-l', 'bbox': [600, 220, 730, 500]},
                    {'name': 'arm-r', 'bbox': [270, 220, 400, 500]},
                ],
            }
            (package / 'authoring-manifest.json').write_text(json.dumps(manifest))
            recipe = motion_recipe(package)
            self.assertGreater(recipe['body']['pivot'][1], manifest['layers'][0]['bbox'][1])
            self.assertLess(recipe['body']['lean_pin_y'], 950)
            self.assertGreater(recipe['body']['lean_pin_y'], recipe['body']['lean_full_y'])
            manifest['layers']=[l for l in manifest['layers'] if l['name']!='bottomwear']
            (package / 'authoring-manifest.json').write_text(json.dumps(manifest))
            coat_recipe=motion_recipe(package)
            self.assertEqual(coat_recipe['skirt']['target_layers'],['topwear'])
            self.assertEqual(coat_recipe['skirt']['hem_y'],450)
            self.assertLess(coat_recipe['body']['lean_pin_y'],950)

    def test_artifact_integrity_and_zip_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            archive = out / 'result.zip'
            content = b'8BPS-fixture'
            hashes = {'input/test.psd': hashlib.sha256(content).hexdigest()}
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('input/test.psd', content)
                z.writestr('artifacts.json', json.dumps(hashes))
            expected = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(len(unpack_result(archive, out, expected)), 1)
            with self.assertRaises(RuntimeError):
                unpack_result(archive, out, '0' * 64)
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('../escape.psd', content)
            expected = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(RuntimeError):
                unpack_result(archive, out, expected)

    def test_worker_requires_real_output_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            (job / 'output').mkdir()
            spec = {'cwd': tmp, 'env': {}, 'command': [sys.executable, '-c', 'pass']}
            (job / 'spec.json').write_text(json.dumps(spec))
            subprocess.run([sys.executable, str(ROOT / 'scripts/remote_worker.py'), tmp], check=True)
            status = json.loads((job / 'status.json').read_text())
            self.assertEqual(status['state'], 'failed')
            self.assertIn('no PSD', status['error'])
            spec['command'] = [sys.executable, '-c', 'raise SystemExit(17)']
            (job / 'spec.json').write_text(json.dumps(spec))
            subprocess.run([sys.executable, str(ROOT / 'scripts/remote_worker.py'), tmp], check=True)
            self.assertIn('17', json.loads((job / 'status.json').read_text())['error'])


if __name__ == '__main__':
    unittest.main()
