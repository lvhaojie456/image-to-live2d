import {InteractionDirector, hitZone, clamp} from '/interactions.mjs';
const $ = id => document.getElementById(id);
const state = {name:'小忆', speechAvailable:false, asrAvailable:false, busy:false, model:null, app:null, zoom:true,
  speaking:false, mouth:0, audioLevel:0};
const diagnostic = window.companionDiagnostics = {ready:false, maxAudioLevel:0, speakingFrames:0, interactions:0, lastReply:'', state};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const director=new InteractionDirector({reduced:reduceMotion});
let controller, activeRequest=0, profile, showZones=false, lastVoice=-Infinity, heldProp=null, propDrag=null;
let viewport={x:0,y:0,scale:1}, pointerId=null, noticeTimer, hiddenAt=0, frameNumber=0;
let interactionContext=null;
const zoneNodes=[];

function bubble(text) { $('bubble').textContent=text; }
function status(text) { $('avatar-status').replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(text)); }
function notice(text='') { $('notice').textContent=text; clearTimeout(noticeTimer);$('scene-notice').textContent=text;$('scene-notice').hidden=!text;
  if(text)noticeTimer=setTimeout(()=>$('scene-notice').hidden=true,6000);
}
function openChat(open=true){
  $('scene').scrollTop=0;$('scene').classList.toggle('chat-open',open);$('chat-panel').inert=!open;$('chat-open').setAttribute('aria-expanded',String(open));
  if(open){$('message').focus({preventScroll:true});scrollToLatest();}else $('chat-open').focus({preventScroll:true});
}
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
      finally{if(id===recording.id){recording.busy=false;$('microphone').disabled=false;updateRecordingUi();status('在这里，听你说');$('message').focus({preventScroll:true});}}
    };
    recording.started=performance.now();recorder.start(250);$('microphone').disabled=false;updateRecordingUi(true);status('正在听你说话');
    recording.timer=setInterval(()=>{if(id!==recording.id)return;const seconds=Math.floor((performance.now()-recording.started)/1000);$('microphone').textContent='结束识别 · '+seconds+'秒';if(seconds>=59&&recorder.state==='recording')recorder.stop();},250);
  }catch(error){if(id!==recording.id)return;cancelRecording();$('microphone').disabled=false;notice(error.name==='NotAllowedError'?'麦克风权限未开启。请在浏览器权限中允许，或输入文字。':'无法使用麦克风，请检查设备后重试。');}
}

function propArtwork(kind){const span=document.createElement('span');span.className=kind==='tea'?'tea-art':'gift-art';span.append(document.createElement('i'));return span;}
function particles(effect,point){
  if(reduceMotion||effect==='none')return;
  const rect=$('stage').getBoundingClientRect();const at=point||sourceToStage(zoneCenter('head'));
  for(let i=0;i<(effect==='ring'?1:5);i++){
    const el=document.createElement('span');el.className='effect '+effect;el.textContent=effect==='ring'?'':(effect==='pop'?'·':['✧','✦','⋆'][i%3]);
    el.style.left=(clamp(at.x,15,rect.width-15)+(i-2)*14)+'px';el.style.top=(at.y+(i%2)*12)+'px';el.style.animationDelay=i*.06+'s';
    $('effects').append(el);setTimeout(()=>el.remove(),1700);
  }
}
function feedback(event,{speak=true,point=null}={}){
  if(!event)return;
  if(!event.quiet)interactionContext={kind:event.kind,at:performance.now()};
  diagnostic.interactions=director.count;diagnostic.lastInteraction=event.kind;
  $('scene').dataset.lastInteraction=event.kind;$('scene').dataset.interactions=director.count;
  $('mood').textContent=event.mood;$('reaction-label').textContent=event.label;
  $('interaction-count').textContent=director.count?'互动 '+director.count+' 次':'初次见面';
  if(!state.busy&&!state.speaking)bubble(event.line);
  particles(event.effect,point);
  if(['tea','gift'].includes(event.kind)){
    heldProp={kind:event.kind,until:performance.now()+4600};$('held-prop').replaceChildren(propArtwork(event.kind));$('held-prop').hidden=false;
    requestAnimationFrame(()=>$('held-prop').classList.add('show'));
  }
  if(speak&&!event.quiet&&!state.busy&&!recording.busy&&!state.speaking&&performance.now()-lastVoice>3500){
    lastVoice=performance.now();speech.stop();speech.unlock().catch(()=>{});speech.enqueue(event.line);
  }
}
function react(kind,speak=true){feedback(director.trigger(kind),{speak});}
function zoneCenter(kind){const zone=profile?.zones.find(z=>z.id===kind)||profile?.zones[0];
  return zone?{x:(zone.rect[0]+zone.rect[2])/2,y:(zone.rect[1]+zone.rect[3])/2}:{x:.5,y:.13};}
