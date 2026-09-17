#!/usr/bin/env python3
"""Create a self-contained Cubism authoring package with verified expression layers."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer
from scipy.ndimage import distance_transform_edt

from face_assets import rgba, feathered_patch
from inspect_psd import inspect
from prepare_rig_layers import prepare

OPEN_MOUTH = {'mouth_open', 'tooth-t', 'tongue', 'lip_upper', 'lip_lower'}
OPEN_EYES = {'eyewhite-l', 'eyewhite-r', 'irides-l', 'irides-r', 'eyelash-l', 'eyelash-r'}
CLOSED_EYES = {'eye_close-l', 'eye_close-r'}
GROUPS = [
    ('01_BackHair', {'back hair'}),
    ('02_Legs', {'legwear-l', 'legwear-r', 'footwear-l', 'footwear-r'}),
    ('03_Arms', {'arm-l', 'arm-r', 'hand-l', 'hand-r'}),
    ('04_Clothing', {'bottomwear', 'topwear', 'neckwear'}),
    ('05_Neck', {'neck'}), ('06_Ears', {'ears-l', 'ears-r'}),
    ('07_Face', {'face', 'nose'}),
    ('08_EyeWhite', {'eyewhite-l', 'eyewhite-r'}),
    ('09_Iris', {'irides-l', 'irides-r'}),
    ('10_OpenLashes', {'eyelash-l', 'eyelash-r'}),
    ('11_Brows', {'eyebrow-l', 'eyebrow-r'}),
    ('12_ClosedEyes', CLOSED_EYES), ('13_ClosedMouth', {'mouth_close'}),
    ('14_OpenMouth', OPEN_MOUTH), ('15_FrontHair', {'front hair'}),
    ('16_Headwear', {'headwear'})
]


def load_positioned(root, layer, size):
    im = rgba(root/layer['path'])
    canvas = Image.new('RGBA', size)
    im.putalpha(im.getchannel('A').point(lambda v: round(v*layer['opacity'])))
    canvas.alpha_composite(im, (layer['x'], layer['y']))
    return canvas


def repair_face(image, spec):
    if spec.get('mode') == 'preserve_texture':
        return image.copy()
    pixels = np.array(image).copy()
    alpha = pixels[..., 3]
    box = image.getchannel('A').getbbox()
    if box is None:
        raise ValueError('Face layer is empty')
    x0, y0, x1, y1 = box
    points = list(spec.get('sample_points', []))
    if not points:
        # Select conservative skin samples from the lower half of the detected face.
        # This is a fallback for a new character; Astra's measured points take precedence.
        candidates = []
        for yy in range(y0 + (y1-y0)//2, max(y0+1, y1-4), max(1,(y1-y0)//8)):
            for xx in range(x0 + (x1-x0)//5, x1 - max(1,(x1-x0)//5), max(1,(x1-x0)//5)):
                if alpha[yy,xx] > 200:
                    candidates.append((xx,yy))
        points = candidates[:6]
    if len(points) < 2:
        return image
    colors = []
    for x, y in points:
        if not (0 <= x < image.width and 0 <= y < image.height and alpha[y, x] > 200):
            raise ValueError('Skin sample must be on opaque face pixels')
        colors.append(np.median(pixels[max(0,y-1):y+2, max(0,x-1):x+2, :3], axis=(0,1)))
    yy, xx = np.mgrid[y0:y1, x0:x1]
    weights = np.stack([1/((xx-x)**2+(yy-y)**2+36) for x,y in points])
    colors = np.asarray(colors)
    fill = np.einsum('nhw,nc->hwc', weights, colors)/weights.sum(axis=0)[..., None]
    distance = distance_transform_edt(alpha[y0:y1,x0:x1] > 32)
    fade = np.clip((spec['fade_end_y']-yy)/max(1,spec['fade_end_y']-spec['fade_start_y']),0,1)
    blend = np.clip((distance-1)/2,0,1)*fade
    pixels[y0:y1,x0:x1,:3] = np.round(pixels[y0:y1,x0:x1,:3]*(1-blend[...,None])+fill*blend[...,None])
    return Image.fromarray(pixels)


def partition(image, boundary):
    pixels = np.array(image)
    first, second = pixels.copy(), pixels.copy()
    first[...,3][boundary] = 0
    second[...,3][~boundary] = 0
    first[first[...,3]==0,:3] = 0
    second[second[...,3]==0,:3] = 0
    return Image.fromarray(first), Image.fromarray(second)


def ordered_names(layers):
    names = [n for _, members in GROUPS for n in sorted(members) if n in layers]
    names += sorted(set(layers)-set(names))
    return names


def composite(layers, size, eyes_closed=False, mouth_open=False):
    canvas = Image.new('RGBA', size)
    excluded = OPEN_EYES if eyes_closed else CLOSED_EYES
    excluded = excluded | ({'mouth_close'} if mouth_open else OPEN_MOUTH)
    for name in ordered_names(layers):
        if name not in excluded:
            canvas.alpha_composite(layers[name])
    return canvas


def write_psd(layers, size, path, hidden):
    psd = PSDImage.new('RGBA', size, depth=8)
    included = set()
    for group_name, members in GROUPS + [('17_Other',set(layers)-set().union(*(m for _,m in GROUPS)))]:
        actual = [name for name in ordered_names(layers) if name in members]
        if not actual:
            continue
        group = Group.new(psd, name=group_name)
        for name in actual:
            im = layers[name]
            box = im.getchannel('A').getbbox()
            if box is None:
                raise ValueError('Empty authored layer: '+name)
            layer = PixelLayer.frompil(im.crop(box), parent=group, name=name, left=box[0], top=box[1])
            # frompil adds an alpha mask as well as transparency, which squares coverage.
            if layer.has_mask():
                layer.remove_mask()
            layer.visible = name not in hidden
            included.add(name)
    if included != set(layers):
        raise ValueError('Lost layers during PSD grouping')
    psd.save(path)
    check = PSDImage.open(path)
    found = [l for l in check.descendants() if not l.is_group()]
    if len(found) != len(layers) or check.size != size:
        raise ValueError('PSD round-trip layer count or canvas mismatch')
    for layer in found:
        if layer.visible != (layer.name not in hidden):
            raise ValueError('PSD lost expression visibility')
        x1,y1,x2,y2=layer.bbox
        if min(x1,y1)<0 or x2>size[0] or y2>size[1]:
            raise ValueError('Layer extends outside canvas: '+layer.name)
        decoded = layer.topil().convert('RGBA')
        expected = layers[layer.name].crop(layer.bbox)
        if not np.array_equal(np.array(decoded), np.array(expected)):
            raise ValueError('PSD pixel round-trip mismatch: '+layer.name)
        if layer.has_mask():
            raise ValueError('Unexpected duplicate alpha mask: '+layer.name)
    return len(found)


def build(source, face_assets, recipe_path, out):
    recipe = json.loads(recipe_path.read_text())
    if recipe.get('source_psd_sha256') and hashlib.sha256(source.read_bytes()).hexdigest() != recipe['source_psd_sha256']:
        raise ValueError('Refinement recipe belongs to a different decomposition')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose a new, empty output directory')
    out.mkdir(parents=True, exist_ok=True)
    (out/'sources').mkdir()
    shutil.copy2(source, out/'sources/source_decomposition.psd')
    shutil.copy2(recipe_path,out/'recipe.json')
    shutil.copytree(face_assets, out/'face_assets')
    inspect(source,out/'sources/raw_layers')
    prepare(out/'sources/raw_layers',out/'sources/prepared_layers')
    raw = json.loads((out/'sources/prepared_layers/manifest.json').read_text())
    size = (raw['width'], raw['height'])
    if size != tuple(recipe['model_canvas']):
        raise ValueError('Model canvas differs from the measured recipe')
    layers = {l['name']:load_positioned(out/'sources/prepared_layers',l,size) for l in raw['layers']}
    if len(layers) != len(raw['layers']):
        raise ValueError('Duplicate layer names require manual classification')
    layers['face'] = repair_face(layers['face'],recipe['face_repair'])
    for name, x in recipe.get('body_splits', {}).items():
        if name not in layers:
            continue
        yy,xx=np.mgrid[:size[1],:size[0]]
        viewer_left, viewer_right = partition(layers.pop(name),xx>=x)
        layers[name+'-r'],layers[name+'-l']=viewer_left,viewer_right
    for name, line in recipe.get('hand_splits', {}).items():
        if name not in layers:
            continue
        (x1,y1),(x2,y2)=line
        if x1==x2:
            raise ValueError('Cuff boundary must have nonzero horizontal span')
        yy,xx=np.mgrid[:size[1],:size[0]]
        cuff_y = y1+(xx-x1)*(y2-y1)/(x2-x1)
        sleeve, hand=partition(layers.pop(name),yy>=cuff_y)
        side=name.rsplit('-',1)[1]
        layers['arm-'+side],layers['hand-'+side]=sleeve,hand
    if 'mouth' in layers:
        layers['mouth_close']=layers.pop('mouth')
    elif recipe.get('closed_mouth_region'):
        # This is real neutral-mouth artwork from the source face. The open-mouth
        # silhouette covers it when open; no synthetic placeholder is exported.
        layers['mouth_close']=feathered_patch(layers['face'],recipe['closed_mouth_region'],2)
        if not layers['mouth_close'].getchannel('A').getbbox():
            raise ValueError('Neutral mouth extraction did not intersect the face')
    else:
        raise ValueError('Missing mouth layer and no measured neutral-mouth region')
    metadata=json.loads((face_assets/'face-assets.json').read_text())
    if metadata['recipe'] != recipe:
        raise ValueError('Face assets and body preparation must use the same recipe')
    for entry in metadata['features']:
        path=face_assets/entry['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('Expression asset differs from recorded hash')
        image=rgba(path)
        if image.size!=size:
            raise ValueError('Expression asset is not registered to the PSD canvas')
        layers[entry['name']]=image
    (out/'layers').mkdir()
    records=[]
    for index,name in enumerate(ordered_names(layers)):
        filename=f'{index:02d}_{name.replace(" ","_")}.png'
        layers[name].save(out/'layers'/filename)
        box=layers[name].getchannel('A').getbbox()
        records.append(dict(name=name,path='layers/'+filename,x=0,y=0,z=index,opacity=1,
                            bbox=list(box) if box else None,
                            visible=name not in CLOSED_EYES|OPEN_MOUTH))
    count=write_psd(layers,size,out/'cubism_refinement.psd',CLOSED_EYES|OPEN_MOUTH)
    # Alternate artwork is also supplied visibly in its own PSD, for importers that skip hidden layers.
    expression={n:layers[n] for n in CLOSED_EYES|OPEN_MOUTH}
    write_psd(expression,size,out/'expression_parts.psd',set())
    pose_files=[]
    for name,eyes,mouth in [('neutral',False,False),('closed',True,False),
                             ('mouth-open',False,True),('closed-mouth-open',True,True)]:
        image=composite(layers,size,eyes,mouth)
        image.save(out/(name+'.png'))
        pose_files.append(name+'.png')
    face_box=layers['face'].getchannel('A').getbbox()
    x1,y1,x2,y2=face_box
    view=(max(0,x1-45),max(0,y1-50),min(size[0],x2+50),min(size[1],y2+35))
    sheet=Image.new('RGB',(4*350,410),'#e9edf0')
    for i,name in enumerate(pose_files):
        im=rgba(out/name).crop(view)
        im.thumbnail((330,365),Image.Resampling.LANCZOS)
        sheet.paste(im,(i*350+(350-im.width)//2,15),im)
        ImageDraw.Draw(sheet).text((i*350+12,389),name,fill='#202020')
    sheet.save(out/'expressions-review.png')
    manifest=dict(name='RefinementCharacter',width=size[0],height=size[1],layers=records,
                  stage='materials_only_no_keyforms',
                  manual_parameter_targets=recipe['manual_targets'])
    (out/'authoring-manifest.json').write_text(json.dumps(manifest,indent=2))
    report=dict(stage='ready_for_manual_rigging',psd_layer_count=count,canvas=size,
                expression_layers=list(sorted(expression)),coordinate_checks_passed=True,
                psd_roundtrip_passed=True,psd_pixels_equal_source_layers=True,
                cubism_gui_import_verified=False,
                actual_static_pose_previews=pose_files,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                remaining_work=['Arm, leg and skirt keyforms are not authored by this material preparation stage.',
                    'Sleeves and hands have exact complementary cuff cuts; create concealed overlap before large limb rotation.',
                    'Face underpainting is sampled skin; match shading for large head turns.',
                    'Mouth parts are individually editable, but smooth open/form interpolation still needs Cubism keyforms.'])
    (out/'validation.json').write_text(json.dumps(report,indent=2))
    text = """# Cubism 5.3 精修起点

