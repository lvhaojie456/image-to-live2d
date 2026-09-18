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
from scripts.auto_build import make_recipe, motion_recipe, verification_failure


def make_recipe_from_manifest(package):
    """Run make_recipe against a synthetic decomposition PSD and manifest."""
    from PIL import Image, ImageDraw
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer
    import numpy as np
    manifest = json.loads((package / 'authoring-manifest.json').read_text())
    size = manifest['width']
    psd = PSDImage.new('RGBA', (size, size), depth=8)
    for layer in manifest['layers']:
        x1, y1, x2, y2 = layer['bbox']
        pixels = np.zeros((size, size, 4), 'uint8')
        pixels[y1:y2, x1:x2] = (120, 90, 80, 255)
        image = Image.fromarray(pixels).crop(layer['bbox'])
        PixelLayer.frompil(image, parent=psd, name=layer['name'], left=x1, top=y1)
    psd.save(package / 'input.psd')
    reference = Image.new('RGB', (1024, 1536), 'white')
    ImageDraw.Draw(reference).rectangle((330, 40, 700, 560), fill=(230, 200, 180))
    reference.save(package / '01_input_white.png')
    from scripts.auto_build import face_crop
    eyes = Image.new('RGBA', (256, 384), (0, 0, 0, 0)); eyes.save(package / 'eyes.png')
    # Editor image: a dark cavity with teeth and tongue inside. The mouth box is generous so the
    # edit-space search box stays wider than the dilated aperture (otherwise the measurement
    # legitimately reports the search boundary).
    regions = {'face': [380, 60, 280, 480], 'left_eye': [520, 250, 44, 26], 'right_eye': [470, 250, 44, 26],
               'mouth': [430, 425, 200, 105]}
    _, crop_box = face_crop(Image.new('RGB', (1024, 1536), 'white'), regions['face'])
    x, y, w, h = regions['mouth']
    rect = [round((x - crop_box[0]) * 256 / crop_box[2]), round((y - crop_box[1]) * 384 / crop_box[3]),
            round(w * 256 / crop_box[2]), round(h * 384 / crop_box[3])]
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    # Skin-toned base: a transparent edit would read as dark everywhere and legitimately fail.
    mouth = Image.new('RGBA', (256, 384), (237, 206, 181, 255))
    draw = ImageDraw.Draw(mouth)
    draw.ellipse((cx - rect[2] * .22, cy - rect[3] * .30, cx + rect[2] * .22, cy + rect[3] * .30), fill=(45, 20, 20, 255))
    draw.rectangle((cx - rect[2] * .12, cy - rect[3] * .18, cx + rect[2] * .12, cy - rect[3] * .05), fill=(235, 230, 218, 255))
    draw.ellipse((cx - rect[2] * .13, cy + rect[3] * .08, cx + rect[2] * .13, cy + rect[3] * .26), fill=(190, 105, 100, 255))
    mouth.save(package / 'mouth.png')
    recipe_path = make_recipe(package / '01_input_white.png', package / 'input.psd', regions,
                              package / 'eyes.png', package / 'mouth.png', package)
    return json.loads(recipe_path.read_text())


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

    def test_motion_scale_shrinks_every_amplitude_and_verification_failures_are_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            manifest = {'width': 1000, 'height': 1000, 'layers': [
                {'name': 'topwear', 'bbox': [400, 180, 600, 450]}, {'name': 'legwear-l', 'bbox': [520, 560, 650, 950]},
                {'name': 'legwear-r', 'bbox': [350, 560, 480, 950]}, {'name': 'arm-l', 'bbox': [600, 220, 730, 500]},
                {'name': 'arm-r', 'bbox': [270, 220, 400, 500]}]}
            (package / 'authoring-manifest.json').write_text(json.dumps(manifest))
            full, small = motion_recipe(package), motion_recipe(package, 0.33)
            self.assertEqual(full['motion_scale'], 1.0); self.assertEqual(small['motion_scale'], 0.33)
            self.assertAlmostEqual(small['body']['lean_degrees'], 0.99); self.assertAlmostEqual(small['body']['breath_lift_px'], 1.32)
            self.assertAlmostEqual(small['arms']['l']['degrees'], 0.99); self.assertAlmostEqual(small['skirt']['sway_px'], 1.32)
            self.assertEqual(small['body']['pivot'], full['body']['pivot']); self.assertEqual(small['body']['lean_pin_y'], full['body']['lean_pin_y'])
            with self.assertRaises(ValueError):
                motion_recipe(package, 0.05)
            motion = package / 'body-motion'; (motion / 'verification').mkdir(parents=True)
            self.assertIsNone(verification_failure(motion))
            poses = [{'finite': True, 'trianglesFlippedFromNeutral': 0, 'degenerateTriangles': 0}] * 3
            (motion / 'verification/sequence-checks.json').write_text(json.dumps({'passed': False, 'feetMaxDisplacementPixels': 0.32, 'poses': poses}))
            found = verification_failure(motion)
            self.assertTrue(found['tunable']); self.assertEqual(found['poseCount'], 3)
            poses[1] = {'finite': True, 'trianglesFlippedFromNeutral': 0, 'degenerateTriangles': 4}
            (motion / 'verification/sequence-checks.json').write_text(json.dumps({'passed': False, 'feetMaxDisplacementPixels': 0.32, 'poses': poses}))
            self.assertFalse(verification_failure(motion)['tunable'])
            (motion / 'verification/sequence-checks.json').write_text(json.dumps({'passed': False, 'feetMaxDisplacementPixels': 0.0,
                'poses': [{'finite': True, 'trianglesFlippedFromNeutral': 2, 'degenerateTriangles': 0}]}))
            self.assertTrue(verification_failure(motion)['tunable'])

    def test_unified_handwear_layer_is_split_into_arms_and_hands(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            manifest = {'width': 1000, 'height': 1000, 'layers': [
                {'name': 'topwear', 'bbox': [400, 180, 600, 450]}, {'name': 'legwear-l', 'bbox': [520, 560, 650, 950]},
                {'name': 'legwear-r', 'bbox': [350, 560, 480, 950]},
                {'name': 'handwear', 'bbox': [300, 250, 700, 600]}]}
            (package / 'authoring-manifest.json').write_text(json.dumps(manifest))
            recipe = make_recipe_from_manifest(package)
            self.assertEqual(recipe['body_splits']['handwear'], 500)
            self.assertEqual(recipe['hand_splits']['handwear-l'][0][0], 500)
            self.assertEqual(recipe['hand_splits']['handwear-r'][1][0], 500)
            self.assertEqual(recipe['hand_splits']['handwear-l'][0][1], 250 + round(350 * .78))
            self.assertEqual(recipe['hand_splits']['handwear-l'][1][1], 250 + round(350 * .72))
            # Separate per-side layers keep the old per-layer cuff, untouched.
            manifest['layers'] = [l for l in manifest['layers'] if l['name'] != 'handwear'] + [
                {'name': 'handwear-l', 'bbox': [520, 250, 700, 600]}, {'name': 'handwear-r', 'bbox': [300, 250, 480, 600]}]
            (package / 'authoring-manifest.json').write_text(json.dumps(manifest))
            split = make_recipe_from_manifest(package)
            self.assertNotIn('handwear', split['body_splits'])
            self.assertEqual(split['hand_splits']['handwear-l'][0][0], 520)
            self.assertEqual(split['hand_splits']['handwear-r'][1][0], 480)

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
