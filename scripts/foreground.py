"""Detect background leaked into See-through layers and clip it away.

See-through treats a plain white background as part of the character and folds it
into a garment layer (bbox = whole canvas). Downstream the breathing deformer then
pins to the canvas bottom and moves the feet. This module detects that per layer
and rebuilds the decomposition PSD with the leaked pixels removed.

Mask sources, best first:
1. Real transparency in the source image (generated with a transparent background).
2. The per-layer depth map See-through returns (`input/<part>_depth.png`): leaked
   background saturates at >= 250 while the garment itself stays below.
3. Near-white pixels connected to the image border (white-background photos).
"""
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from scipy.ndimage import binary_fill_holes, label

LEAK_CANVAS_SHARE = 0.6      # layer bbox covering more of the canvas than this
LEAK_FAR_SHARE = 0.3         # or more of its pixels at saturated depth
FAR_DEPTH = 250
NEAR_WHITE = 238
ALPHA_OPAQUE = 128


def alpha_mask(source):
    """Foreground mask from real transparency, or None when the image is opaque."""
    with Image.open(source) as image:
        rgba = np.array(ImageOps.exif_transpose(image).convert('RGBA'))
    alpha = rgba[..., 3]
    transparent = (alpha == 0).mean()
    opaque = (alpha >= ALPHA_OPAQUE).mean()
    if transparent < 0.05 or opaque < 0.05:
        return None
    return alpha >= ALPHA_OPAQUE


def near_white_mask(image_path, threshold=NEAR_WHITE):
    """Foreground = everything not in a near-white region touching the border."""
    with Image.open(image_path) as image:
        rgb = np.array(image.convert('RGB'))
    corners = [rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]]
    if sum(int(c.min()) >= threshold for c in corners) < 3:
        return None
    white = (rgb.min(axis=2) >= threshold).astype('uint8')
    components, _ = label(white)
    border = set(np.unique(np.concatenate([components[0], components[-1], components[:, 0], components[:, -1]]))) - {0}
    background = np.isin(components, list(border))
    return largest_component(~background)


def largest_component(mask, open_size=5):
    mask = cv2.morphologyEx(mask.astype('uint8'), cv2.MORPH_OPEN, np.ones((open_size, open_size), 'uint8')).astype(bool)
    mask = binary_fill_holes(mask)
    components, count = label(mask)
    if count > 1:
        areas = np.bincount(components.ravel()); areas[0] = 0
        mask = components == areas.argmax()
    return mask


def to_canvas(mask, canvas_size):
    """Map an input-image mask onto the square See-through canvas (centred letterbox)."""
    h, w = mask.shape
    side = max(w, h)
    square = np.zeros((side, side), bool)
    px, py = (side - w) // 2, (side - h) // 2
    square[py:py + h, px:px + w] = mask
    resized = cv2.resize(square.astype('uint8'), (canvas_size, canvas_size), interpolation=cv2.INTER_NEAREST).astype(bool)
    # Two pixels of margin keep feathered edges of legitimate layers intact.
    return cv2.dilate(resized.astype('uint8'), np.ones((5, 5), 'uint8')).astype(bool)


def full_canvas(layer, size):
    """The layer's pixels placed on the full square canvas (parts outside are dropped)."""
    image = np.array(layer.composite().convert('RGBA'))
    full = np.zeros((size, size, 4), 'uint8')
    x1, y1, x2, y2 = layer.bbox
    sx, sy = max(0, -x1), max(0, -y1)
    dx1, dy1 = max(0, x1), max(0, y1)
    dx2, dy2 = min(x2, size), min(y2, size)
    if dx2 > dx1 and dy2 > dy1:
        full[dy1:dy2, dx1:dx2] = image[sy:sy + dy2 - dy1, sx:sx + dx2 - dx1]
    return full


def depth_map(psd_path, layer_name, size):
    part = layer_name.rsplit('-', 1)[0] if layer_name.endswith(('-l', '-r')) else layer_name
    path = psd_path.parent / 'input' / f'{part}_depth.png'
    if not path.is_file():
        return None
    with Image.open(path) as image:
        depth = np.array(image.convert('L'))
    return depth if depth.shape == (size, size) else None


def check_layers(psd_path, foreground=None):
    """Report every layer's canvas share and saturated-depth share; flag leaks."""
    psd = PSDImage.open(psd_path)
    size = psd.size[0]
    report = []
    for layer in psd.descendants():
        if layer.is_group():
            continue
        pixels = full_canvas(layer, size)
        alpha = pixels[..., 3] > 0
        count = int(alpha.sum())
        if not count:
            continue
        x, y, w, h = cv2.boundingRect(alpha.astype('uint8'))
        depth = depth_map(psd_path, layer.name, size)
        far = float(((depth >= FAR_DEPTH) & alpha).sum() / count) if depth is not None else None
        outside = float((alpha & ~foreground).sum() / count) if foreground is not None else None
        share = (w * h) / (size * size)
        leak = share > LEAK_CANVAS_SHARE or (far is not None and far > LEAK_FAR_SHARE) or (outside is not None and outside > LEAK_FAR_SHARE)
        report.append(dict(name=layer.name, pixels=count, bbox=[x, y, x + w, y + h], canvas_share=round(share, 4),
                           far_share=None if far is None else round(far, 4),
                           outside_mask_share=None if outside is None else round(outside, 4), leak=bool(leak)))
    return dict(canvas=size, layers=report, leaking=[r['name'] for r in report if r['leak']])


