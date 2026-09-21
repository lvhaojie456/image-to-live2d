'use strict';
const $ = id => document.getElementById(id);
const state = {name:'小忆', speechAvailable:false, asrAvailable:false, busy:false, model:null, app:null, zoom:true,
  gaze:{x:0,y:0}, look:{x:0,y:0}, gesture:null, speaking:false, mouth:0, audioLevel:0};
const diagnostic = window.companionDiagnostics = {ready:false, maxAudioLevel:0, speakingFrames:0, interactions:0, lastReply:'', state};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let bubbleTimer, controller, activeRequest = 0;

function bubble(text, duration=3200) {
  clearTimeout(bubbleTimer); $('bubble').textContent=text; $('bubble').classList.add('show');
  bubbleTimer=setTimeout(()=>$('bubble').classList.remove('show'),duration);
}
function status(text) { $('avatar-status').replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(text)); }
function notice(text='') { $('notice').textContent=text; }
function scrollToLatest(){requestAnimationFrame(()=>$('messages').scrollTop=$('messages').scrollHeight);}
async function api(path, body, signal) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',signal,
    headers:body===undefined?{}:{'Content-Type':'application/json','X-Live2d-Client':'local'},
    body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok) {
    const data=await response.json().catch(()=>({}));
    throw new Error(data.error||'request_failed');
  }
  return response;
}

class SpeechQueue {
  constructor(){this.context=null;this.analyser=null;this.source=null;this.epoch=0;this.tail=Promise.resolve();this.pending=0;this.samples=null;}
  async unlock() {
    if(!this.context){this.context=new AudioContext();this.analyser=this.context.createAnalyser();this.analyser.fftSize=512;
      this.analyser.connect(this.context.destination);this.samples=new Float32Array(this.analyser.fftSize);}
    if(this.context.state!=='running')await this.context.resume();
  }
  enabled(){return $('voice').checked&&state.speechAvailable;}
  stop(){this.epoch++;if(this.source){try{this.source.stop();}catch{}this.source=null;}this.pending=0;
    state.speaking=false;state.audioLevel=0;state.mouth=0;$('stop').hidden=true;this.tail=Promise.resolve();}
  enqueue(text) {
    text=text.trim();if(!text||!/[\p{L}\p{N}]/u.test(text)||!this.enabled())return;
    const epoch=this.epoch;this.pending++;$('stop').hidden=false;
    const ready=api('/api/speech',{text}).then(r=>r.json()).then(async data=>{
      if(epoch!==this.epoch)return null;
      const response=await api(data.audio);return response.arrayBuffer();
    }).catch(error=>({error}));
    this.tail=this.tail.then(async()=>{
      try{
        const result=await ready;if(epoch!==this.epoch||!result)return;
        if(result.error)throw result.error;
        await this.unlock();const audio=await this.context.decodeAudioData(result);
        if(epoch!==this.epoch)return;
        if(this.context.state!=='running')throw Error('audio_suspended');
        await new Promise(resolve=>{
          const source=this.context.createBufferSource();source.buffer=audio;source.connect(this.analyser);this.source=source;
          state.speaking=true;status('正在和你说话');source.onended=()=>{source.disconnect();if(this.source===source){this.source=null;state.speaking=false;}resolve();};source.start();
        });
      }catch(error){if(epoch===this.epoch)notice('这句话暂时没有读出来，文字已保留。');}
      finally{if(epoch===this.epoch){this.pending--;if(!this.pending){$('stop').hidden=true;state.speaking=false;state.mouth=0;status(state.busy?'正在想怎么回答':'在这里，听你说');}}}
    });
  }
  level(){
    if(!state.speaking||!this.analyser)return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    const rms=Math.sqrt(this.samples.reduce((total,s)=>total+s*s,0)/this.samples.length);
    const level=Math.min(.95,Math.max(0,(rms-.007)*9));
    diagnostic.maxAudioLevel=Math.max(diagnostic.maxAudioLevel,level);if(level>.03)diagnostic.speakingFrames++;
    return level;
  }
}
const speech=new SpeechQueue();
const recording={recorder:null,stream:null,id:0,busy:false,timer:null,started:0};

