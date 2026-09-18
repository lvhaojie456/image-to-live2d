#!/usr/bin/env python3
import argparse,hashlib,json,math,os,shutil,subprocess,sys
from argparse import Namespace
from pathlib import Path
from PIL import Image,ImageDraw
from psd_tools import PSDImage
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT))
from live2d_pipeline import client,load_env,new_workspace,plan,prepare_input,remote_decompose,save_image_response
from build_refinement_package import build as build_refinement
from face_assets import build_face_assets
from body_motion import build_body_motion
from auto_expression import measure_mouth, eye_patch_rect

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def box(v,w,h):
 if not isinstance(v,(list,tuple)) or len(v)!=4:return None
 try:x,y,a,b=[float(z) for z in v]
 except: return None
 if not all(math.isfinite(z) for z in (x,y,a,b)) or min(a,b)<=0 or x<0 or y<0 or x+a>w or y+b>h:return None
 return [round(x),round(y),round(a),round(b)]
def regions(data,im,psd):
 w,h=im.size;r=data.get("regions",{});f=box(r.get("face"),w,h)
 if f is None and psd is not None:
  ls=[z for z in psd.descendants() if not z.is_group() and z.name=="face"]
  if ls:
   mw,_=psd.size;s=max(w,h);px,py=(s-w)//2,(s-h)//2;k=mw/s;x,y,R,B=ls[0].bbox;f=box([x/k-px,y/k-py,(R-x)/k,(B-y)/k],w,h)
 f=f or [round(w*.32),round(h*.04),round(w*.36),round(h*.24)];x,y,a,b=f
 def one(n,d):return box(r.get(n),w,h) or [round(x+a*d[0]),round(y+b*d[1]),round(a*d[2]),round(b*d[3])]
 return {"face":f,"left_eye":one("left_eye",(.18,.38,.25,.16)),"right_eye":one("right_eye",(.57,.38,.25,.16)),"mouth":one("mouth",(.38,.67,.24,.14))}
def face_crop(im,f,p=.45):
 x,y,w,h=f;s=round(max(w,h)*(1+2*p));cx,cy=x+w/2,y+h/2;L,T=round(cx-s/2),round(cy-s/2);o=Image.new("RGB",(s,s),"white");x1,y1=max(0,L),max(0,T);x2,y2=min(im.width,L+s),min(im.height,T+s)
 if x2>x1 and y2>y1:o.paste(im.convert("RGB").crop((x1,y1,x2,y2)),(x1-L,y1-T))
 return o,(L,T,s,s)
def edit_face(api,model,crop,origin,rs,out,kind,strict=False):
 _,_,s,_=origin; keys=("mouth",) if kind=="mouth" else ("left_eye","right_eye");m=Image.new("RGBA",crop.size,(255,255,255,255));d=ImageDraw.Draw(m)
 for key in keys:
  x,y,w,h=rs[key];d.rectangle((max(0,round(x-origin[0]-w*.35)),max(0,round(y-origin[1]-h*.45)),min(s,round(x-origin[0]+w*1.35)),min(s,round(y-origin[1]+h*1.45))),fill=(0,0,0,0))
 ip=out/f"face_input_{kind}.png";mp=out/f"face_mask_{kind}.png";crop.save(ip);m.save(mp)
 prompt="Edit only the transparent mask. Preserve the reference's exact age, identity, realism or drawing style, facial hair, skin texture, wrinkles, eyebrows, lighting and every unmasked pixel. "+("Close both eyes naturally. Keep existing eyebrows and wrinkles. Do not add long eyelashes or makeup." if kind=="eyes" else "Open the mouth naturally for speech, with a dark cavity, lips, subtle tongue and small visible upper teeth. Keep the moustache and beard. Do not change the art style or draw a rectangular boundary.")
 if strict: prompt+=" The previous attempt was rejected: "+("the eyes were not fully closed; both eyelids must be completely shut with no visible iris or sclera." if kind=="eyes" else "the mouth was not clearly open; show a clearly open mouth with a dark cavity and visible upper teeth.")
 with ip.open("rb") as i,mp.open("rb") as mask:
  r=api.images.edit(model=model,image=i,mask=mask,prompt=prompt,background="opaque",input_fidelity="high",quality="high",output_format="png",response_format="b64_json",size="1024x1024")
 p=out/f"expression_{kind}.png";save_image_response(r,p);return p