function sourceToStage(point){
  if(!state.model)return{x:0,y:0};const im=state.model.internalModel;
  return state.model.toGlobal(new PIXI.Point(point.x*im.originalWidth,point.y*im.originalHeight));
}
function pointOnModel(event){
  const rect=$('avatar').getBoundingClientRect();const im=state.model.internalModel;
  const local=state.model.toLocal(new PIXI.Point(event.clientX-rect.left,event.clientY-rect.top));
  return{x:local.x/im.originalWidth,y:local.y/im.originalHeight};
}
function setGaze(event){
  const b=$('avatar').getBoundingClientRect(),face=sourceToStage(zoneCenter('cheek'));
  director.gaze={x:clamp((event.clientX-b.left-face.x)/Math.max(110,b.width*.3)),y:clamp((event.clientY-b.top-face.y)/Math.max(110,b.height*.35))};
}
function refreshZones(){
  for(const {element,zone} of zoneNodes){const [x1,y1,x2,y2]=zone.rect,a=sourceToStage({x:x1,y:y1}),b=sourceToStage({x:x2,y:y2});
    element.style.left=a.x+'px';element.style.top=a.y+'px';element.style.width=(b.x-a.x)+'px';element.style.height=(b.y-a.y)+'px';}
}
function updateTouchBounds(){
  const im=state.model.internalModel;
  for(const zone of profile.zones){
    if(zone.meshIndex===undefined||zone.meshIndex<0)continue;
    const b=im.getDrawableBounds(zone.meshIndex),base=zone.meshRect;
    const current=[b.x/im.originalWidth,b.y/im.originalHeight,(b.x+b.width)/im.originalWidth,(b.y+b.height)/im.originalHeight];
    zone.rect=zone.baseRect.map((value,i)=>{const axis=i%2;return current[axis]+(value-base[axis])/(base[axis+2]-base[axis])*(current[axis+2]-current[axis]);});
  }
}
function setupTouches(){
  const canvas=$('avatar');
  const im=state.model.internalModel;
  for(const zone of profile.zones){
    zone.baseRect=[...zone.rect];if(!zone.meshRect)continue;
    zone.meshIndex=zone.mesh?im.getDrawableIndex(zone.mesh):-1;
    // Older exporters name sleeves and hands Handwear/Handwear2. Match source
    // bounds when semantic drawable IDs are unavailable, then follow real vertices.
    if(zone.meshIndex<0){let best=.06;
      im.getDrawableIDs().forEach((id,index)=>{const b=im.getDrawableBounds(index);
        const rect=[b.x/im.originalWidth,b.y/im.originalHeight,(b.x+b.width)/im.originalWidth,(b.y+b.height)/im.originalHeight];
        const error=rect.reduce((sum,v,i)=>sum+Math.abs(v-zone.meshRect[i]),0);
        if(error<best){best=error;zone.meshIndex=index;}
      });
    }
  }
  canvas.dataset.trackedZones=profile.zones.filter(z=>z.meshIndex>=0).length;
  for(const zone of profile.zones){const element=document.createElement('div');element.className='touch-zone';element.dataset.zone=zone.id;const label=document.createElement('span');label.textContent=zone.label.split(' · ')[0];element.append(label);$('touch-zones').append(element);zoneNodes.push({zone,element});}
  $('touch-zones').hidden=true;
  canvas.addEventListener('pointerdown',event=>{
    if(!event.isPrimary||event.button!==0||pointerId!==null)return;const point=pointOnModel(event),zone=hitZone(point,profile);if(!zone)return;
    event.preventDefault();pointerId=event.pointerId;canvas.setPointerCapture(pointerId);director.begin(zone,point);setGaze(event);speech.unlock().catch(()=>{});
    $('interaction-tip').textContent=zone==='head'?'按住头发，轻轻左右揉动':'按住并拖动，松开会慢慢回正';
    $('touch-hint').hidden=true;canvas.style.cursor='grabbing';
  });
  canvas.addEventListener('pointermove',event=>{
    setGaze(event);if(pointerId!==null&&event.pointerId===pointerId){director.move(pointOnModel(event));return;}
    const zone=hitZone(pointOnModel(event),profile);canvas.style.cursor=zone?'grab':'default';
    $('touch-hint').hidden=!zone||event.pointerType==='touch';
    if(zone){const b=canvas.getBoundingClientRect();$('touch-hint').textContent=profile.zones.find(z=>z.id===zone).label;
      $('touch-hint').style.left=clamp(event.clientX-b.left,90,b.width-90)+'px';$('touch-hint').style.top=(event.clientY-b.top)+'px';}
  });
  const finish=(event,cancelled=false)=>{
    if(event.pointerId!==pointerId)return;pointerId=null;feedback(director.end(cancelled));
    if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
    canvas.style.cursor='grab';$('interaction-tip').textContent='轻触有回应，按住也有小惊喜';
  };
  canvas.addEventListener('pointerup',event=>finish(event));canvas.addEventListener('pointercancel',event=>finish(event,true));
  canvas.addEventListener('lostpointercapture',event=>finish(event,true));
  canvas.addEventListener('pointerleave',()=>{if(pointerId===null){director.gaze={x:0,y:0};$('touch-hint').hidden=true;}});
  canvas.addEventListener('keydown',event=>{
    const action={'1':'head','2':'cheek','3':'hand','4':'stretch'}[event.key];if(action){event.preventDefault();react(action);}
    if(event.key==='Escape'){director.end(true);pointerId=null;director.gaze={x:0,y:0};}
  });
}
function isDropTarget(point){const b=profile.bounds;return point.x>=b[0]-.025&&point.x<=b[2]+.025&&point.y>=b[1]&&point.y<=Math.min(b[3],profile.nearBounds[3]);}
function setupProps(){
  document.querySelectorAll('[data-prop]').forEach(button=>{
    button.addEventListener('pointerdown',event=>{
      if(!state.model||!event.isPrimary||event.button!==0||propDrag)return;
      propDrag={kind:button.dataset.prop,id:event.pointerId,startX:event.clientX,startY:event.clientY,moved:false};
      button.setPointerCapture(event.pointerId);speech.unlock().catch(()=>{});director.activity();
    });
    button.addEventListener('pointermove',event=>{
      if(!propDrag||propDrag.id!==event.pointerId)return;
      propDrag.moved ||= Math.hypot(event.clientX-propDrag.startX,event.clientY-propDrag.startY)>8;if(!propDrag.moved)return;
      const b=$('stage').getBoundingClientRect();$('prop-ghost').hidden=false;$('prop-ghost').replaceChildren(propArtwork(propDrag.kind));
      $('prop-ghost').style.left=(event.clientX-b.left)+'px';$('prop-ghost').style.top=(event.clientY-b.top)+'px';
      $('drop-target').hidden=false;const a=sourceToStage({x:profile.bounds[0],y:profile.bounds[1]}),c=sourceToStage({x:profile.bounds[2],y:profile.nearBounds[3]});
      Object.assign($('drop-target').style,{left:a.x+'px',top:a.y+'px',width:c.x-a.x+'px',height:c.y-a.y+'px'});
      $('drop-target').classList.toggle('active',isDropTarget(pointOnModel(event)));
    });
    const finish=(event,cancelled=false)=>{
      if(!propDrag||event.pointerId!==propDrag.id)return;const {kind,moved}=propDrag;propDrag=null;$('prop-ghost').hidden=true;$('drop-target').hidden=true;
      if(button.hasPointerCapture(event.pointerId))button.releasePointerCapture(event.pointerId);
      if(!cancelled&&(!moved||isDropTarget(pointOnModel(event))))react(kind);
      else if(!cancelled)$('interaction-tip').textContent='拖到角色身上再松开，就能递给他';
    };
    button.addEventListener('pointerup',event=>finish(event));button.addEventListener('pointercancel',event=>finish(event,true));button.addEventListener('lostpointercapture',event=>finish(event,true));
    button.addEventListener('click',event=>{if(event.detail===0)react(button.dataset.prop);});
  });
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
  openChat();director.activity();state.busy=true;activeRequest++;const request=activeRequest;controller=new AbortController();
  $('send').disabled=true;$('message').disabled=true;$('suggestions').hidden=true;notice();speech.stop();speech.unlock().catch(()=>{});
  status('正在想怎么回答');if(!retryRow)message('user',text);else retryRow.remove();
  const reply=message('assistant','正在想…',true);let received='',spoken=0,done=false;
  const command=[[/眨.*眼|wink/i,'wink'],[/点.*头/,'nod'],[/招呼|挥.*手/,'wave'],[/摸.*头/,'head'],[/戳.*脸/,'cheek'],[/拍.*肩/,'shoulder'],[/拉.*手|碰.*手/,'hand'],[/递.*茶|给你.*茶/,'tea'],[/送.*礼物/,'gift'],[/活动一下|伸.*懒腰/,'stretch']].find(([pattern])=>pattern.test(text));
  if(command&&!/不要|别|不用|不想|停止|取消/.test(text))react(command[1],false);
  try{
    const interaction=interactionContext&&performance.now()-interactionContext.at<60000?interactionContext.kind:undefined;
    const response=await api('/api/chat',{text,requestId,interaction},controller.signal);const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
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
        feedback(director.trigger(/开心|高兴|太好了/.test(event.message.content)?'wave':'nod',{quiet:true}),{speak:false});
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
  }finally{if(request===activeRequest){state.busy=false;$('send').disabled=false;$('message').disabled=false;$('message').focus({preventScroll:true});if(!state.speaking&&!speech.pending)status('在这里，听你说');}}
}

