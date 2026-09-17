"""Explicit See-through semantic ordering and conservative disconnected-alpha cleanup."""
import json
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import label

ORDER = ['back hair', 'legwear', 'footwear', 'handwear', 'bottomwear', 'topwear',
         'neck', 'ears', 'face', 'nose', 'eyewhite', 'irides', 'eyelash', 'eyebrow',
         'mouth', 'front hair', 'headwear']


def prepare(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source/'manifest.json').read_text())
    changes = []
    def semantic(name):
        for i, value in enumerate(ORDER):
            if name == value or name.startswith(value+'-'):
                return i
        return len(ORDER)
    for l in manifest['layers']:
        image = np.array(Image.open(source/l['path']).convert('RGBA'))
        alpha = image[...,3].copy()
        components, count = label(alpha > 15)
        areas = np.bincount(components.ravel())
        if count:
            threshold = max(2, min(128, int(areas[1:].max()*0.0005)))
            keep = areas >= threshold
            keep[0] = False
            image[...,3][~keep[components]] = 0
        Image.fromarray(image).save(output/l['path'])
        old_z = l['z']
        l['z'] = semantic(l['name'])*100 + old_z
        changes.append(dict(name=l['name'], old_z=old_z, new_z=l['z'],
                            alpha_pixels_removed=int(np.count_nonzero(alpha != image[...,3]))))
    manifest['layers'].sort(key=lambda l:l['z'])
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (output/'preparation.json').write_text(json.dumps(dict(
        reason='Place lashes/brows above face and neck above filled shirt collar; remove disconnected alpha dust.',
        source='Unmodified PSD layers remain in ../layers', changes=changes),indent=2))
    canvas = Image.new('RGBA',(manifest['width'],manifest['height']))
    for l in manifest['layers']:
        im=Image.open(output/l['path']).convert('RGBA')
        im.putalpha(im.getchannel('A').point(lambda v:round(v*l['opacity'])))
        canvas.alpha_composite(im,(l['x'],l['y']))
    canvas.save(output/'composite.png')
