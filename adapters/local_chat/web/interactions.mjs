// Deterministic motion/state layer. Coordinates are fractions of the source canvas.
export const clamp = (v, lo=-1, hi=1) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : 0));
const smooth = v => {v=clamp(v,0,1);return v*v*(3-2*v);};
const frame = (at, values) => ({at, values});
const P = (x=0,y=0,z=0,body=0,left=0,right=0,smile=0,eyeL=1,eyeR=eyeL) =>
  ({ParamAngleX:x,ParamAngleY:y,ParamAngleZ:z,ParamBodyAngleZ:body,ParamArmLSwing:left,ParamArmRSwing:right,
    ParamMouthForm:smile,ParamEyeLOpen:eyeL,ParamEyeROpen:eyeR});
// Each action has anticipation, a held expression, and an eased recovery.
export const ACTIONS = {
  head: {label:'摸摸头',mood:'放松',effect:'spark',lines:['头发要被你揉乱啦。','嗯，这样轻轻的就好。','好啦，给你摸一下。'],frames:[frame(0,P()),frame(.3,P(0,-13,5,2,0,0,.45,.25)),frame(1.2,P(-9,-9,-8,-3,.25,-.2,.7,.2)),frame(2.1,P(8,-4,7,2,.1,0,.65,.8)),frame(3.3,P())]},
  cheek: {label:'戳戳脸',mood:'有点惊讶',effect:'pop',lines:['诶？被你戳到了。','你在叫我吗？我看着你呢。','脸颊可不是按钮哦。'],frames:[frame(0,P()),frame(.16,P(-24,7,-16,-6,.7,.3,-.1)),frame(.7,P(-13,4,-9,-3,.4,0,.2,.35,1)),frame(1.5,P(12,-3,10,3,0,.3,.6)),frame(2.7,P())]},
  shoulder: {label:'拍拍肩',mood:'认真听',effect:'ring',lines:['怎么啦？我在听。','收到，精神起来！','嗯，转过来看你了。'],frames:[frame(0,P()),frame(.22,P(25,5,10,7,.6,-.4)),frame(1.1,P(18,-6,4,4,.2,0,.35)),frame(2.5,P())]},
  hand: {label:'碰一下手',mood:'开心',effect:'spark',lines:['碰到啦！','来，晃一晃手。','收到你的招呼啦。'],frames:[frame(0,P()),frame(.35,P(-13,-5,-8,-5,-.9,.8,.6)),frame(.8,P(8,3,6,4,.8,-.6,.7,.7)),frame(1.4,P(-5,0,-4,-2,-.7,.5,.55)),frame(2.8,P())]},
  release: {label:'松手回弹',mood:'放松',effect:'ring',lines:['好啦，站稳了。','跟着你的节奏晃一晃。'],frames:[frame(0,P()),frame(.35,P(10,2,10,5,.6,-.6,.4)),frame(.85,P(-6,-2,-6,-3,-.35,.3,.45)),frame(1.4,P(3,0,3,1,.15,0)),frame(2,P())]},
  tease: {label:'连续戳戳',mood:'被逗笑了',effect:'pop',lines:['哈哈，给我一点反应的时间嘛。','又被你找到啦，换个地方试试？'],frames:[frame(0,P()),frame(.2,P(-24,5,-15,-6,.65,.4,.6,.25)),frame(.55,P(20,-5,15,6,-.5,-.6,.75,.15)),frame(1,P(-13,-4,-9,-4,.6,.3,.65,.4)),frame(1.5,P(8,1,7,3,0,.3,.6)),frame(3,P())]},
  wink: {label:'眨眨眼',mood:'俏皮',effect:'spark',lines:['这个眼神，接住了吗？','给你一个小小的暗号。'],frames:[frame(0,P()),frame(.25,P(9,-3,-12,-3,.3,0,.7,0,1)),frame(1.05,P(13,0,-10,-2,.2,0,.65,0,1)),frame(2.1,P())]},
  nod: {label:'点点头',mood:'认真听',effect:'ring',lines:['嗯嗯，我在认真听。','好呀，我明白了。'],frames:[frame(0,P()),frame(.4,P(0,-23,3,3,.2,0,.2)),frame(.85,P(0,13,-2,-2)),frame(1.3,P(0,-16,2,2,0,0,.3)),frame(2.6,P())]},
  wave: {label:'打个招呼',mood:'开心',effect:'spark',lines:['嗨，见到你真好。','我在这里，陪你聊一会儿。'],frames:[frame(0,P()),frame(.35,P(16,5,-12,-5,.95,-.8,.7)),frame(.9,P(-13,0,10,5,-.8,.8,.6)),frame(1.45,P(12,3,-9,-4,.95,-.7,.65)),frame(2.05,P(-8,0,7,3,-.7,.6,.5)),frame(3.2,P())]},
  stretch: {label:'活动一下',mood:'轻松',effect:'spark',lines:['一起活动一下，坐久了要休息哦。','左边，右边，好多了。'],frames:[frame(0,P()),frame(.7,P(-27,8,-23,-9,-.9,.85,.35,.6)),frame(1.8,P(-20,10,-20,-8,-1,.9,.5,.4)),frame(3,P(27,8,23,9,.9,-.9,.45,.6)),frame(4.1,P(18,3,16,6,.7,-.7,.5)),frame(5.1,P())]},
  tea: {label:'递杯茶',mood:'惬意',effect:'tea',lines:['谢谢你的茶，闻起来暖暖的。','收到这杯茶啦，一起歇一会儿。'],frames:[frame(0,P()),frame(.55,P(-8,-20,-8,-4,-.7,.7,.4)),frame(1.7,P(0,-8,4,2,-.3,.3,.65,.3)),frame(3,P(8,2,-4,-2,.3,0,.7)),frame(4.2,P())]},
  gift: {label:'送个小礼物',mood:'惊喜',effect:'gift',lines:['是给我的？谢谢这份小惊喜。','收到啦，今天多了一件开心的小事。'],frames:[frame(0,P()),frame(.3,P(0,14,4,-4,-.8,.8,.25)),frame(1.2,P(12,-9,-13,-6,-.65,.7,.8,.35)),frame(2.3,P(-10,0,10,5,.6,-.5,.8,.8)),frame(4,P())]},
  idle: {label:'发会儿呆',mood:'悠闲',effect:'none',lines:['窗外的光，刚刚好。','慢慢来，我们不用着急。'],frames:[frame(0,P()),frame(1.2,P(20,8,12,5,-.35,.3,.2,.7)),frame(3,P(-12,-4,-8,-3,.2,-.2,.3,.65)),frame(4.6,P())]},
  welcome: {label:'欢迎回来',mood:'开心',effect:'spark',lines:['回来啦，接着聊吧。','嗨，我们又见面了。'],frames:[frame(0,P()),frame(.55,P(12,4,-10,-4,.8,-.5,.7)),frame(1.3,P(-9,-4,8,4,-.6,.5,.6)),frame(3,P())]}
};
export const LIMITS = {ParamAngleX:[-45,45],ParamAngleY:[-30,30],ParamAngleZ:[-30,30],ParamBodyAngleZ:[-10,10],
  ParamEyeLOpen:[0,1],ParamEyeROpen:[0,1],ParamEyeBallX:[-1,1],ParamEyeBallY:[-1,1],ParamMouthForm:[-1,1],
  ParamMouthOpenY:[0,1],ParamBreath:[0,1],ParamArmLSwing:[-1,1],ParamArmRSwing:[-1,1],ParamSkirtSwing:[-1,1],ParamHairFront:[-1,1],ParamHairBack:[-1,1]};
