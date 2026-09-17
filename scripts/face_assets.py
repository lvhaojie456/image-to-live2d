#!/usr/bin/env python3
"""Extract measured expression features and register them on the model canvas."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion
from scipy.ndimage import distance_transform_edt


def rgba(path):
    with Image.open(path) as image:
        return image.convert('RGBA')


def polygon_mask(size, points):
    scale = 4
    mask = Image.new('L', (size[0]*scale, size[1]*scale))
    ImageDraw.Draw(mask).polygon([(round(x*scale), round(y*scale)) for x, y in points], fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def register_edit(image, reference_size, crop, target_size):
    x, y, width, height = crop
    rw, rh = reference_size
    # The portrait crop may contain padding beyond an image edge.
    side = max(rw, rh)
    if not all(math.isfinite(v) for v in (*crop,*reference_size,*target_size)) or min(width,height,rw,rh,*target_size)<=0:
        raise ValueError('Edit crop and canvases must have positive finite dimensions')
    if x>=rw or y>=rh or x+width<=0 or y+height<=0:
        raise ValueError('Edit crop must intersect the reference canvas')
    if x < -side or y < -side or x+width > rw+side or y+height > rh+side:
        raise ValueError('Edit crop extends beyond the permitted padded canvas')
    if target_size[0] != target_size[1]:
        raise ValueError('See-through registration requires a square model canvas')
    scale = side / target_size[0]
    px, py = (side-rw)//2, (side-rh)//2
    # Inverse mapping avoids repeated resampling of the small expression features.
    return image.transform(tuple(target_size), Image.Transform.AFFINE,
                           (scale*image.width/width, 0, (-px-x)*image.width/width,
                            0, scale*image.height/height, (-py-y)*image.height/height),
                           resample=Image.Resampling.BICUBIC)


def ink_layer(image, box, dark=115, light=195):
    if not 0 <= dark < light <= 255:
        raise ValueError('Invalid ink thresholds')
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
        raise ValueError('Ink region outside edit image')
    pixels = np.array(image).copy()
    brightness = pixels[..., :3].mean(axis=2)
    coverage = np.clip((light-brightness)/(light-dark), 0, 1)
    region = np.zeros(coverage.shape, dtype=bool)
    region[y1:y2, x1:x2] = True
    pixels[..., 3] = np.round(pixels[..., 3]*coverage*region).astype('uint8')
    pixels[pixels[..., 3] == 0, :3] = 0
    return Image.fromarray(pixels)


def mask_image(image, mask):
    result = np.array(image).copy()
    result[..., 3] = np.round(result[..., 3].astype(float)*np.array(mask)/255).astype('uint8')
    result[result[..., 3] == 0, :3] = 0
    return Image.fromarray(result)


def feathered_patch(image, box, feather=3):
    """Retain skin texture inside an expression patch and soften only its edge."""
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
        raise ValueError('Patch region outside edit image')
    mask = np.zeros((image.height, image.width), dtype=bool)
    mask[y1:y2, x1:x2] = True
    coverage = np.clip(distance_transform_edt(mask)/max(1,feather),0,1)
    return mask_image(image, Image.fromarray(np.round(coverage*255).astype('uint8')))


def build_face_assets(base, mouth, eyes, recipe_path, output):
    recipe = json.loads(recipe_path.read_text())
    expected = tuple(recipe['reference_size'])
    if rgba(base).size != expected:
        raise ValueError('Reference size does not match this measured recipe')
    if recipe.get('reference_sha256') and hashlib.sha256(base.read_bytes()).hexdigest() != recipe['reference_sha256']:
        raise ValueError('Recipe belongs to a different character reference')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose a new or empty face-assets output directory')
    output.mkdir(parents=True, exist_ok=True)
    m, e = rgba(mouth), rgba(eyes)
    edit_size = tuple(recipe['edit_size'])
    if m.size != edit_size or e.size != edit_size:
        raise ValueError('Edit dimensions changed; remeasure the expression regions')
    for name, path in [('mouth',mouth),('eyes',eyes)]:
        expected_hash = recipe.get('edit_sha256', {}).get(name)
        if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Expression pixels differ from this measured recipe: '+name)
    features = {}
    for name, spec in recipe['closed_eyes'].items():
        if spec.get('method') == 'texture_patch':
            features[name] = feathered_patch(e, spec['rect'], spec.get('feather',3))
        else:
            features[name] = ink_layer(e, spec['rect'], **spec.get('thresholds', {}))
    mouth_mask = polygon_mask(m.size, recipe['mouth']['outline'])
    full_mouth = mask_image(m, mouth_mask)
    teeth_mask = polygon_mask(m.size, recipe['mouth']['teeth'])
    tongue_mask = polygon_mask(m.size, recipe['mouth']['tongue'])
    # Preserve visible artwork; fill hidden cavity pixels under movable mouth parts.
    cavity = np.array(m).copy()
    sx, sy = recipe['mouth']['cavity_sample']
    replace = np.maximum(np.array(teeth_mask), np.array(tongue_mask)).astype(float)/255
    cavity[..., :3] = np.round(cavity[..., :3]*(1-replace[..., None]) + cavity[sy, sx, :3]*replace[..., None])
    interior = binary_erosion(np.array(mouth_mask)>127, iterations=3)
    features['mouth_open'] = mask_image(Image.fromarray(cavity), Image.fromarray((interior*255).astype('uint8')))
    features['tooth-t'] = mask_image(full_mouth, teeth_mask)
    features['tongue'] = mask_image(full_mouth, tongue_mask)
    ring = np.array(mouth_mask).copy()
    ring[interior] = 0
    top_ring = ring.copy()
    top_ring[recipe['mouth']['lip_split_y']:] = 0
    bottom_ring = ring.copy()
    bottom_ring[:recipe['mouth']['lip_split_y']] = 0
    features['lip_upper'] = mask_image(m, Image.fromarray(top_ring))
    features['lip_lower'] = mask_image(m, Image.fromarray(bottom_ring))
    (output/'native').mkdir()
    records = []
    for name, image in features.items():
        image.save(output/'native'/f'{name}.png')
        aligned = register_edit(image, expected, recipe['edit_crop'], recipe['model_canvas'])
        bbox = aligned.getchannel('A').getbbox()
        if bbox is None:
            raise ValueError('Extracted empty feature: '+name)
        aligned.save(output/f'{name}.png')
        records.append({'name':name, 'path':f'{name}.png', 'bbox':bbox,
                        'sha256':hashlib.sha256((output/f'{name}.png').read_bytes()).hexdigest()})
    register_edit(full_mouth, expected, recipe['edit_crop'], recipe['model_canvas']).save(output/'mouth_open_combined.png')
    provenance = {name: {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'name':path.name}
                  for name, path in [('reference',base),('mouth_edit',mouth),('eyes_edit',eyes)]}
    (output/'face-assets.json').write_text(json.dumps({'canvas':recipe['model_canvas'], 'features':records,
          'registration':'center square pad, then resize; feature masks are measured on native edit pixels',
          'sources':provenance, 'recipe':recipe}, indent=2))
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['base','mouth','eyes','recipe','output']:
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    build_face_assets(a.base, a.mouth, a.eyes, a.recipe, a.output)


if __name__ == '__main__':
    main()
