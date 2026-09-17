"""Decode every PSD layer, render contact sheets, and export positioned PNGs."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw
from psd_tools import PSDImage


def inspect(path, output):
    output.mkdir(parents=True, exist_ok=True)
    source = PSDImage.open(path)
    canvas = Image.new('RGBA', source.size)
    layers = []
    previews = []
    for i, layer in enumerate(source.descendants()):
        if layer.is_group():
            continue
        img = layer.topil()
        if img is None:
            continue
        img = img.convert('RGBA')
        bbox = img.getchannel('A').getbbox()
        filename = f'layer_{i:03d}.png'
        img.save(output / filename)
        layers.append(dict(name=layer.name, path=filename, x=layer.left, y=layer.top,
                           z=i, opacity=layer.opacity, visible=layer.visible,
                           empty=bbox is None, width=img.width, height=img.height))
        if layer.visible:
            visible = img.copy()
            if layer.opacity != 255:
                visible.putalpha(visible.getchannel('A').point(lambda v: round(v*layer.opacity/255)))
            canvas.alpha_composite(visible, (layer.left, layer.top))
        tile = Image.new('RGB', (256, 300), '#d8dde5')
        thumb = img.copy()
        thumb.thumbnail((246, 260))
        tile.paste(thumb, ((256-thumb.width)//2, 12), thumb)
        ImageDraw.Draw(tile).text((8, 278), f'{i}: {layer.name}', fill='#202735')
        previews.append(tile)
    canvas.save(output / 'composite.png')
    sheet = Image.new('RGB', (4*256, ((len(previews)+3)//4)*300), '#ffffff')
    for i, tile in enumerate(previews):
        sheet.paste(tile, ((i%4)*256, (i//4)*300))
    sheet.save(output / 'layers_contact_sheet.jpg', quality=90)
    manifest = dict(name='E2ECharacter', width=source.width, height=source.height,
                    config={'mesh_spacing': 48, 'head_strength': 0.3, 'body_strength': 0.3},
                    layers=[{**{k:v for k,v in l.items() if k in {'name','path','x','y','z'}},
                             'opacity': l['opacity'] / 255.0}
                            for l in layers if l['visible'] and not l['empty']])
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    report = dict(psd_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  width=source.width, height=source.height, layer_count=len(layers),
                  nonempty_layers=sum(not l['empty'] for l in layers), layers=layers,
                  validation_scope='PSD decoding and static layer compositing only; animation and Cubism import require separate verification')
    (output / 'psd_report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != 'layers'}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('psd', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    inspect(a.psd, a.output)