function setName(name){state.name=name;$('name').textContent=name;$('profile-name').value=name;$('dialogue-name').textContent=name;document.title=name+' · 陪伴时刻';}
function fit(){
  if(!state.model||!profile)return;const parent=$('stage'),w=parent.clientWidth,h=parent.clientHeight;state.app.renderer.resize(w,h);
  const [x1,y1,x2,y2]=state.zoom?profile.nearBounds:profile.bounds,im=state.model.internalModel;
  const availableW=w*(w<700?.73:.64),availableH=h-(w<700?135:142);
  const scale=Math.min(availableW/((x2-x1)*im.originalWidth),availableH/((y2-y1)*im.originalHeight))*.94;
  viewport={x:w/2-(x1+x2)/2*im.originalWidth*scale,y:24-y1*im.originalHeight*scale,scale};
  state.model.scale.set(scale);state.model.position.set(viewport.x,viewport.y);refreshZones();
}
function pose(){
  state.audioLevel=speech.level();state.mouth+=(state.audioLevel-state.mouth)*(state.audioLevel>state.mouth?.58:.24);
  if(!state.speaking&&state.mouth<.005)state.mouth=0;
  const result=director.tick({mouth:state.mouth,speaking:state.speaking,busy:state.busy||recording.busy||document.hidden});
  if(result.event)feedback(result.event,{speak:!result.event.quiet});
  state.model.position.set(viewport.x+result.offset.x*20,viewport.y+result.offset.y*6);
  updateTouchBounds();
  if(showZones)refreshZones();
  if(heldProp){const hand=sourceToStage(zoneCenter('hand-r'));$('held-prop').style.left=hand.x+'px';$('held-prop').style.top=hand.y+'px';
    if(performance.now()>heldProp.until){heldProp=null;$('held-prop').classList.remove('show');setTimeout(()=>{if(!heldProp)$('held-prop').hidden=true;},300);}}
  frameNumber++;
  if(frameNumber%8===0){
    $('avatar').dataset.motion=result.kind;$('avatar').dataset.mouth=state.mouth.toFixed(3);
    $('avatar').dataset.pose=JSON.stringify(result.values);$('avatar').dataset.speaking=String(state.speaking);
    $('avatar').dataset.audioPeak=diagnostic.maxAudioLevel.toFixed(3);$('avatar').dataset.audioFrames=diagnostic.speakingFrames;
  }
  return result.values;
}
async function initialize(){
  const info=await(await api('/api/state')).json();setName(info.name);state.speechAvailable=info.speechAvailable;
  profile=info.interactionProfile;
  state.asrAvailable=info.asrAvailable&&!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined';
  $('microphone').hidden=!state.asrAvailable;
  $('privacy').textContent='聊天内容会发给已配置的模型服务生成回复；'+(info.speechProvider==='tencent'?'回复文字还会发送至腾讯云语音合成（'+info.voice+'）。':'朗读使用系统中文语音。')+(info.asrAvailable?'麦克风录音经你同意后发送至腾讯云识别；本机不保存原始录音。':'')+'聊天记录与回复语音保存在这台电脑，没有克隆照片人物的声音。';
  $('voice').disabled=!info.speechAvailable;$('voice').checked=info.speechAvailable;
  if(!info.speechAvailable)notice('这台电脑暂不支持语音朗读，文字聊天和动作可以使用。');
  renderHistory(info.messages);
  state.app=new PIXI.Application({view:$('avatar'),backgroundAlpha:0,antialias:true,resolution:Math.min(devicePixelRatio,2),autoDensity:true});
  state.model=await PIXI.live2d.Live2DModel.from(info.modelPath,{autoInteract:false});state.model.anchor.set(0,0);state.app.stage.addChild(state.model);fit();
  state.model.internalModel.on('beforeModelUpdate',()=>{const values=pose();diagnostic.lastPose=values;for(const [id,v] of Object.entries(values))state.model.internalModel.coreModel.setParameterValueById(id,v);});
  setupTouches();setupProps();new ResizeObserver(()=>fit()).observe($('stage'));
  diagnostic.ready=true;$('avatar').dataset.ready='true';status('在这里，等你打招呼');bubble('嗨，我是'+state.name+'。试着摸摸头，或拉一拉我的手。');
  feedback(director.trigger('welcome',{quiet:true}),{speak:false});
  if(!info.messages.length){$('reaction-label').textContent='见面时刻';bubble('嗨，我是'+state.name+'。试着摸摸头，或拉一拉我的手。');}
}