function updateRecordingUi(active=false){
  $('microphone').classList.toggle('recording',active);$('microphone').textContent=active?'结束识别':'说话';
  $('microphone').setAttribute('aria-label',active?'结束录音并识别':'开始语音输入');
  $('record-cancel').hidden=!active;$('send').disabled=state.busy||recording.busy;$('message').disabled=state.busy||active;
}
function cancelRecording(){
  recording.id++;clearInterval(recording.timer);
  if(recording.recorder?.state==='recording')recording.recorder.stop();
  recording.stream?.getTracks().forEach(track=>track.stop());recording.recorder=null;recording.stream=null;recording.busy=false;
  $('microphone').disabled=false;updateRecordingUi();status(state.busy?'正在想怎么回答':'在这里，听你说');
}
async function wavFromRecording(blob){
  await speech.unlock();const decoded=await speech.context.decodeAudioData(await blob.arrayBuffer());
  if(decoded.duration<.25)throw Error('too_short');
  const duration=Math.min(decoded.duration,60),offline=new OfflineAudioContext(1,Math.ceil(duration*16000),16000);
  const node=offline.createBufferSource();node.buffer=decoded;node.connect(offline.destination);node.start();const rendered=await offline.startRendering();
  const samples=rendered.getChannelData(0),bytes=new ArrayBuffer(44+samples.length*2),view=new DataView(bytes);
  const text=(offset,value)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));};
  text(0,'RIFF');view.setUint32(4,36+samples.length*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++){const value=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,Math.round(value*(value<0?32768:32767)),true);}
  const array=new Uint8Array(bytes);let binary='';for(let i=0;i<array.length;i+=16384)binary+=String.fromCharCode(...array.subarray(i,i+16384));return btoa(binary);
}
async function beginRecording(){
  if(recording.busy||state.busy)return;
  speech.stop();notice();recording.busy=true;const id=++recording.id;updateRecordingUi();$('microphone').disabled=true;
  try{
    await speech.unlock();
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
    if(id!==recording.id){stream.getTracks().forEach(t=>t.stop());return;}
    recording.stream=stream;const mime=['audio/webm;codecs=opus','audio/mp4'].find(value=>MediaRecorder.isTypeSupported(value));
    const recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});recording.recorder=recorder;const chunks=[];
    recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
    recorder.onerror=()=>{notice('录音中断，请重试。');cancelRecording();};
    recorder.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop());if(id!==recording.id)return;
      clearInterval(recording.timer);recording.stream=null;recording.recorder=null;
      $('record-cancel').hidden=true;$('microphone').classList.remove('recording');$('microphone').textContent='识别中…';$('microphone').disabled=true;status('正在听清你说的话');
      try{const audio=await wavFromRecording(new Blob(chunks,{type:recorder.mimeType}));if(id!==recording.id)return;
        const data=await(await api('/api/transcribe',{audio})).json();if(id!==recording.id)return;
        $('message').value=data.text;notice('已转成文字，确认后点发送。');diagnostic.lastTranscript=data.text;
      }catch(error){if(id===recording.id)notice(error.message==='too_short'?'录音太短，请再说一句。':'这次没有听清，请重试或输入文字。');}
      finally{if(id===recording.id){recording.busy=false;$('microphone').disabled=false;updateRecordingUi();status('在这里，听你说');$('message').focus();}}
    };
    recording.started=performance.now();recorder.start(250);$('microphone').disabled=false;updateRecordingUi(true);status('正在听你说话');
    recording.timer=setInterval(()=>{if(id!==recording.id)return;const seconds=Math.floor((performance.now()-recording.started)/1000);$('microphone').textContent='结束识别 · '+seconds+'秒';if(seconds>=59&&recorder.state==='recording')recorder.stop();},250);
  }catch(error){if(id!==recording.id)return;cancelRecording();$('microphone').disabled=false;notice(error.name==='NotAllowedError'?'麦克风权限未开启。请在浏览器权限中允许，或输入文字。':'无法使用麦克风，请检查设备后重试。');}
}

