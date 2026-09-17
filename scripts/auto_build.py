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
 if f is None:
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
def edit_face(api,model,crop,origin,rs,out,kind):
 _,_,s,_=origin; keys=("mouth",) if kind=="mouth" else ("left_eye","right_eye");m=Image.new("RGBA",crop.size,(255,255,255,255));d=ImageDraw.Draw(m)
 for key in keys:
  x,y,w,h=rs[key];d.rectangle((max(0,round(x-origin[0]-w*.35)),max(0,round(y-origin[1]-h*.45)),min(s,round(x-origin[0]+w*1.35)),min(s,round(y-origin[1]+h*1.45))),fill=(0,0,0,0))
 ip=out/f"face_input_{kind}.png";mp=out/f"face_mask_{kind}.png";crop.save(ip);m.save(mp)
 prompt="Edit only the transparent mask. Preserve the reference's exact age, identity, realism or drawing style, facial hair, skin texture, wrinkles, eyebrows, lighting and every unmasked pixel. "+("Close both eyes naturally. Keep existing eyebrows and wrinkles. Do not add long eyelashes or makeup." if kind=="eyes" else "Open the mouth naturally for speech, with a dark cavity, lips, subtle tongue and small visible upper teeth. Keep the moustache and beard. Do not change the art style or draw a rectangular boundary.")
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
 def poly(b,sw,sh,e):
  x,y,w,h=b;x-=e*w;y-=e*h;w*=1+2*e;h*=1+2*e
  return [[round((x-crop[0])*sw/crop[2]),round((y-crop[1])*sh/crop[3])],[round((x+w-crop[0])*sw/crop[2]),round((y-crop[1])*sh/crop[3])],[round((x+w-crop[0])*sw/crop[2]),round((y+h-crop[1])*sh/crop[3])],[round((x-crop[0])*sw/crop[2]),round((y+h-crop[1])*sh/crop[3])]]
 leaves=[l for l in psd.descendants() if not l.is_group()];names={l.name for l in leaves};body={}
 for n in ("legwear","footwear"):
  if n in names:body[n]=mw//2
 hands={}
 for n in ("handwear-l","handwear-r"):
  if n in names:
   x,y,R,B=next(l for l in leaves if l.name==n).bbox;hands[n]=[[x,y+round((B-y)*.78)],[R,y+round((B-y)*.72)]]
 face=next((l for l in leaves if l.name=="face"),None);samples=[]
 if face:
  x,y,R,B=face.bbox;samples=[(round(x+(R-x)*u),round(y+(B-y)*v)) for u,v in ((.25,.7),(.45,.72),(.65,.7),(.35,.85),(.55,.85))]
 recipe={"schema_version":1,"reference_sha256":sha(source),"source_psd_sha256":sha(decomp),"edit_sha256":{"mouth":sha(mouth),"eyes":sha(eyes)},"reference_size":[rw,rh],"model_canvas":[mw,mh],"edit_size":[ew,eh],"edit_crop":list(crop),"closed_eyes":{"eye_close-r":{"rect":[le[0],le[1],le[0]+le[2],le[1]+le[3]],"thresholds":{"dark":100,"light":155}},"eye_close-l":{"rect":[re[0],re[1],re[0]+re[2],re[1]+re[3]],"thresholds":{"dark":100,"light":155}}},"mouth":{"outline":poly(rs["mouth"],ow,oh,.18),"teeth":poly(rs["mouth"],ow,oh,.03),"tongue":poly(rs["mouth"],ow,oh,.10),"cavity_sample":[round(mo[0]+mo[2]*.5),round(mo[1]+mo[3]*.55)],"lip_split_y":round(mo[1]+mo[3]*.33)},"face_repair":{"layer":"face","fade_start_y":round(face.bbox[1]+(face.bbox[3]-face.bbox[1])*.5) if face else 0,"fade_end_y":round(face.bbox[1]+(face.bbox[3]-face.bbox[1])*.8) if face else 1,"sample_points":samples},"body_splits":body,"hand_splits":hands,"manual_targets":{"ParamArmL":["arm-l","hand-l"],"ParamArmR":["arm-r","hand-r"],"ParamSkirtSwing":["bottomwear"],"ParamBodyAngleX":["topwear","bottomwear","neck"],"ParamLegL":["legwear-l","footwear-l"],"ParamLegR":["legwear-r","footwear-r"]}}
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

