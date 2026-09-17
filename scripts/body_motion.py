#!/usr/bin/env python3
"""Add editable breathing, body lean, arm sway and skirt keyforms to a refinement package."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ENGINE_COMMIT = '5526f2e16b57e5f83d34f33730d6fa26d8bc8695'
BODY_PARAMETERS = ('ParamBreath','ParamBodyAngleZ','ParamArmLSwing','ParamArmRSwing','ParamSkirtSwing')
EXPRESSION_PARAMETERS = ('ParamEyeLOpen','ParamEyeROpen','ParamMouthOpenY','ParamMouthForm')
PARAMETERS = BODY_PARAMETERS + EXPRESSION_PARAMETERS


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_recipe(data):
    def numeric(value, low, high, label):
        if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{label} must be finite and in {low}..{high}')
    numeric(data['loop_seconds'],1,30,'loop_seconds')
    numeric(data['fps'],6,60,'fps')
    if not isinstance(data['fps'],int):
        raise ValueError('fps must be an integer')
    if not math.isclose(data['loop_seconds']*data['fps'], round(data['loop_seconds']*data['fps'])):
        raise ValueError('Loop duration must cover an integer number of frames')
    for k in ['model_canvas']:
        if len(data[k])!=2 or any(not isinstance(v,int) or v<1 or v>16384 for v in data[k]):
            raise ValueError('Invalid canvas')
    width,height=data['model_canvas']
    body=data['body'];arms=data['arms'];skirt=data['skirt']
    for spec in [body,*arms.values()]:
        x,y=spec['pivot'];numeric(x,0,width,'pivot x');numeric(y,0,height,'pivot y')
    for k in ['lean_full_y','lean_pin_y','breath_pin_y','shoulder_y','chest_y']:
        numeric(body[k],0,height,k)
    if not body['lean_full_y']<body['lean_pin_y'] or not body['shoulder_y']<body['breath_pin_y']:
        raise ValueError('Body falloff endpoints must be ordered')
    numeric(body['lean_degrees'],0.1,6,'lean_degrees')
    numeric(body['breath_lift_px'],0.1,12,'breath_lift_px')
    numeric(body['chest_expansion'],0,0.03,'chest_expansion')
    numeric(body['chest_radius'],1,height,'chest_radius')
    for side in ['l','r']:
        arm=arms[side]
        numeric(arm['pin_y'],0,height,'arm pin')
        numeric(arm['free_y'],0,height,'arm free')
        numeric(arm['degrees'],0.1,6,'arm degrees')
        if not arm['pin_y']<arm['free_y']:
            raise ValueError('Arm falloff endpoints must be ordered')
    for k in ['pin_y','hem_y']:
        numeric(skirt[k],0,height,k)
    if not skirt['pin_y']<skirt['hem_y']:
        raise ValueError('Skirt hem must be below the pinned waist')
    numeric(skirt['sway_px'],0.1,20,'skirt sway')
    numeric(skirt['hem_lift_px'],0,6,'skirt hem lift')
    numeric(skirt['half_width'],1,width,'skirt width')


def values_at(t, duration):
    phase=2*math.pi*t/duration
    blink = max(0.0, max((1+math.cos(2*math.pi*(t/duration-0.20)/0.045))/2 if abs((t/duration-0.20+0.5)%1-0.5)<0.045 else 0,
                          (1+math.cos(2*math.pi*(t/duration-0.70)/0.045))/2 if abs((t/duration-0.70+0.5)%1-0.5)<0.045 else 0))
    mouth_phase=max(0.0, math.sin(phase*2))
    return {'ParamBreath':(1-math.cos(phase*2))/2,
            'ParamBodyAngleZ':8*math.sin(phase),
            'ParamArmLSwing':0.72*math.sin(phase*2),
            'ParamArmRSwing':-0.62*math.sin(phase*2),
            'ParamSkirtSwing':0.75*math.sin(phase-0.45),
            'ParamEyeLOpen':1-blink,
            'ParamEyeROpen':1-blink,
            'ParamMouthOpenY':0.82*mouth_phase*mouth_phase,
            'ParamMouthForm':0.30*math.sin(phase)}


def motion_document(duration, fps, selected):
    frames=round(duration*fps)
    curves=[]
    for parameter in selected:
        # Explicit equal endpoints avoid a tiny numerical discontinuity at a loop boundary.
        initial=round(values_at(0,duration)[parameter],7)
        segments=[0,initial]
        for i in range(1,frames+1):
            v=initial if i==frames else values_at(i/fps,duration)[parameter]
            segments.extend([0,i/fps,round(v,7)])
        curves.append({'Target':'Parameter','Id':parameter,'Segments':segments})
    return {'Version':3,'Meta':{'Duration':duration,'Fps':fps,'Loop':True,
            'AreBeziersRestricted':True,'CurveCount':len(curves),
            'TotalSegmentCount':frames*len(curves),'TotalPointCount':(frames+1)*len(curves),
            'UserDataCount':0,'TotalUserDataSize':0},'Curves':curves,'UserData':[]}


def check_package(package, manifest):
    if manifest.get('stage')!='materials_only_no_keyforms':
        raise ValueError('Expected a prepared Cubism authoring package')
    names={l['name'] for l in manifest['layers']}
    required={'arm-l','arm-r','hand-l','hand-r','topwear','face','mouth_close',
              'mouth_open','tooth-t','tongue','lip_upper','lip_lower','eye_close-l','eye_close-r',
              'legwear-l','legwear-r'}
    if not required<=names or len(names)!=len(manifest['layers']):
        raise ValueError('Incomplete or duplicate authoring layers: '+str(sorted(required-names)))
    for l in manifest['layers']:
        path=(package/l['path']).resolve()
        if package not in path.parents or not path.is_file():
            raise ValueError('Layer path is outside the source package')
        with Image.open(path) as image:
            if image.size!=(manifest['width'],manifest['height']) or image.mode!='RGBA' or not image.getchannel('A').getbbox():
                raise ValueError('Layer must be a nonempty RGBA image on the full canvas: '+l['name'])


def install_motion_files(output, name, recipe):
    model=output/'model'
    settings_path=model/(name+'.model3.json')
    settings=json.loads(settings_path.read_text())
    groups={'BodyIdle':PARAMETERS,'Expressions':EXPRESSION_PARAMETERS,'Breathing':('ParamBreath',),
            'BodyLean':('ParamBodyAngleZ',),'Arms':('ParamArmLSwing','ParamArmRSwing'),
            'Skirt':('ParamSkirtSwing',)}
    for group,parameters in groups.items():
        filename=f'{name}.{group}.motion3.json'
        (model/filename).write_text(json.dumps(motion_document(recipe['loop_seconds'],recipe['fps'],parameters),indent=2))
        settings['FileReferences'].setdefault('Motions',{})[group]=[{'File':filename}]
    groups_json=settings.setdefault('Groups',[])
    if not any(g.get('Name')=='BodyMotion' for g in groups_json):
        groups_json.append({'Target':'Parameter','Name':'BodyMotion','Ids':list(BODY_PARAMETERS)})
    if not any(g.get('Name')=='ExpressionMotion' for g in groups_json):
        groups_json.append({'Target':'Parameter','Name':'ExpressionMotion','Ids':list(EXPRESSION_PARAMETERS)})
    # Standard Idle is the complete body loop. Its curves do not also drive the hair physics outputs.
    settings['FileReferences']['Motions']['Idle']=settings['FileReferences']['Motions']['BodyIdle']
    settings_path.write_text(json.dumps(settings,indent=2))


def write_motion_readme(output):
    recipe=json.loads((output/'motion-recipe.json').read_text())
    report=json.loads((output/'core-report.json').read_text())
    sequence_path=output/'verification/sequence-checks.json'
    garment=recipe['skirt'].get('display_name','裙摆晃动')
    sequence_text='身体组合与动作采样尚未执行。运行 body-motion 时加 --preview 可执行。'
    if sequence_path.exists():
        sequence=json.loads(sequence_path.read_text())
        flips=sum(p['trianglesFlippedFromNeutral'] for p in sequence['poses'])
        sequence_text=(f"{sequence['poseCount']} 个动作采样：{'通过' if sequence['passed'] else '未通过'}；"
                       f"三角形翻转 {flips}；脚底最大位移 {sequence['feetMaxDisplacementPixels']:.6f} 像素。")
    (output/'BODY_MOTION.md').write_text(f'''# 可编辑身体动作

打开 `model/MotionCharacter.cmo3` 可以在 Cubism 5.3 继续精修。

| 参数 | 范围 | 目标 |
| --- | --- | --- |
| ParamBreath | 0..1 | 呼吸 |
| ParamBodyAngleZ | -10..10 | 约 ±{recipe['body']['lean_degrees']:g}° 身体倾斜 |
| ParamArmLSwing / ParamArmRSwing | -1..1 | 左右袖子和手共同轻摆 |
| ParamSkirtSwing | -1..1 | {garment} |

BodyIdle 是 {recipe['loop_seconds']:g} 秒、{recipe['fps']} fps 的待机循环。
Breathing、BodyLean、Arms、Skirt 分别是单项动作；Skirt 为兼容既有模型沿用的文件名，本角色实际目标为 {', '.join(recipe['skirt'].get('target_layers',['bottomwear']))}。

官方 Core {report['coreVersion']}：{'通过' if report['passed'] else '未通过'}。
{sequence_text}

诊断预览通过官方 Core 计算顶点、Java2D 近似绘制；没有执行物理模拟或面捕测试。
眼睑、嘴角、袖口接缝及衣物形变仍需要在 Cubism 中进行美术验收。
''',encoding='utf-8')


def build_body_motion(package, output, recipe_path, kit, engine, java_home, core):
    package=package.resolve();output=output.resolve();kit=kit.resolve();engine=engine.resolve()
    java_home=java_home.resolve();core=core.resolve()
    manifest=json.loads((package/'authoring-manifest.json').read_text())
    recipe=json.loads(recipe_path.read_text());validate_recipe(recipe);check_package(package,manifest)
    garment_targets=recipe['skirt'].get('target_layers',['bottomwear'])
    if not garment_targets or not set(garment_targets)<={l['name'] for l in manifest['layers']}:
        raise ValueError('Garment motion targets must be actual source layers')
    if recipe['model_canvas']!=[manifest['width'],manifest['height']]:
        raise ValueError('Motion recipe belongs to a different canvas')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose a new empty output directory')
    if not (java_home/'bin/java').is_file() or not (core/'Live2DCubismCore.jar').is_file():
        raise ValueError('JDK 21 and local Cubism Core are required')
    commit=subprocess.check_output(['git','-C',str(engine),'rev-parse','HEAD'],text=True).strip()
    if commit!=ENGINE_COMMIT:
        raise ValueError('Engine commit differs from the supported revision')
    lock=json.loads((kit/'integrations/psd2live/engine-lock.json').read_text())
    if lock['commit']!=commit:
        raise ValueError('Agent Kit engine lock differs')
    for path,expected in lock['patched_files'].items():
        if sha(engine/path)!=expected:
            raise ValueError('Engine patch identity differs: '+path)
    output.mkdir(parents=True,exist_ok=True)
    material=output/'materials';shutil.copytree(package,material)
    motion_manifest=json.loads((material/'authoring-manifest.json').read_text())
    motion_manifest.update(name='MotionCharacter',procedural_motion=recipe,
        config={'mesh_spacing':24,'preserve_source_raster':True,'source_closed_eyes':True,
                'head_strength':0.2,'head_roll_strength':0.25,'initial_head_angle_z':0,
                'body_strength':0.3,'mouth_outline':False,'cute_mouth_form':False},render_previews=False)
    (material/'motion-manifest.json').write_text(json.dumps(motion_manifest,indent=2))
    (output/'motion-recipe.json').write_text(json.dumps(recipe,indent=2))
    generated=ROOT/'work/body-motion-integration';generated.mkdir(parents=True,exist_ok=True)
    adapter=(kit/'integrations/psd2live/ManifestExport.kt').read_text()
    anchor='    println("Exporting $name: ${layers.size} layers, ${width}x$height canvas")'
    if adapter.count(anchor)!=1:
        raise ValueError('Unsupported manifest adapter; no code was changed')
    adapter=adapter.replace(anchor,'    config = withProceduralMotion(source, config, manifest.getValue("procedural_motion").jsonObject, output)\n'+anchor)
    (generated/'ManifestExport.kt').write_text(adapter)
    shutil.copy2(ROOT/'integrations/psd2live/ProceduralMotion.kt',generated/'ProceduralMotion.kt')
    env=os.environ.copy();env.update(JAVA_HOME=str(java_home),CUBISM_CORE_DIR=str(core),VALIDATOR_JAVA=str(java_home/'bin/java'))
    env['PATH']=str(java_home/'bin')+os.pathsep+str(Path(sys.executable).parent)+os.pathsep+env['PATH']
    command=['bash','./gradlew','--no-daemon','--console=plain','--init-script',str(kit/'scripts/psd2live-init.gradle'),
             'exportManifest','-PagentKitIntegrationDir='+str(generated),
             '-PcharacterManifest='+str(material/'motion-manifest.json'),'-PcharacterOutput='+str(output/'model')]
    print('Exporting editable body keyforms…',flush=True)
    with (output/'export.out').open('w') as stream:
        subprocess.run(command,cwd=engine,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    install_motion_files(output,'MotionCharacter',recipe)
    subprocess.run(['bash',str(kit/'scripts/validate_core.sh'),str(output/'model/MotionCharacter.moc3'),
                    str(output/'core-report.json')],env=env,check=True)
    with (output/'structural-report.json').open('w') as stream:
        subprocess.run([sys.executable,str(kit/'scripts/validate.py'),'--model',str(output/'model/MotionCharacter.model3.json'),
                        '--core-report',str(output/'core-report.json')],env=env,stdout=stream,check=True)
    report=json.loads((output/'core-report.json').read_text())
    by_id={x['id']:x for x in report['parameters']}
    rig=json.loads((output/'model/procedural-rig.json').read_text())
    effects={}
    for id in PARAMETERS:
        param=by_id[id]
        changed=set(param['minPose']['changedMeshIds'])|set(param['maxPose']['changedMeshIds'])
        if not changed:
            raise RuntimeError('Procedural parameter has no effect: '+id)
        expected=set(rig['targets'].get(id, []))
        if id.startswith('ParamArm') or id=='ParamSkirtSwing':
            if changed!=expected:
                raise RuntimeError('Motion affects wrong meshes: '+id)
        effects[id]={'changed_meshes':sorted(changed),'max_displacement_px':max(
            param['minPose']['maxVertexDisplacementPixels'],param['maxPose']['maxVertexDisplacementPixels'])}
    (output/'motion-checks.json').write_text(json.dumps({'passed':True,'effects':effects,
        'scope':'Native Core parameter effects and target isolation; visual QA is separate'},indent=2))
    write_motion_readme(output)
    versions={'engine_commit':commit,'kit_commit':subprocess.check_output(['git','-C',str(kit),'rev-parse','HEAD'],text=True).strip(),
              'adapter_sha256':sha(generated/'ManifestExport.kt'),'extension_sha256':sha(generated/'ProceduralMotion.kt'),
              'source_manifest_sha256':sha(package/'authoring-manifest.json')}
    (output/'source-versions.json').write_text(json.dumps(versions,indent=2))
    print('Body motion export and Core checks complete:',output,flush=True)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    add_arguments(p)
    execute(p.parse_args())


def add_arguments(p):
    p.add_argument('--package',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--recipe',type=Path,default=ROOT/'recipes/e2e-body-motion.json')
    p.add_argument('--kit',type=Path,default=ROOT/'work/third_party/live2d-agent-kit')
    p.add_argument('--engine',type=Path,default=ROOT/'work/third_party/psd2live')
    p.add_argument('--java-home',type=Path,required=True)
    p.add_argument('--core',type=Path,default=Path('/Applications/Live2D Cubism 5.3/res'))
    p.add_argument('--preview',action='store_true',help='Render and verify emitted body motion curves')


def execute(a):
    build_body_motion(a.package,a.output,a.recipe,a.kit,a.engine,a.java_home,a.core)
    if a.preview:
        if __package__:
            from .verify_body_motion import run
        else:
            from verify_body_motion import run
        run(a.output,a.kit,a.java_home,a.core)


if __name__=='__main__':
    main()