function react(kind, speak=true) {
  state.gesture={kind,start:performance.now()};diagnostic.interactions++;
  const line={wink:'看到啦，眨个眼回应你。',nod:'嗯嗯，我在认真听。',wave:'嗨，我在这里。'}[kind];
  bubble(line);if(speak){speech.stop();speech.unlock().catch(()=>{});speech.enqueue(line);}
}

function message(role, text, pending=false) {
  const row=document.createElement('article');row.className='message '+role+(pending?' pending':'');
  const sender=document.createElement('span');sender.className='sender';sender.textContent=role==='user'?'你':state.name;
  const content=document.createElement('div');content.className='content';content.textContent=text;
  row.append(sender,content);$('messages').append(row);scrollToLatest();
  return {row,content,update(value){content.textContent=value;scrollToLatest();},
    finish(value){row.classList.remove('pending');this.update(value);if(role==='assistant'&&state.speechAvailable){
      const button=document.createElement('button');button.className='replay';button.textContent='再听一遍';
      button.addEventListener('click',()=>{speech.stop();$('voice').checked=true;speech.unlock().catch(()=>{});queueText(value);});row.append(button);scrollToLatest();
    }}};
}
function sentences(text) {return text.match(/[^。！？!?；;\n]+[。！？!?；;\n]?/g)||[text];}
function queueText(text){for(const part of sentences(text))for(let i=0;i<part.length;i+=120)speech.enqueue(part.slice(i,i+120));}
function renderHistory(messages){$('messages').replaceChildren();for(const m of messages)message(m.role,m.content).finish(m.content);
  if(!messages.length){message('assistant','嗨，我在这里。今天想和我聊点什么？').finish('嗨，我在这里。今天想和我聊点什么？');}
  $('suggestions').hidden=messages.length>0;
  scrollToLatest();
}

async function send(text, requestId=crypto.randomUUID(), retryRow=null) {
  text=text.trim();if(!text||state.busy||recording.busy)return;
  state.busy=true;activeRequest++;const request=activeRequest;controller=new AbortController();
  $('send').disabled=true;$('message').disabled=true;$('suggestions').hidden=true;notice();speech.stop();speech.unlock().catch(()=>{});
  status('正在想怎么回答');if(!retryRow)message('user',text);else retryRow.remove();
  const reply=message('assistant','正在想…',true);let received='',spoken=0,done=false;
  if(/眨.*眼|wink/i.test(text))react('wink',false);else if(/点.*头/.test(text))react('nod',false);else if(/招呼|挥.*手/.test(text))react('wave',false);
  try{
    const response=await api('/api/chat',{text,requestId},controller.signal);const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
    const consume=event=>{
      if(request!==activeRequest)return;
      if(event.type==='delta'){
        received+=event.text;reply.update(received);
        const remaining=received.slice(spoken);const match=remaining.match(/^([\s\S]*?[。！？!?；;\n])/);
        if(match){queueText(match[1]);spoken+=match[1].length;}
      } else if(event.type==='done'){
        if(!event.message)throw Error('cancelled');
        if(!received)received=event.message.content;
        queueText(received.slice(spoken));spoken=received.length;
        reply.finish(event.message.content);diagnostic.lastReply=event.message.content;done=true;
        bubble(event.message.content.length>55?event.message.content.slice(0,55)+'…':event.message.content,4500);
        state.gesture={kind:'nod',start:performance.now()};
      } else if(event.type==='error')throw Error(event.error);
      else if(event.type==='cancelled')throw Error('cancelled');
    };
    while(true){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});
      let index;while((index=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,index);buffer=buffer.slice(index+1);if(line.trim())consume(JSON.parse(line));}}
    if(!done)throw Error('incomplete_reply');
  }catch(error){
    if(request!==activeRequest||error.name==='AbortError')return;
    speech.stop();reply.row.classList.remove('pending');reply.row.classList.add('failed');
    reply.update(received?received+'\n（这次回复中断了）':'这次没能连上，点一下重试。');
    const retry=document.createElement('button');retry.className='replay';retry.textContent='重试这句话';retry.onclick=()=>send(text,requestId,reply.row);reply.row.append(retry);
    notice(error.message==='chat_busy'?'上一条回复还在处理中，请稍后重试。':'连接暂时不顺畅，可以重试。');
  }finally{if(request===activeRequest){state.busy=false;$('send').disabled=false;$('message').disabled=false;$('message').focus();if(!state.speaking&&!speech.pending)status('在这里，听你说');}}
}