def motion_recipe(package):
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
  return {"pivot":[round((b["x1"]+b["x2"])/2),round(b["y1"]+10)],"pin_y":b["y1"]+5,"free_y":max(b["y1"]+10,b["y2"]-20),"degrees":3.0}
 return {"schema_version":1,"model_canvas":[manifest["width"],manifest["height"]],"body":{"pivot":[round(center),hip_y],"lean_degrees":3.0,"lean_full_y":lean_full_y,"lean_pin_y":lean_pin_y,"breath_lift_px":4.0,"chest_expansion":.009,"chest_y":round(top["y1"]+top["height"]*.38),"chest_radius":max(40,round(top["height"]*.38)),"shoulder_y":top["y1"],"breath_pin_y":top["y2"]},"arms":{"l":arm("l"),"r":arm("r")},"skirt":{"target_layers":[garment_name],"display_name":"裙摆晃动" if garment_name=='bottomwear' else '衣摆轻摆',"pin_y":skirt["y1"]+10 if garment_name=='bottomwear' else round(top['y1']+top['height']*.65),"hem_y":skirt["y2"],"sway_px":10 if garment_name=='bottomwear' else 4,"hem_lift_px":2 if garment_name=='bottomwear' else 1,"half_width":max(20,round(skirt["width"]/2))},"loop_seconds":8,"fps":30}