本目录是素材精修包。已分离闭眼线、嘴腔、上牙、舌头和唇缘，清理底脸残影并校正绘制顺序。
这不是完成绑定的直播模型。

## 打开

- 从 cubism_refinement.psd 新建模型。默认显示睁眼、闭嘴；所有层使用同一画布坐标。
- expression_parts.psd 中的替代表情全部可见。若编辑器忽略主 PSD 的隐藏层，用它追加导入，或使用 layers/ 中的同画布 PNG。
- layers/ 保存实际透明素材；face_assets/native/ 保留本次 AI 编辑中抽取的高分辨率局部像素。
- sources/ 保留原始 PSD、未经处理及清理后的源层。recipe.json 记录当前角色的测量值。
- 四张表情合成图和 expressions-review.png 是静态素材检查，不能替代动画验收。

## 图层

eye_close-l/r 是独立闭眼线。闭眼状态应隐藏对应 eyewhite、irides、eyelash 层。
mouth_close 与 14_OpenMouth 组互为状态；开放组包含 mouth_open、tooth-t、tongue、lip_upper、lip_lower。
绘制顺序已校正：脸在眉眼后、颈部在衣领填充前。左右按角色自身方向命名。
arm-l/r、hand-l/r、legwear-l/r、footwear-l/r、topwear、bottomwear 可以分别绑定。

## Cubism 精修顺序

1. 检查中性姿态与原画，建立脸、头、躯干、左右手臂及裙摆变形器。
2. 对左右眼分别制作开度 0/0.25/0.5/0.75/1；完整闭合时检查下眼缘无残影。
3. 给口腔、唇缘和牙舌制作 ParamMouthOpenY 与 ParamMouthForm 的组合关键形。
4. 给左右手臂和裙摆建立独立参数，先小幅度；袖口的隐藏接缝需按运动补画。
5. 先完成静态关键形，再增加头发、裙摆和饰物物理。当前包不含完成的身体绑定。
6. 用真实 Cubism 画布检查闭眼、张嘴、左右转头与身体倾斜的组合。

需要新增像素时，在支持 PSD 的绘画软件修改本包 PSD，再重新导入 Cubism。
不能仅凭 Cubism Core 加载通过就判定美术完成。
"""
    (out/'CUBISM_REFINEMENT.md').write_text(text)
    print(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source-psd','face-assets','recipe','output']:
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    build(a.source_psd,a.face_assets,a.recipe,a.output.resolve())


if __name__=='__main__':
    main()
