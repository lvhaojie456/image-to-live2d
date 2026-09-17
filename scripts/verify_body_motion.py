#!/usr/bin/env python3
"""Sample the emitted motion files and check exported MOC geometry and grounded feet."""
import argparse
import bisect
import itertools
import json
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw
if __package__:
    from .body_motion import ROOT, PARAMETERS, BODY_PARAMETERS, write_motion_readme
else:
    from body_motion import ROOT, PARAMETERS, BODY_PARAMETERS, write_motion_readme


def sample_motion(document, t):
    result={}
    for curve in document['Curves']:
        segment=curve['Segments']; times=[segment[0]];values=[segment[1]]
        for i in range(2,len(segment),3):
            if segment[i]!=0:
                raise ValueError('Expected linear segments in procedural motion')
            times.append(segment[i+1]);values.append(segment[i+2])
        index=max(0,min(bisect.bisect_right(times,t)-1,len(times)-2))
        blend=(t-times[index])/(times[index+1]-times[index])
        result[curve['Id']]=values[index]+(values[index+1]-values[index])*blend
    return result


def run(output, kit, java_home, core):
    output=output.resolve(); kit=kit.resolve();java_home=java_home.resolve();core=core.resolve()
    model=output/'model';out=output/'verification'
    if out.exists() and any(out.iterdir()):
        raise ValueError('Verification directory is nonempty')
    out.mkdir(parents=True,exist_ok=True)
    reference=json.loads((model/'MotionCharacter.model3.json').read_text())['FileReferences']
    if len(reference['Textures'])!=1:
        raise ValueError('This diagnostic expects a single atlas')
    motion=json.loads((model/reference['Motions']['BodyIdle'][0]['File']).read_text())
    duration=motion['Meta']['Duration']; rows=[];defaults={k:0 for k in PARAMETERS}
    defaults.update(ParamEyeLOpen=1,ParamEyeROpen=1)
    def row(name,values,render=True):
        rows.append([name,'1' if render else '0',*[f'{values.get(k,defaults[k]):.7f}' for k in PARAMETERS]])
    # Test all independent-body extreme combinations, including both breathing endpoints.
    for i,values in enumerate(itertools.product((0,1),(-10,0,10),(-1,0,1),(-1,0,1),(-1,0,1))):
        row('combo_%03d'%i,dict(zip(BODY_PARAMETERS,values)),False)
    for k in BODY_PARAMETERS:
        for sign in ([-1,1] if k!='ParamBreath' else [1]):
            row(k+('_min' if sign<0 else '_max'),{k:sign*(10 if k=='ParamBodyAngleZ' else 1)})
    fps=12;frames=round(duration*fps)
    for i in range(frames):
        row('frame_%03d'%i,sample_motion(motion,i/fps))
    # Short isolated loops make subtle breathing and each component easy to inspect.
    for group,params in [('breath',('ParamBreath',)),('lean',('ParamBodyAngleZ',)),
                          ('arms',('ParamArmLSwing','ParamArmRSwing')),('skirt',('ParamSkirtSwing',))]:
        source=json.loads((model/f'MotionCharacter.{dict(breath="Breathing",lean="BodyLean",arms="Arms",skirt="Skirt")[group]}.motion3.json').read_text())
        for i in range(24):row(f'{group}_{i:03d}',sample_motion(source,duration*i/24))
    tsv=out/'poses.tsv';tsv.write_text('\n'.join(['\t'.join(['name','render',*PARAMETERS]),*['\t'.join(r) for r in rows]])+'\n')
    classes=ROOT/'work/body-motion-verify-classes';classes.mkdir(parents=True,exist_ok=True)
    jar=core/'Live2DCubismCore.jar'
    subprocess.run([str(java_home/'bin/javac'),'-cp',str(jar),'-d',str(classes),
                    str(kit/'scripts/render_core.java'),str(kit/'scripts/validate_core.java'),
                    str(ROOT/'integrations/psd2live/BodyMotionSequence.java')],check=True)
    command=[str(java_home/'bin/java'),'-Xmx2g','-Djava.awt.headless=true','-Djava.library.path='+str(core),
             '-cp',str(classes)+':'+str(jar),'BodyMotionSequence',str(model/reference['Moc']),
             str(model/reference['Textures'][0]),str(out),str(tsv),'1024']
    with (out/'render.out').open('w') as stream:
        subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True)
    def gif(prefix,dest,ms,crop):
        frames=[]
        for path in sorted(out.glob(prefix+'*.png')):
            image=Image.open(path).convert('RGBA').crop(crop)
            image.thumbnail((420,720),Image.Resampling.LANCZOS)
            bg=Image.new('RGB',image.size,'#e7eef2');bg.paste(image,(0,0),image);frames.append(bg)
        frames[0].save(out/dest,save_all=True,append_images=frames[1:],duration=ms,loop=0,disposal=2)
    gif('frame_','body-idle.gif',round(1000/fps),(295,0,735,1024))
    for group,crop in [('breath',(370,120,660,450)),('lean',(295,0,735,1024)),
                       ('arms',(295,175,735,585)),('skirt',(365,310,680,570))]:
        gif(group+'_',group+'.gif',round(duration*1000/24),crop)
    names=['neutral.png','ParamBodyAngleZ_max.png','ParamArmLSwing_max.png','ParamSkirtSwing_max.png']
    sheet=Image.new('RGB',(4*280,670),'#e7eef2')
    for i,name in enumerate(names):
        image=Image.open(out/name).crop((290,0,740,1024));image.thumbnail((274,628))
        sheet.paste(image,(i*280+(280-image.width)//2,10),image)
        ImageDraw.Draw(sheet).text((i*280+8,647),name.replace('.png',''),fill='black')
    sheet.save(out/'body-poses.jpg',quality=95)
    write_motion_readme(output)
    print('Body geometry and motion previews verified:',out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--kit',type=Path,default=ROOT/'work/third_party/live2d-agent-kit')
    p.add_argument('--java-home',type=Path,required=True)
    p.add_argument('--core',type=Path,default=Path('/Applications/Live2D Cubism 5.3/res'))
    a=p.parse_args();run(a.output,a.kit,a.java_home,a.core)