function setName(name){state.name=name;$('name').textContent=name;$('profile-name').value=name;document.title='和'+name+'聊一会儿';}
function fit(){
  if(!state.model)return;const parent=$('avatar').parentElement,w=parent.clientWidth,h=parent.clientHeight;state.app.renderer.resize(w,h);
  const bounds=state.zoom?{x:515,y:0,width:238,height:400}:{x:425,y:0,width:420,height:1280};
  const im=state.model.internalModel,r=(im.originalWidth||1280)/1280,scale=Math.min(w/(bounds.width*r),h/(bounds.height*r))*.91;
  state.model.scale.set(scale);state.model.position.set(w/2-(bounds.x+bounds.width/2)*r*scale,h/2-(bounds.y+bounds.height/2)*r*scale+13);
}
function pose(){
  const t=performance.now()/1000,phase=t%8;
  const blink=c=>{const d=Math.abs(phase-c);return d<.16?(1+Math.cos(Math.PI*d/.16))/2:0;};
  let eyeL=1-Math.max(blink(1.6),blink(5.6)),eyeR=eyeL,nod=0,arm=0,smile=0;
  state.look.x+=(state.gaze.x-state.look.x)*.08;state.look.y+=(state.gaze.y-state.look.y)*.08;
  if(state.gesture){const elapsed=(performance.now()-state.gesture.start)/1000;
    if(elapsed>2)state.gesture=null;else{const weight=Math.sin(Math.min(1,elapsed/2)*Math.PI);
      if(state.gesture.kind==='wink'){eyeL=1-Math.sin(Math.min(1,elapsed/.8)*Math.PI);smile=.55*weight;}
      if(state.gesture.kind==='nod')nod=7*Math.sin(elapsed*8)*Math.exp(-elapsed*1.8);
      if(state.gesture.kind==='wave'){arm=.95*Math.sin(elapsed*7)*weight;smile=.45*weight;}}}
  state.audioLevel=speech.level();state.mouth+=(state.audioLevel-state.mouth)*(state.audioLevel>state.mouth?.58:.24);
  if(!state.speaking&&state.mouth<.005)state.mouth=0;
  const motion=reduceMotion?.25:1;
  return {ParamEyeLOpen:eyeL,ParamEyeROpen:eyeR,ParamEyeBallX:state.look.x*.6,ParamEyeBallY:-state.look.y*.45,
    ParamAngleX:state.look.x*9,ParamAngleY:-state.look.y*5+nod,ParamAngleZ:-state.look.x*2,
    ParamMouthOpenY:state.mouth,ParamMouthForm:smile,ParamBreath:(1-Math.cos(t*Math.PI/2))/2,
    ParamBodyAngleZ:motion*3*Math.sin(t*Math.PI/4),ParamArmLSwing:arm+motion*.25*Math.sin(t*Math.PI/2),
    ParamArmRSwing:-motion*.23*Math.sin(t*Math.PI/2),ParamSkirtSwing:motion*.28*Math.sin(t*Math.PI/4-.4)};
}
async function initialize(){
  const info=await(await api('/api/state')).json();setName(info.name);state.speechAvailable=info.speechAvailable;
  state.asrAvailable=info.asrAvailable&&!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined';
  $('microphone').hidden=!state.asrAvailable;
  $('privacy').textContent='聊天内容会发给已配置的模型服务生成回复；'+(info.speechProvider==='tencent'?'回复文字还会发送至腾讯云语音合成（'+info.voice+'）。':'朗读使用系统中文语音。')+(info.asrAvailable?'麦克风录音经你同意后发送至腾讯云识别；本机不保存原始录音。':'')+'聊天记录与回复语音保存在这台电脑，没有克隆照片人物的声音。';
  $('voice').disabled=!info.speechAvailable;$('voice').checked=info.speechAvailable;
  if(!info.speechAvailable)notice('这台电脑暂不支持语音朗读，文字聊天和动作可以使用。');
  renderHistory(info.messages);
  state.app=new PIXI.Application({view:$('avatar'),backgroundAlpha:0,antialias:true,resolution:Math.min(devicePixelRatio,2),autoDensity:true});
  state.model=await PIXI.live2d.Live2DModel.from(info.modelPath,{autoInteract:false});state.model.anchor.set(0,0);state.app.stage.addChild(state.model);fit();
  state.model.internalModel.on('beforeModelUpdate',()=>{const values=pose();diagnostic.lastPose=values;for(const [id,v] of Object.entries(values))state.model.internalModel.coreModel.setParameterValueById(id,v);});
  diagnostic.ready=true;status('在这里，听你说');bubble('嗨，我是'+state.name+'。见到你很开心。',4200);
}