$('composer').addEventListener('submit',event=>{event.preventDefault();const value=$('message').value;if(!value.trim())return;$('message').value='';send(value);});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>react(button.dataset.action)));
document.querySelectorAll('#suggestions button').forEach(button=>button.addEventListener('click',()=>send(button.textContent)));
$('zoom').addEventListener('click',()=>{state.zoom=!state.zoom;$('zoom').querySelector('b').textContent=state.zoom?'看全身':'看近一点';director.end(true);pointerId=null;fit();});
$('chat-open').addEventListener('click',()=>openChat(true));$('chat-close').addEventListener('click',()=>openChat(false));
$('zones-toggle').addEventListener('click',()=>{showZones=!showZones;$('touch-zones').hidden=!showZones;$('zones-toggle').setAttribute('aria-pressed',String(showZones));refreshZones();});
$('guide-open').addEventListener('click',()=>$('guide').showModal());$('guide-close').addEventListener('click',()=>$('guide').close());
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
document.addEventListener('visibilitychange',()=>{if(document.hidden){hiddenAt=performance.now();director.end(true);pointerId=null;director.gaze={x:0,y:0};}else if(hiddenAt&&performance.now()-hiddenAt>12000){feedback(director.trigger('welcome',{quiet:true}),{speak:false});hiddenAt=0;}});
addEventListener('resize',()=>{fit();scrollToLatest();});addEventListener('pagehide',()=>{controller?.abort();cancelRecording();speech.stop();});
initialize().catch(()=>{notice('形象暂时没加载成功，请刷新页面。');status('加载遇到问题');});