def make_recipe(source,decomp,rs,eyes,mouth,out):
 psd=PSDImage.open(decomp)
 with Image.open(source) as source_image:
  source_image=source_image.convert("RGB")
  rw,rh=source_image.size
  _,crop=face_crop(source_image,rs["face"])
 mw,mh=psd.size
 ew,eh=Image.open(eyes).size;ow,oh=Image.open(mouth).size
 def rel(b,sw,sh):
  x,y,w,h=b;return [round((x-crop[0])*sw/crop[2]),round((y-crop[1])*sh/crop[3]),round(w*sw/crop[2]),round(h*sh/crop[3])]
 le,re,mo=rel(rs["left_eye"],ew,eh),rel(rs["right_eye"],ew,eh),rel(rs["mouth"],ow,oh)
 leaves=[l for l in psd.descendants() if not l.is_group()];names={l.name for l in leaves};body={}
 for n in ("legwear","footwear"):
  if n in names:body[n]=mw//2
 hands={}
 # Separate per-side layers split at their own cuff.
 for n in ("handwear-l","handwear-r"):
  if n in names:
   x,y,R,B=next(l for l in leaves if l.name==n).bbox;hands[n]=[[x,y+round((B-y)*.78)],[R,y+round((B-y)*.72)]]
 # A single handwear layer (clasped or hidden hands) is split down the canvas midline first,
 # then each half at the same cuff ratio, so arms and hands still become separate layers.
 unified=next((l for l in leaves if l.name=='handwear'),None)
 if unified is not None and 'handwear-l' not in names and 'handwear-r' not in names:
  body['handwear']=mw//2
  x,y,R,B=unified.bbox
  cuff=y+round((B-y)*.78);free=y+round((B-y)*.72)
  hands['handwear-l']=[[mw//2,y+round((B-y)*.78)],[R,free]]
  hands['handwear-r']=[[x,cuff],[mw//2,free]]
 recipe={"schema_version":1,"reference_sha256":sha(source),"source_psd_sha256":sha(decomp),"edit_sha256":{"mouth":sha(mouth),"eyes":sha(eyes)},"reference_size":[rw,rh],"model_canvas":[mw,mh],"edit_size":[ew,eh],"edit_crop":list(crop),"closed_eyes":{"eye_close-r":{"rect":[le[0],le[1],le[0]+le[2],le[1]+le[3]],"thresholds":{"dark":100,"light":155}},"eye_close-l":{"rect":[re[0],re[1],re[0]+re[2],re[1]+re[3]],"thresholds":{"dark":100,"light":155}}},"body_splits":body,"hand_splits":hands,"manual_targets":{"ParamArmL":["arm-l","hand-l"],"ParamArmR":["arm-r","hand-r"],"ParamSkirtSwing":["bottomwear"],"ParamBodyAngleX":["topwear","bottomwear","neck"],"ParamLegL":["legwear-l","footwear-l"],"ParamLegR":["legwear-r","footwear-r"]}}
 # Detect actual mouth pixels. Coarse rectangles must never become visible skin blocks.
 recipe['mouth']=measure_mouth(mouth,mo)
 for suffix in ('r','l'):
  eye=next((l for l in leaves if l.name=='eyelash-'+suffix),None)
  if eye is not None:
   recipe['closed_eyes']['eye_close-'+suffix]={'rect':eye_patch_rect(
       eye.bbox,[rw,rh],crop,[ew,eh],[mw,mh]),'method':'texture_patch','feather':5}
 # Missing semantic mouth layers are common for moustaches. Keep textured faces
 # intact and isolate the neutral mouth from the source pixels in the package.
 recipe['face_repair']={'mode':'preserve_texture','layer':'face'}
 if 'mouth' not in names:
  points=recipe['mouth']['outline'];xs=[p[0] for p in points];ys=[p[1] for p in points]
  side=max(rw,rh);px,py=(side-rw)//2,(side-rh)//2
  recipe['closed_mouth_region']=[
      round((min(xs)*crop[2]/ow+crop[0]+px)*mw/side)-2,
      round((min(ys)*crop[3]/oh+crop[1]+py)*mh/side)-2,
      round((max(xs)*crop[2]/ow+crop[0]+px)*mw/side)+3,
      round((max(ys)*crop[3]/oh+crop[1]+py)*mh/side)+3]
 path=out/"character-refinement-recipe.json";path.write_text(json.dumps(recipe,ensure_ascii=False,indent=2));return path

def motion_recipe(package,scale=1.0):
 """Procedural motion recipe. `scale` shrinks every amplitude together (lean, breath, arms, garment);
 the verifier's feet and triangle checks decide whether a smaller scale is needed."""
 if not 0.1<=scale<=1.0: raise ValueError('Motion scale must be within 0.1..1')
 manifest=json.loads((package/"authoring-manifest.json").read_text());names={x["name"]:x for x in manifest["layers"]}
 def bbox(name):
  raw=names.get(name,{}).get("bbox") or [0,0,manifest["width"],manifest["height"]]
  x1,y1,x2,y2=raw
  return {"x1":x1,"y1":y1,"x2":x2,"y2":y2,
          "width":max(1,x2-x1),"height":max(1,y2-y1)}
 garment_name='bottomwear' if 'bottomwear' in names else 'topwear'
 top,skirt=bbox('topwear'),bbox(garment_name)
 center=(top['x1']+top['x2'])/2
 leg_boxes=[bbox(name) for name in ("legwear-l","legwear-r") if name in names]
 leg_bottom=max((b["y2"] for b in leg_boxes),default=manifest["height"]-40)
 # Rotate around the hip and fade the lean out through the thighs so the feet stay grounded.
 hip_y=round(skirt['y1']+skirt['height']*(.55 if garment_name=='bottomwear' else .85))
 lean_full_y=skirt['y1'] if garment_name=='bottomwear' else round(top['y1']+top['height']*.70)
 lean_pin_y=round(skirt["y2"]+max(120,min(280,(leg_bottom-skirt["y2"])*.4)))
 shoe_tops=[bbox(name)['y1'] for name in ('footwear-l','footwear-r') if name in names]
 if shoe_tops:
  lean_pin_y=min(lean_pin_y,round(min(shoe_tops)-manifest['height']*.15))
 lean_pin_y=min(manifest["height"]-40,max(skirt["y1"]+1,lean_pin_y))
 def arm(side):
  b=bbox("arm-"+side)
  return {"pivot":[round((b["x1"]+b["x2"])/2),round(b["y1"]+10)],"pin_y":b["y1"]+5,"free_y":max(b["y1"]+10,b["y2"]-20),"degrees":round(3.0*scale,3)}
 return {"schema_version":1,"motion_scale":scale,"model_canvas":[manifest["width"],manifest["height"]],"body":{"pivot":[round(center),hip_y],"lean_degrees":round(3.0*scale,3),"lean_full_y":lean_full_y,"lean_pin_y":lean_pin_y,"breath_lift_px":round(4.0*scale,3),"chest_expansion":round(.009*scale,5),"chest_y":round(top["y1"]+top["height"]*.38),"chest_radius":max(40,round(top["height"]*.38)),"shoulder_y":top["y1"],"breath_pin_y":top["y2"]},"arms":{"l":arm("l"),"r":arm("r")},"skirt":{"target_layers":[garment_name],"display_name":"裙摆晃动" if garment_name=='bottomwear' else '衣摆轻摆',"pin_y":skirt["y1"]+10 if garment_name=='bottomwear' else round(top['y1']+top['height']*.65),"hem_y":skirt["y2"],"sway_px":round((10 if garment_name=='bottomwear' else 4)*scale,3),"hem_lift_px":round((2 if garment_name=='bottomwear' else 1)*scale,3),"half_width":max(20,round(skirt["width"]/2))},"loop_seconds":8,"fps":30}

def write_report(workspace,args,stages):
 data={"pipeline":"prompt_or_image_to_cubism","status":"complete","input_mode":"prompt" if args.prompt else "image","requested_prompt":args.prompt,"stages":stages,"output":str(workspace),"limitations":["Cubism 5.3 still needs artist review of keyforms and physics.","A single image cannot reveal all hidden side/back pixels."]}
 (workspace/"build.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))
 (workspace/"BUILD.md").write_text("# 图生 Live2D 完整构建结果\n\n本目录由 python live2d_pipeline.py build 生成，包含原图、Astra 规划、See-through 分层 PSD、表情素材、Cubism 精修 PSD、程序化身体动作模型和验证报告。\n\nbody-motion/model/MotionCharacter.cmo3 是可在 Cubism 5.3 中继续编辑的工程起点；model3.json 已包含 BodyIdle、Breathing、BodyLean、Arms 和 Skirt 动作。\n\n自动阶段已经完成：输入、生图、规划、拆层、透明层整理、表情素材、身体动作关键形、motion3 曲线、官方 Core 结构检查和动作采样。手工阶段包括连续表情关键形、隐藏区域绘画、手臂/裙摆极值修形、头发与裙摆物理的最终手感，以及 VTube Studio 面捕验收。\n",encoding="utf-8")

def verification_failure(motion):
 """Classify a failed verification: tunable when only feet drift or flipped triangles failed."""
 path=motion/'verification/sequence-checks.json'
 if not path.is_file(): return None
 try: report=json.loads(path.read_text())
 except ValueError: return None
 poses=report.get('poses') or []
 flips=sum(int(p.get('trianglesFlippedFromNeutral',0)) for p in poses)
 degenerate=sum(int(p.get('degenerateTriangles',0)) for p in poses)
 finite=all(p.get('finite',False) for p in poses)
 feet=float(report.get('feetMaxDisplacementPixels',0))
 metrics={'feetMaxDisplacementPixels':round(feet,4),'trianglesFlipped':flips,'degenerateTriangles':degenerate,'finite':finite,'poseCount':len(poses)}
 metrics['tunable']=bool(poses) and finite and degenerate==0 and (feet>=0.25 or flips>0)
 return metrics

MOTION_SCALES=(1.0,0.66,0.33)

def run(args):
 load_env()
 from foreground import neutralize_background, clip_background
 from supervisor import Supervisor
 if bool(args.prompt)==bool(args.image):
  raise ValueError('Provide exactly one of --prompt or --image')
 workspace=Path(args.output).expanduser().resolve() if getattr(args,'output',None) else new_workspace(args.name or args.prompt or Path(args.image).stem)
 if workspace.exists() and any(workspace.iterdir()):
  raise ValueError('Choose a new empty output directory')
 workspace.mkdir(parents=True,exist_ok=True)
 def progress(stage,value):
  print(f'LIVE2D_STAGE {stage} {value}',flush=True)
  if os.environ.get('LIVE2D_PROGRESS_FILE'):
   path=Path(os.environ['LIVE2D_PROGRESS_FILE'])
   temporary=path.with_suffix('.tmp')
   temporary.write_text(json.dumps({'stage':stage,'progress':value}))
   temporary.replace(path)
 stages={}
 api=None
 def get_api():
  nonlocal api
  if api is None:
   api=client()
  return api
 supervisor=Supervisor(workspace,state_path=getattr(args,'supervisor_state',None))
 stages['supervisor']=dict(mode=supervisor.mode,model=supervisor.model)
 current={'stage':'preparing'}
 def stage(name,value):
  current['stage']=name
  progress(name,value)
 try:
  if args.prompt:
   stage('generating',5)
   model=os.environ.get("IMAGE_MODEL","gpt-image-2.5-sunburst")
   prompt=args.prompt+"\n正面完整角色立绘，干净背景，四肢不裁切，适合 Live2D 拆层。"
   background=getattr(args,'background',None) or os.environ.get('IMAGE_BACKGROUND','transparent')
   source=workspace/"01_generated.png"
   result=None
   if background=='transparent':
    try: result=get_api().images.generate(model=model,prompt=prompt,size=args.size,n=1,background="transparent",output_format="png")
    except Exception as error:
     # Providers without transparent output answer 4xx; a plain request is still worth the cost.
     from openai import APIStatusError
     if not isinstance(error,APIStatusError) or error.status_code>=500: raise
     print('透明背景不可用（'+type(error).__name__+'），改用普通生成',flush=True);background='opaque'
   if result is None: result=get_api().images.generate(model=model,prompt=prompt,size=args.size,n=1)
   save_image_response(result,source)
   stages["image_generation"]=dict(model=model,file=source.name,background=background)
  else:
   source_path=Path(args.image).expanduser().resolve()
   if not source_path.is_file():
    raise FileNotFoundError(f"找不到输入图片：{source_path}")
   source=workspace/("01_reference"+source_path.suffix.lower())
   shutil.copy2(source_path,source)
   stages["input_copy"]=dict(file=source.name)

  # The file name is a checkpoint marker for the worker; the background is now grey, not white.
  input_white=workspace/"01_input_white.png"
  foreground_dir=workspace/"foreground"
  input_mask=foreground_dir/"input_mask.png"
  stages["input_background"]=neutralize_background(source,input_white,mask_output=input_mask)
  stage('planning',15)
  if args.reuse_plan:
   plan_source=Path(args.reuse_plan).expanduser().resolve()
   if not plan_source.is_file():
    raise FileNotFoundError(f"找不到复用的 Astra 计划：{plan_source}")
   shutil.copy2(plan_source,workspace/"layer_plan.json")
   stages["astra_plan"]=dict(file="layer_plan.json",reused=True,source=str(plan_source))
  else:
   plan(Namespace(image=str(input_white),output=str(workspace)))
   stages["astra_plan"]=dict(file="layer_plan.json")
  layer_plan=json.loads((workspace/"layer_plan.json").read_text())
  with Image.open(input_white) as input_image:
   input_rgb=input_image.convert("RGB")
  gate=supervisor.review_regions(input_white,regions(layer_plan,input_rgb,None))
  if gate:
   stages["astra_plan"]["gate"]=gate
   if gate['regions'] and supervisor.mode=='act':
    layer_plan.setdefault("regions",{}).update(gate['regions'])
    layer_plan["regions_corrected_by"]="supervisor"
    (workspace/"layer_plan.json").write_text(json.dumps(layer_plan,ensure_ascii=False,indent=2))

  stage('decomposing',25)
  decomp_dir=workspace/"decomposition"
  if args.reuse_decomposition:
   decomposition=Path(args.reuse_decomposition).expanduser().resolve()
   if not decomposition.is_file():
    raise FileNotFoundError(f"找不到复用的拆层 PSD：{decomposition}")
   decomp_dir.mkdir()
   copied=decomp_dir/decomposition.name
   shutil.copy2(decomposition,copied)
   if (decomposition.parent/"input").is_dir():
    shutil.copytree(decomposition.parent/"input",decomp_dir/"input")
   decomposition=copied
  else:
   remote_decompose(Namespace(image=str(input_white),output=str(decomp_dir),group_offload=args.group_offload,resolution=args.resolution,new_run=True))
   psds=sorted(decomp_dir.glob("*.psd"),key=lambda p:("depth" in p.name,p.name))
   decomposition=next(p for p in psds if "depth" not in p.name)
  stages["see_through"]=dict(psd=str(decomposition.relative_to(workspace)))
  check=clip_background(decomposition,decomp_dir/"input_clipped.psd",mask_path=input_mask,report_dir=foreground_dir)
  stages["foreground"]=dict(leaking=check['leaking'],mask_source=check['mask_source'],clipped=check['clipped'])
  if check['leaking']:
   decomposition=decomp_dir/"input_clipped.psd"
   stages["see_through"]["clipped_psd"]=str(decomposition.relative_to(workspace))
   print('已裁掉并入图层的背景：'+', '.join(check['leaking']),flush=True)
  sheet=None
  try:
   from inspect_psd import inspect
   inspect(decomposition,foreground_dir/"layers")
   sheet=foreground_dir/"layers/layers_contact_sheet.jpg"
  except Exception as error:
   stages["foreground"]["sheet_error"]=type(error).__name__
  if sheet and sheet.is_file():
   from foreground import check_layers
   after=check_layers(decomposition) if check['clipped'] else check
   gate=supervisor.review_layers(sheet,dict(after,clipped=check['clipped']))
   if gate: stages["foreground"]["gate"]=gate

  with Image.open(input_white) as input_image:
   input_rgb=input_image.convert("RGB")
   psd=PSDImage.open(decomposition)
   rs=regions(json.loads((workspace/"layer_plan.json").read_text()),input_rgb,psd)
   crop,crop_box=face_crop(input_rgb,rs["face"])
  expr=workspace/"expressions"
  expr.mkdir()
  stage('expressions',55)
  model=os.environ.get("IMAGE_MODEL","gpt-image-2.5-sunburst")
  if args.reuse_expressions:
   expression_dir=Path(args.reuse_expressions).expanduser().resolve()
   eyes_source=expression_dir/"expression_eyes.png"
   mouth_source=expression_dir/"expression_mouth.png"
   for candidate in (eyes_source,mouth_source):
    if not candidate.is_file():
     raise FileNotFoundError(f"找不到复用的表情素材：{candidate}")
   eyes=expr/eyes_source.name
   mouth=expr/mouth_source.name
   shutil.copy2(eyes_source,eyes)
   shutil.copy2(mouth_source,mouth)
   stages["expression_assets"]=dict(reused=True,source=str(expression_dir))
  else:
   eyes=edit_face(get_api(),model,crop,crop_box,rs,expr,"eyes")
   mouth=edit_face(get_api(),model,crop,crop_box,rs,expr,"mouth")
   stages["expression_assets"]=dict(eyes=str(eyes.relative_to(workspace)),mouth=str(mouth.relative_to(workspace)))
   gate=supervisor.review_expressions(crop,eyes,mouth)
   if gate:
    stages["expression_assets"]["gate"]=gate
    if not gate['ok'] and supervisor.mode=='act' and supervisor.consume('redo_expressions','gate rejected'):
     redo=workspace/"expressions.rejected"
     expr.rename(redo);expr.mkdir()
     eyes=edit_face(get_api(),model,crop,crop_box,rs,expr,"eyes",strict=True)
     mouth=edit_face(get_api(),model,crop,crop_box,rs,expr,"mouth",strict=True)
     stages["expression_assets"].update(redone=True,eyes=str(eyes.relative_to(workspace)),mouth=str(mouth.relative_to(workspace)))
     second=supervisor.review_expressions(crop,eyes,mouth)
     if second: stages["expression_assets"]["gate_after_redo"]=second

  stage('refining',70)
  try:
   recipe=make_recipe(input_white,decomposition,rs,eyes,mouth,workspace)
  except ValueError as error:
   if 'search boundary' not in str(error) and 'mouth' not in str(error).lower(): raise
   located=supervisor.locate_mouth(Image.open(mouth).convert('RGB'))
   if not located: raise
   located_input=[round(located[0]*crop_box[2]/Image.open(mouth).width+crop_box[0]),round(located[1]*crop_box[3]/Image.open(mouth).height+crop_box[1]),
                  round(located[2]*crop_box[2]/Image.open(mouth).width),round(located[3]*crop_box[3]/Image.open(mouth).height)]
   rs=dict(rs,mouth=located_input)
   stages["mouth_relocated"]=dict(by="supervisor",mouth=located_input)
   recipe=make_recipe(input_white,decomposition,rs,eyes,mouth,workspace)
  stages["expression_assets"].update(recipe=str(recipe.relative_to(workspace)))
  assets=workspace/"face-assets"
  build_face_assets(input_white,mouth,eyes,recipe,assets)
  refinement=workspace/"cubism-ready"
  build_refinement(decomposition,assets,recipe,refinement)
  stages["cubism_refinement"]=dict(path=str(refinement.relative_to(workspace)))
  java=Path(os.environ.get("LIVE2D_JAVA_HOME",ROOT/"work/jdk-21.0.12.1+1/Contents/Home"))
  kit=Path(os.environ.get('LIVE2D_KIT_DIR',ROOT/'work/third_party/live2d-agent-kit'))
  engine=Path(os.environ.get('LIVE2D_ENGINE_DIR',ROOT/'work/third_party/psd2live'))
  core=Path(os.environ.get('CUBISM_CORE_DIR','/Applications/Live2D Cubism 5.3/res'))
  motion=workspace/"body-motion"
  attempts=[]
  for index,scale in enumerate(MOTION_SCALES):
   stage('rigging',80)
   mr=workspace/"body-motion-recipe.json"
   mr.write_text(json.dumps(motion_recipe(refinement,scale),ensure_ascii=False,indent=2))
   build_body_motion(refinement,motion,mr,kit,engine,java,core)
   stage('verifying',90)
   try:
    subprocess.run([sys.executable,str(ROOT/"scripts/verify_body_motion.py"),"--output",str(motion),"--java-home",str(java),'--kit',str(kit),'--core',str(core)],check=True)
    attempts.append(dict(scale=scale,passed=True))
    break
   except subprocess.CalledProcessError as error:
    metrics=verification_failure(motion)
    attempts.append(dict(scale=scale,passed=False,metrics=metrics))
    next_scale=MOTION_SCALES[index+1] if index+1<len(MOTION_SCALES) else None
    if not (metrics and metrics['tunable'] and next_scale is not None and supervisor.consume('tune_motion',f'scale {scale} -> {next_scale}')):
     raise
    motion.rename(workspace/f"body-motion.failed-scale{int(scale*100):03d}")
    print(f'动作检查未通过（脚底位移 {metrics["feetMaxDisplacementPixels"]} px，翻转 {metrics["trianglesFlipped"]}），幅度降到 {next_scale} 重新绑定',flush=True)
  stages["body_motion"]=dict(path=str(motion.relative_to(workspace)),verification="passed",attempts=attempts,motion_scale=attempts[-1]['scale'])
  sheets=[('expressions',refinement/'expressions-review.png'),('poses',motion/'verification/body-poses.jpg')]
  review=supervisor.visual_review([(label,path) for label,path in sheets if path.is_file()])
  if review: stages["visual_review"]=review
  write_report(workspace,args,stages)
  print("完整项目已生成："+str(workspace),flush=True)
  return workspace
 except BaseException as error:
  if isinstance(error,KeyboardInterrupt): raise
  try: handle_failure(supervisor,current['stage'],error,workspace,stages)
  except Exception as secondary: print('监督诊断本身失败（'+type(secondary).__name__+'），按原错误上报',flush=True)
  raise

def handle_failure(supervisor,stage_name,error,workspace,stages):
 """Rule-first diagnosis; the model only refines the code and the user-facing sentence."""
 metrics={}
 foreground=stages.get('foreground') or {}
 body=stages.get('body_motion') or {}
 if stage_name in ('rigging','verifying'):
  found=verification_failure(workspace/'body-motion')
  if found: metrics.update(found)
 if foreground.get('leaking'): metrics['leaking_layers']=foreground['leaking']
 packet=supervisor.failure_packet(stage_name,error,metrics)
 transport=type(error).__name__ in {'APIConnectionError','APITimeoutError','InternalServerError','RemoteProtocolError','TransportError','ConnectError','ReadTimeout','CalledProcessError'} and stage_name in ('generating','planning','decomposing','expressions')
 if transport: code='provider_unavailable'
 elif stage_name in ('rigging','verifying'): code='background_leak' if foreground.get('leaking') else 'rig_unstable'
 elif stage_name in ('refining','expressions'): code='expression_failed'
 else: code='provider_unavailable'
 if not any(v>0 for k,v in supervisor.budget_left().items() if k!='model_calls'): code='budget_exhausted'
 suggestion=None;summary=None;action=None
 images=[]
 for label,path in (('neutral',workspace/'cubism-ready/neutral.png'),('layers',workspace/'foreground/layers/layers_contact_sheet.jpg')):
  if path.is_file(): images.append((label,path))
 decision=supervisor.diagnose(packet,images) if not transport else None
 if decision:
  action=decision['action']
  if decision['confidence']>=0.6:
   code=decision['diagnosis_code'];summary=decision['explanation']
  if action=='regenerate_image': suggestion='regenerate_image'
  elif action in ('retry_stage','replan_with_hint','tune_motion','clip_background','redo_expressions'): suggestion='retry'
  elif action=='give_up': suggestion=None
  if action=='replan_with_hint' and supervisor.mode=='act' and supervisor.consume('replan_with_hint','next retry replans'):
   # Drop this attempt's plan checkpoint so the worker's next retry calls Astra again.
   (workspace/'layer_plan.json').unlink(missing_ok=True)
 supervisor.write_diagnosis(code,suggestion,summary,action,packet)

def main():
 p=argparse.ArgumentParser(description=__doc__)
 g=p.add_mutually_exclusive_group(required=True)
 g.add_argument("--prompt")
 g.add_argument("--image")
 p.add_argument("--name")
 p.add_argument("--output",type=Path)
 p.add_argument("--size",default="1024x1536")
 p.add_argument("--resolution",type=int,default=1280)
 p.add_argument("--group-offload",action="store_true")
 p.add_argument("--reuse-decomposition")
 p.add_argument("--background",choices=["transparent","opaque"])
 p.add_argument("--supervisor-state")
 p.add_argument("--reuse-plan",help="复用同一输入图的 layer_plan.json，跳过 Astra 请求")
 p.add_argument("--reuse-expressions",help="复用同一输入图目录中的 expression_eyes.png 和 expression_mouth.png")
 run(p.parse_args())
if __name__=="__main__":main()