def write_report(workspace,args,stages):
 data={"pipeline":"prompt_or_image_to_cubism","status":"complete","input_mode":"prompt" if args.prompt else "image","requested_prompt":args.prompt,"stages":stages,"output":str(workspace),"limitations":["Cubism 5.3 still needs artist review of keyforms and physics.","A single image cannot reveal all hidden side/back pixels."]}
 (workspace/"build.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))
 (workspace/"BUILD.md").write_text("# 图生 Live2D 完整构建结果\n\n本目录由 python live2d_pipeline.py build 生成，包含原图、Astra 规划、See-through 分层 PSD、表情素材、Cubism 精修 PSD、程序化身体动作模型和验证报告。\n\nbody-motion/model/MotionCharacter.cmo3 是可在 Cubism 5.3 中继续编辑的工程起点；model3.json 已包含 BodyIdle、Breathing、BodyLean、Arms 和 Skirt 动作。\n\n自动阶段已经完成：输入、生图、规划、拆层、透明层整理、表情素材、身体动作关键形、motion3 曲线、官方 Core 结构检查和动作采样。手工阶段包括连续表情关键形、隐藏区域绘画、手臂/裙摆极值修形、头发与裙摆物理的最终手感，以及 VTube Studio 面捕验收。\n",encoding="utf-8")

def run(args):
 load_env()
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

 if args.prompt:
  progress('generating',5)
  model=os.environ.get("IMAGE_MODEL","gpt-image-2.5-sunburst")
  result=get_api().images.generate(model=model,prompt=args.prompt+"\n正面完整角色立绘，干净背景，四肢不裁切，适合 Live2D 拆层。",size=args.size,n=1)
  source=workspace/"01_generated.png"
  save_image_response(result,source)
  stages["image_generation"]=dict(model=model,file=source.name)
 else:
  source_path=Path(args.image).expanduser().resolve()
  if not source_path.is_file():
   raise FileNotFoundError(f"找不到输入图片：{source_path}")
  source=workspace/("01_reference"+source_path.suffix.lower())
  shutil.copy2(source_path,source)
  stages["input_copy"]=dict(file=source.name)

 input_white=workspace/"01_input_white.png"
 prepare_input(source,input_white)
 progress('planning',15)
 if args.reuse_plan:
  plan_source=Path(args.reuse_plan).expanduser().resolve()
  if not plan_source.is_file():
   raise FileNotFoundError(f"找不到复用的 Astra 计划：{plan_source}")
  shutil.copy2(plan_source,workspace/"layer_plan.json")
  stages["astra_plan"]=dict(file="layer_plan.json",reused=True,source=str(plan_source))
 else:
  plan(Namespace(image=str(input_white),output=str(workspace)))
  stages["astra_plan"]=dict(file="layer_plan.json")

 progress('decomposing',25)
 if args.reuse_decomposition:
  decomposition=Path(args.reuse_decomposition).expanduser().resolve()
  if not decomposition.is_file():
   raise FileNotFoundError(f"找不到复用的拆层 PSD：{decomposition}")
  decomp_dir=workspace/"decomposition"
  decomp_dir.mkdir()
  copied=decomp_dir/decomposition.name
  shutil.copy2(decomposition,copied)
  decomposition=copied
 else:
  remote_decompose(Namespace(image=str(input_white),output=str(workspace/"decomposition"),group_offload=args.group_offload,resolution=args.resolution,new_run=True))
  decomp_dir=workspace/"decomposition"
  psds=sorted(decomp_dir.glob("*.psd"),key=lambda p:("depth" in p.name,p.name))
  decomposition=next(p for p in psds if "depth" not in p.name)
 stages["see_through"]=dict(psd=str(decomposition.relative_to(workspace)))

 with Image.open(input_white) as input_image:
  input_rgb=input_image.convert("RGB")
  psd=PSDImage.open(decomposition)
  rs=regions(json.loads((workspace/"layer_plan.json").read_text()),input_rgb,psd)
  crop,crop_box=face_crop(input_rgb,rs["face"])
 expr=workspace/"expressions"
 expr.mkdir()
 progress('expressions',55)
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

 progress('refining',70)
 recipe=make_recipe(input_white,decomposition,rs,eyes,mouth,workspace)
 stages["expression_assets"].update(recipe=str(recipe.relative_to(workspace)))
 assets=workspace/"face-assets"
 build_face_assets(input_white,mouth,eyes,recipe,assets)
 refinement=workspace/"cubism-ready"
 build_refinement(decomposition,assets,recipe,refinement)
 stages["cubism_refinement"]=dict(path=str(refinement.relative_to(workspace)))
 mr=workspace/"body-motion-recipe.json"
 mr.write_text(json.dumps(motion_recipe(refinement),ensure_ascii=False,indent=2))
 motion=workspace/"body-motion"
 java=Path(os.environ.get("LIVE2D_JAVA_HOME",ROOT/"work/jdk-21.0.12.1+1/Contents/Home"))
 kit=Path(os.environ.get('LIVE2D_KIT_DIR',ROOT/'work/third_party/live2d-agent-kit'))
 engine=Path(os.environ.get('LIVE2D_ENGINE_DIR',ROOT/'work/third_party/psd2live'))
 core=Path(os.environ.get('CUBISM_CORE_DIR','/Applications/Live2D Cubism 5.3/res'))
 progress('rigging',80)
 build_body_motion(refinement,motion,mr,kit,engine,java,core)
 progress('verifying',90)
 subprocess.run([sys.executable,str(ROOT/"scripts/verify_body_motion.py"),"--output",str(motion),"--java-home",str(java),'--kit',str(kit),'--core',str(core)],check=True)
 stages["body_motion"]=dict(path=str(motion.relative_to(workspace)),verification="passed")
 write_report(workspace,args,stages)
 print("完整项目已生成："+str(workspace),flush=True)
 return workspace

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
 p.add_argument("--reuse-plan",help="复用同一输入图的 layer_plan.json，跳过 Astra 请求")
 p.add_argument("--reuse-expressions",help="复用同一输入图目录中的 expression_eyes.png 和 expression_mouth.png")
 run(p.parse_args())
if __name__=="__main__":main()
