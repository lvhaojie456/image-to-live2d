import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
from psd_tools import PSDImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from foreground import (alpha_mask, check_layers, clip_background, near_white_mask, neutralize_background,
                        to_canvas, write_flat_psd)


def rgba(size, box=None, colour=(120, 60, 40, 255)):
    pixels = np.zeros((size, size, 4), 'uint8')
    if box:
        x1, y1, x2, y2 = box
        pixels[y1:y2, x1:x2] = colour
    return pixels


class ForegroundTests(unittest.TestCase):
    def test_alpha_and_near_white_masks_and_letterbox_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transparent = np.zeros((150, 100, 4), 'uint8'); transparent[30:140, 20:80] = (200, 150, 120, 250)
            Image.fromarray(transparent).save(root / 'alpha.png')
            mask = alpha_mask(root / 'alpha.png')
            self.assertEqual(mask.sum(), 110 * 60)
            opaque = np.full((150, 100, 3), 255, 'uint8'); opaque[30:140, 20:80] = (200, 150, 120)
            opaque[60:70, 40:50] = 255  # a white pocket inside the figure must stay foreground
            Image.fromarray(opaque).save(root / 'white.png')
            near = near_white_mask(root / 'white.png')
            self.assertTrue(near[65, 45] and near[35, 25] and not near[5, 5])
            self.assertIsNone(alpha_mask(root / 'white.png'))
            grey = np.full((150, 100, 3), 128, 'uint8'); Image.fromarray(grey).save(root / 'grey.png')
            self.assertIsNone(near_white_mask(root / 'grey.png'))
            canvas = to_canvas(mask, 60)
            # 100x150 sits centred in a 150x150 square, scaled to 60: x offset 25 -> 10, figure 20..80 -> 18..42 (+2 px margin)
            ys, xs = np.where(canvas)
            self.assertEqual((xs.min(), xs.max()), (16, 43))
            self.assertLess(ys.min(), 14); self.assertGreater(ys.max(), 53)

    def test_neutralize_background_greys_transparent_and_white_studio_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transparent = np.zeros((150, 100, 4), 'uint8'); transparent[30:140, 20:80] = (200, 150, 120, 253)
            Image.fromarray(transparent).save(root / 'alpha.png')
            info = neutralize_background(root / 'alpha.png', root / 'out1.png', mask_output=root / 'mask1.png')
            out = np.array(Image.open(root / 'out1.png').convert('RGB'))
            self.assertEqual(info['source'], 'alpha')
            self.assertEqual(tuple(out[0, 0]), (210, 210, 210))
            self.assertEqual(np.array(Image.open(root / 'mask1.png'))[65, 45], 255)
            white = np.full((150, 100, 3), 254, 'uint8'); white[30:140, 20:80] = (200, 150, 120)
            Image.fromarray(white).save(root / 'white.png')
            info = neutralize_background(root / 'white.png', root / 'out2.png')
            out = np.array(Image.open(root / 'out2.png').convert('RGB'))
            self.assertEqual(info['source'], 'near_white')
            self.assertEqual(tuple(out[0, 0]), (210, 210, 210)); self.assertEqual(tuple(out[65, 45]), (200, 150, 120))
            busy = np.random.default_rng(1).integers(0, 200, (150, 100, 3), dtype='uint8')
            Image.fromarray(busy).save(root / 'busy.png')
            info = neutralize_background(root / 'busy.png', root / 'out3.png')
            self.assertEqual(info['source'], 'none')
            self.assertTrue(np.array_equal(np.array(Image.open(root / 'out3.png').convert('RGB')), busy))

    def test_leaked_layer_is_detected_and_clipped_by_depth_and_mask_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); size = 120
            (root / 'input').mkdir()
            face = rgba(size, (50, 10, 70, 30), (230, 200, 180, 255))
            legwear = rgba(size, (45, 70, 75, 110), (40, 40, 60, 255))
            # topwear = real garment (40..80 x 30..70) plus leaked background over the whole canvas
            topwear = rgba(size, (0, 0, size, size), (245, 245, 245, 255)); topwear[30:70, 40:80] = (90, 60, 50, 255)
            depth = np.full((size, size), 255, 'uint8'); depth[30:70, 40:80] = 150
            Image.fromarray(depth).save(root / 'input' / 'topwear_depth.png')
            write_flat_psd([('topwear', topwear, True, 255), ('legwear', legwear, True, 255), ('face', face, True, 255)], size, root / 'input.psd')
            report = check_layers(root / 'input.psd')
            self.assertEqual(report['leaking'], ['topwear'])
            self.assertFalse(any(l['leak'] for l in report['layers'] if l['name'] != 'topwear'))
            # a foreground mask in input coordinates (same size as canvas here)
            mask = np.zeros((size, size), bool); mask[5:115, 35:85] = True
            Image.fromarray((mask * 255).astype('uint8')).save(root / 'mask.png')
            result = clip_background(root / 'input.psd', root / 'clipped.psd', mask_path=root / 'mask.png', report_dir=root / 'report')
            self.assertEqual(result['mask_source'], 'input_mask')
            self.assertEqual(result['clipped'][0]['name'], 'topwear')
            self.assertEqual(result['clipped'][0]['bbox_after'], [40, 30, 80, 70])
            clipped = PSDImage.open(root / 'clipped.psd')
            names = [l.name for l in clipped.descendants() if not l.is_group()]
            self.assertEqual(names, ['topwear', 'legwear', 'face'])
            by_name = {l.name: l for l in clipped.descendants() if not l.is_group()}
            self.assertEqual(by_name['topwear'].bbox, (40, 30, 80, 70))
            self.assertEqual(by_name['face'].bbox, (50, 10, 70, 30))
            self.assertTrue(np.array_equal(np.array(by_name['legwear'].topil().convert('RGBA')), legwear[70:110, 45:75]))
            self.assertTrue((root / 'report' / 'foreground-check.json').is_file())
            self.assertTrue((root / 'report' / 'foreground_mask.png').is_file())
            clean = check_layers(root / 'clipped.psd')
            self.assertEqual(clean['leaking'], [])

    def test_clean_decomposition_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); size = 100
            write_flat_psd([('topwear', rgba(size, (30, 20, 70, 60)), True, 255), ('face', rgba(size, (40, 5, 60, 20)), True, 255)], size, root / 'input.psd')
            result = clip_background(root / 'input.psd', root / 'clipped.psd')
            self.assertEqual(result['leaking'], []); self.assertEqual(result['clipped'], [])
            self.assertFalse((root / 'clipped.psd').exists())


if __name__ == '__main__':
    unittest.main()