def clip_layer(pixels, depth, foreground):
    alpha = pixels[..., 3] > 0
    keep = alpha.copy()
    if depth is not None:
        keep &= depth < FAR_DEPTH
    if foreground is not None:
        keep &= foreground
    keep = cv2.morphologyEx(keep.astype('uint8'), cv2.MORPH_OPEN, np.ones((5, 5), 'uint8')).astype(bool)
    components, count = label(keep)
    if count > 1:
        areas = np.bincount(components.ravel()); areas[0] = 0
        keep = np.isin(components, [i for i in range(1, count + 1) if areas[i] >= areas.max() * 0.02])
    keep = binary_fill_holes(keep) & alpha
    clipped = pixels.copy()
    clipped[..., 3] = np.where(keep, clipped[..., 3], 0)
    clipped[clipped[..., 3] == 0, :3] = 0
    return clipped


def write_flat_psd(layers, size, path):
    """layers: list of (name, RGBA ndarray, visible, opacity). Order is bottom to top."""
    psd = PSDImage.new('RGBA', (size, size), depth=8)
    for name, pixels, visible, opacity in layers:
        image = Image.fromarray(pixels)
        box = image.getchannel('A').getbbox()
        if box is None:
            raise ValueError('Clipping removed the whole layer: ' + name)
        layer = PixelLayer.frompil(image.crop(box), parent=psd, name=name, left=box[0], top=box[1])
        if layer.has_mask():
            layer.remove_mask()
        layer.visible = visible
        layer.opacity = opacity
    psd.save(path)
    check = PSDImage.open(path)
    found = [l for l in check.descendants() if not l.is_group()]
    if len(found) != len(layers) or check.size != (size, size):
        raise ValueError('Clipped PSD round-trip mismatch')
    for layer, (name, pixels, _, _) in zip(found, layers):
        if layer.name != name:
            raise ValueError('Clipped PSD layer order changed')
        expected = Image.fromarray(pixels).crop(layer.bbox)
        if not np.array_equal(np.array(layer.topil().convert('RGBA')), np.array(expected)):
            raise ValueError('Clipped PSD pixel round-trip mismatch: ' + name)


def clip_background(psd_path, output_path, mask_path=None, report_dir=None):
    """Rebuild the PSD with leaked background removed. Returns the check report.

    `mask_path` is the input-image foreground mask written by neutralize_background.
    Layers that are not leaking are copied unchanged; only flagged layers are clipped
    by the depth map and the mask.
    """
    psd_path = Path(psd_path); output_path = Path(output_path)
    psd = PSDImage.open(psd_path)
    size = psd.size[0]
    mask_source = None
    foreground = None
    if mask_path is not None and Path(mask_path).is_file():
        with Image.open(mask_path) as opened:
            foreground, mask_source = to_canvas(np.array(opened.convert('L')) >= 128, size), 'input_mask'
    report = check_layers(psd_path, foreground)
    report.update(mask_source=mask_source, clipped=[])
    if not report['leaking']:
        return report
    layers = []
    for layer in psd.descendants():
        if layer.is_group():
            continue
        pixels = full_canvas(layer, size)
        if layer.name in report['leaking']:
            depth = depth_map(psd_path, layer.name, size)
            if depth is None and foreground is None:
                raise ValueError('No depth map or foreground mask to clip ' + layer.name)
            before = int((pixels[..., 3] > 0).sum())
            pixels = clip_layer(pixels, depth, foreground)
            after = int((pixels[..., 3] > 0).sum())
            x, y, w, h = cv2.boundingRect((pixels[..., 3] > 0).astype('uint8'))
            report['clipped'].append(dict(name=layer.name, pixels_before=before, pixels_after=after,
                                          bbox_after=[x, y, x + w, y + h], depth=depth is not None))
        layers.append((layer.name, pixels, layer.visible, layer.opacity))
    write_flat_psd(layers, size, output_path)
    if report_dir is not None:
        report_dir = Path(report_dir); report_dir.mkdir(parents=True, exist_ok=True)
        if foreground is not None:
            Image.fromarray((foreground * 255).astype('uint8')).save(report_dir / 'foreground_mask.png')
        (report_dir / 'foreground-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


NEUTRAL_GREY = (210, 210, 210)


def neutralize_background(source, output, grey=NEUTRAL_GREY, mask_output=None):
    """Write the decomposition input with any transparent or near-white studio
    background replaced by neutral grey. See-through keeps a plain white
    background as part of the character; a mid grey is separated cleanly.

    Returns {"source": "alpha" | "near_white" | "none", "foreground_share": float | None}.
    """
    with Image.open(source) as opened:
        rgba = ImageOps.exif_transpose(opened).convert('RGBA')
    pixels = np.array(rgba)
    mask = alpha_mask(source)
    kind = 'alpha' if mask is not None else 'none'
    if mask is None:
        flat = Image.fromarray(pixels[..., :3])
        temp = io.BytesIO(); flat.save(temp, 'PNG'); temp.seek(0)
        candidate = near_white_mask(temp)
        if candidate is not None and 0.05 <= candidate.mean() <= 0.95:
            mask, kind = candidate, 'near_white'
    if mask is None:
        canvas = Image.new('RGBA', rgba.size, 'white')
        canvas.alpha_composite(rgba)
        canvas.convert('RGB').save(output, format='PNG')
        return {'source': 'none', 'foreground_share': None}
    if kind == 'alpha':
        canvas = Image.new('RGBA', rgba.size, grey + (255,))
        canvas.alpha_composite(rgba)
        result = np.array(canvas.convert('RGB'))
    else:
        result = pixels[..., :3].copy()
        result[~mask] = grey
    Image.fromarray(result).save(output, format='PNG')
    if mask_output is not None:
        Path(mask_output).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray((mask * 255).astype('uint8')).save(mask_output, format='PNG')
    return {'source': kind, 'foreground_share': round(float(mask.mean()), 4)}