export function hitZone(point, profile){
  for(const zone of profile.zones){const [x1,y1,x2,y2]=zone.rect;
    if(point.x>=x1&&point.x<=x2&&point.y>=y1&&point.y<=y2)return zone.id;}
  return null;
}
export function sampleAction(kind, elapsed){
  const frames=ACTIONS[kind]?.frames;if(!frames)return P();
  if(elapsed<=0||elapsed>=frames.at(-1).at)return {...frames[elapsed<=0?0:frames.length-1].values};
  const index=frames.findIndex(f=>f.at>=elapsed),a=frames[index-1],b=frames[index];
  const mix=smooth((elapsed-a.at)/(b.at-a.at));
  return Object.fromEntries(Object.keys(a.values).map(key=>[key,a.values[key]+(b.values[key]-a.values[key])*mix]));
}
export class InteractionDirector {
  constructor({now=()=>performance.now(),random=Math.random,reduced=false}={}){
    this.now=now;this.random=random;this.reduced=reduced;this.current=null;this.hold=null;this.look={x:0,y:0};
    this.gaze={x:0,y:0};this.lastTouch={kind:null,time:-Infinity,count:0};this.lastStart=-Infinity;
    this.lastActive=now();this.idleAt=now()+26000;this.lastTick=now();this.drag={x:0,y:0};this.counts={};this.count=0;
  }
  activity(){this.lastActive=this.now();this.idleAt=this.now()+26000+this.random()*16000;}
  trigger(kind,{quiet=false,force=false}={}){
    if(!ACTIONS[kind])return null;const now=this.now();
    if(!force&&now-this.lastStart<180)return null;
    const touch=['head','cheek','shoulder','hand'].includes(kind);
    if(touch){const last=this.lastTouch;this.lastTouch={kind,time:now,count:last.kind===kind&&now-last.time<1900?last.count+1:1};
      if(this.lastTouch.count>=3){kind='tease';this.lastTouch.count=0;}}
    const action=ACTIONS[kind],ordinal=this.counts[kind]||0;this.counts[kind]=ordinal+1;
    // Blend interruptions from the last displayed pose to avoid snapping.
    this.current={kind,start:now,from:this.lastAction||P()};this.lastStart=now;this.activity();
    if(!quiet)this.count++;
    return {kind,label:action.label,mood:action.mood,line:action.lines[ordinal%action.lines.length],effect:action.effect,quiet};
  }
  begin(zone,point){this.activity();this.hold={zone,start:this.now(),origin:point,point,distance:0,announced:false};}
  move(point){
    if(!this.hold)return;const h=this.hold;h.distance+=Math.hypot(point.x-h.point.x,point.y-h.point.y);h.point=point;this.activity();
  }
  end(cancelled=false){
    const h=this.hold;this.hold=null;if(!h||cancelled)return null;
    const moved=h.distance>.015,held=this.now()-h.start>450;
    if(moved&&(h.zone==='hand-l'||h.zone==='hand-r'||h.zone==='shoulder'))return this.trigger('release',{force:true});
    if(h.zone==='head'&&(moved||held))return this.trigger('head',{force:true,quiet:h.announced});
    return this.trigger(h.zone.startsWith('hand-')?'hand':h.zone,{force:true});
  }
  tick({mouth=0,speaking=false,busy=false}={}){
    const now=this.now(),dt=clamp((now-this.lastTick)/1000,0,.05),t=now/1000;this.lastTick=now;
    const approach=1-Math.exp(-dt*11);this.look.x+=(this.gaze.x-this.look.x)*approach;this.look.y+=(this.gaze.y-this.look.y)*approach;
    let event=null;
    if(!this.current&&!this.hold&&!speaking&&!busy&&now>this.idleAt)event=this.trigger('idle',{quiet:true});
    let a=P();
    if(this.current){const elapsed=(now-this.current.start)/1000;
      a=sampleAction(this.current.kind,elapsed);const blend=smooth(elapsed/.18);
      for(const key in a)a[key]=this.current.from[key]+(a[key]-this.current.from[key])*blend;
      if(elapsed>=ACTIONS[this.current.kind].frames.at(-1).at)this.current=null;
    }
    const h=this.hold;let dx=0,dy=0;
    if(h){dx=clamp((h.point.x-h.origin.x)*7);dy=clamp((h.point.y-h.origin.y)*6);
      const weight=smooth((now-h.start)/250);
      if(h.zone==='head'){
        const pat=Math.sin(t*9)*Math.min(h.distance*8,1);
        const target=P(dx*20,-10+dy*8,dx*19+pat*5,dx*5,.15,-.1,.65,.28);
        for(const key in a)a[key]+=(target[key]-a[key])*weight;
        if(!h.announced&&(now-h.start>450||h.distance>.02)){h.announced=true;event={kind:'head',label:'正在摸头',mood:'放松',line:'嗯，轻轻地摸就好。',effect:'spark',quiet:false};this.count++;}
      }else if(h.zone.startsWith('hand-')||h.zone==='shoulder'){
        const left=h.zone==='hand-l',target=P(dx*30,-dy*12,dx*22,dx*9,left?dx:0,left?0:dx,.45);
        if(!left&&h.zone==='shoulder')target.ParamArmLSwing=dx*.7;
        for(const key in a)a[key]+=(target[key]-a[key])*weight;
      }
    }
    const dragGain=h&&h.zone!=='head'?1:0;this.drag.x+=(dx*dragGain-this.drag.x)*approach;this.drag.y+=(dy*dragGain-this.drag.y)*approach;
    const phase=t%7.5,distance=Math.abs(phase-1.5),blink=distance<.14?1-Math.cos(distance/.14*Math.PI/2):1;
    const motion=this.reduced?.25:1;
    const values={...a,ParamAngleX:a.ParamAngleX+this.look.x*16,ParamAngleY:a.ParamAngleY-this.look.y*9,
      ParamAngleZ:a.ParamAngleZ-this.look.x*3,ParamBodyAngleZ:a.ParamBodyAngleZ+Math.sin(t*1.1)*1.25,
      ParamEyeLOpen:Math.min(a.ParamEyeLOpen,blink),ParamEyeROpen:Math.min(a.ParamEyeROpen,blink),
      ParamEyeBallX:this.look.x*.85,ParamEyeBallY:-this.look.y*.65,ParamMouthOpenY:mouth,
      ParamBreath:(1-Math.cos(t*1.55))/2,ParamArmLSwing:a.ParamArmLSwing+.12*Math.sin(t*1.6),
      ParamArmRSwing:a.ParamArmRSwing-.1*Math.sin(t*1.6),ParamSkirtSwing:Math.sin(t*1.1-.5)*.25+a.ParamBodyAngleZ*.04,
      ParamHairFront:Math.sin(t*1.5)*.18+dx*.6,ParamHairBack:Math.sin(t*1.5-.6)*.18+dx*.4};
    for(const key in values){if(/Angle|Swing|Hair/.test(key))values[key]*=motion;values[key]=clamp(values[key],...(LIMITS[key]||[-1,1]));}
    this.lastAction=a;
    return {values,event,kind:this.hold?'holding-'+this.hold.zone:this.current?.kind||'idle-breathe',offset:{x:this.drag.x*motion,y:this.drag.y*motion}};
  }
}
