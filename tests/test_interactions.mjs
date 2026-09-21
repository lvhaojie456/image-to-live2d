import test from 'node:test';
import assert from 'node:assert/strict';
import {ACTIONS, LIMITS, InteractionDirector, hitZone, sampleAction} from '../adapters/local_chat/web/interactions.mjs';

function fixture(reduced=false){let time=10000;const director=new InteractionDirector({now:()=>time,random:()=>.5,reduced});return {director,advance(ms){time+=ms;return director.tick();}};}
test('touch coordinates choose a real region, leave blank space alone, and preserve left/right hand',()=>{
  const profile={zones:[{id:'head',rect:[.4,0,.6,.1]},{id:'hand-l',rect:[.7,.4,.8,.5]},{id:'hand-r',rect:[.2,.4,.3,.5]}]};
  assert.equal(hitZone({x:.5,y:.05},profile),'head');assert.equal(hitZone({x:.75,y:.45},profile),'hand-l');
  assert.equal(hitZone({x:.25,y:.45},profile),'hand-r');assert.equal(hitZone({x:.1,y:.1},profile),null);
});
test('all full motion sequences remain finite, respect native ranges, and return to rest',()=>{
  for(const kind of Object.keys(ACTIONS)){
    const {director,advance}=fixture();director.gaze={x:1,y:-1};director.trigger(kind);
    for(let i=0;i<360;i++){const pose=advance(20);for(const [key,value] of Object.entries(pose.values)){
      assert.ok(Number.isFinite(value),kind+' '+key);assert.ok(value>=LIMITS[key][0]&&value<=LIMITS[key][1],kind+' '+key);}}
    assert.equal(director.current,null,kind);
    const end=sampleAction(kind,99);assert.equal(end.ParamAngleZ,0);assert.equal(end.ParamEyeLOpen,1);
  }
});
test('repeated pokes change the reaction while bounce noise is debounced',()=>{
  const {director,advance}=fixture();assert.equal(director.trigger('cheek').kind,'cheek');assert.equal(director.trigger('cheek'),null);
  advance(300);assert.equal(director.trigger('cheek').kind,'cheek');advance(300);assert.equal(director.trigger('cheek').kind,'tease');
  advance(3000);assert.equal(director.trigger('cheek').kind,'cheek');
});
test('holding and rubbing head closes eyes and responds continuously; cancel releases the hold',()=>{
  const {director,advance}=fixture();director.begin('head',{x:.5,y:.05});director.move({x:.55,y:.05});
  const pose=advance(700);assert.equal(pose.event.kind,'head');assert.ok(pose.values.ParamEyeLOpen<.5);assert.ok(pose.values.ParamAngleZ>5);
  assert.equal(director.end(true),null);assert.equal(director.hold,null);
});
test('a long head rub counts once and release does not queue a second voice',()=>{
  const {director,advance}=fixture();director.begin('head',{x:.5,y:.05});director.move({x:.56,y:.05});
  advance(650);assert.equal(director.count,1);assert.equal(director.end().quiet,true);assert.equal(director.count,1);
});
test('hand drag leans in both directions, survives out-of-canvas motion, and springs back after release',()=>{
  const {director,advance}=fixture();director.begin('hand-l',{x:.7,y:.5});director.move({x:30,y:-50});
  let pose=advance(500);assert.ok(pose.values.ParamBodyAngleZ>7);assert.ok(pose.offset.x>0);
  director.move({x:-30,y:50});pose=advance(200);assert.ok(pose.values.ParamBodyAngleZ< -7);
  assert.equal(director.end().kind,'release');for(let i=0;i<100;i++)pose=advance(30);
  assert.equal(director.current,null);assert.ok(Math.abs(pose.offset.x)<.001);
});
test('idle events are quiet and never interrupt speaking, composing a reply, or holding',()=>{
  const {director,advance}=fixture();advance(27000);assert.equal(director.current.kind,'idle');
  advance(6000);director.idleAt=0;assert.equal(director.tick({speaking:true}).event,null);
  assert.equal(director.tick({busy:true}).event,null);director.begin('hand-r',{x:.3,y:.5});director.idleAt=0;assert.equal(director.tick().event,null);
});
test('reduced motion lowers body movement while maintaining expression and audio mouth',()=>{
  const normal=fixture(),reduced=fixture(true);normal.director.trigger('stretch');reduced.director.trigger('stretch');
  const a=normal.advance(1700),b=reduced.advance(1700);assert.ok(Math.abs(b.values.ParamBodyAngleZ)<Math.abs(a.values.ParamBodyAngleZ)*.3);
  assert.equal(reduced.director.tick({mouth:.65,speaking:true}).values.ParamMouthOpenY,.65);
});