$('composer').addEventListener('submit',event=>{event.preventDefault();const value=$('message').value;if(!value.trim())return;$('message').value='';send(value);});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>react(button.dataset.action)));
document.querySelectorAll('#suggestions button').forEach(button=>button.addEventListener('click',()=>send(button.textContent)));
$('avatar').addEventListener('pointermove',event=>{const b=event.currentTarget.getBoundingClientRect();state.gaze.x=Math.max(-1,Math.min(1,(event.clientX-b.left)/b.width*2-1));state.gaze.y=Math.max(-1,Math.min(1,(event.clientY-b.top)/b.height*2-1));});
$('avatar').addEventListener('pointerleave',()=>{state.gaze={x:0,y:0};});
$('avatar').addEventListener('click',event=>{const bounds=event.currentTarget.getBoundingClientRect();react((event.clientY-bounds.top)/bounds.height<.55?'wink':'nod');});
$('zoom').addEventListener('click',()=>{state.zoom=!state.zoom;$('zoom').textContent=state.zoom?'看全身 ↗':'看近一点 ↗';fit();});
$('stop').addEventListener('click',()=>{speech.stop();status(state.busy?'正在想怎么回答':'在这里，听你说');});
$('voice').addEventListener('change',()=>{if(!$('voice').checked)speech.stop();else speech.unlock().catch(()=>{});});
$('microphone').addEventListener('click',()=>{if(recording.recorder?.state==='recording'){recording.recorder.stop();return;}if(recording.busy||state.busy)return;if(sessionStorage.getItem('local-mic-consent')!=='yes')$('mic-consent').showModal();else beginRecording();});
$('mic-decline').addEventListener('click',()=>$('mic-consent').close());
$('mic-accept').addEventListener('click',()=>{sessionStorage.setItem('local-mic-consent','yes');$('mic-consent').close();beginRecording();});
$('record-cancel').addEventListener('click',()=>{cancelRecording();notice('录音已取消。');});
$('settings-open').addEventListener('click',()=>$('settings').showModal());$('settings-close').addEventListener('click',()=>$('settings').close());
$('profile').addEventListener('submit',async event=>{event.preventDefault();try{const data=await(await api('/api/profile',{name:$('profile-name').value})).json();setName(data.name);$('settings').close();bubble('好呀，以后就叫我'+data.name+'。');}catch{notice('名字没有保存成功，请重试。');}});
$('clear').addEventListener('click',async()=>{activeRequest++;controller?.abort();cancelRecording();speech.stop();state.busy=false;$('send').disabled=false;$('message').disabled=false;
  try{await api('/api/clear',{});renderHistory([]);notice();$('settings').close();bubble('我们重新开始吧。');}catch{notice('没有清空成功，请重试。');}});
addEventListener('resize',()=>{fit();scrollToLatest();});addEventListener('pagehide',()=>{controller?.abort();cancelRecording();speech.stop();});
initialize().catch(()=>{notice('形象暂时没加载成功，请刷新页面。');status('加载遇到问题');});
