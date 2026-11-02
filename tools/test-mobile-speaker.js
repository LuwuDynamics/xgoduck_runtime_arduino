/* Real cockpit scripts with a simulated media worker; no browser or hardware. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assets=path.join(__dirname,'../assets'),tick=()=>new Promise(resolve=>setImmediate(resolve));
class Element extends EventTarget {
  constructor(){super();this.attributes={};this.dataset={};this.style={setProperty(){}};this.value='100';this.offsetHeight=64;const names=new Set();this.classList={contains:n=>names.has(n),add:n=>names.add(n),remove:n=>names.delete(n),toggle:(n,on)=>{if(on??!names.has(n))names.add(n);else names.delete(n);return names.has(n);}};}
  setAttribute(k,v){this.attributes[k]=v;} removeAttribute(k){delete this.attributes[k];} append(){}
}
function setup(){
  const ids=[...fs.readFileSync(path.join(assets,'mobile.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
  const el=Object.fromEntries(ids.map(id=>[id,new Element()])),window=new EventTarget(),polls=[],requests=[];
  const document=Object.assign(new EventTarget(),{hidden:false,documentElement:{},body:new Element(),getElementById:id=>el[id],querySelectorAll:()=>[],querySelector:()=>new Element(),createElement:()=>new Element(),createTextNode:t=>t});
  const state={camera:{available:true,running:false,ready:false,light:{supported:true,enabled:false}},audio:{available:true,volume:75,state:'idle',error:''}};
  let postOverride=null,readOverride=null;
  const fetch=async(url,options={})=>{
    requests.push({url,options});
    if(options.method==='POST'){
      if(postOverride)return postOverride(url,options);
      if(url==='/api/media/audio/play')state.audio.state='playing';
      if(url==='/api/media/audio/stop')state.audio.state='idle';
      return {ok:true,json:async()=>structuredClone(state)};
    }
    if(url==='/api/media/status'&&readOverride)return readOverride();
    const data=url==='/api/media/status'?state:url==='/api/status'?{mode:'shadow',enabled:false,feedback_hz:100}:{usb:{available:true,host:true},input:{connected:false}};
    return {ok:true,json:async()=>structuredClone(data)};
  };
  const context=vm.createContext({window,document,navigator:{language:'en-US'},location:{href:'http://duck.test/'},URL,fetch,AbortSignal,AbortController,CustomEvent:class extends Event{},setTimeout:(fn,ms)=>{if(ms===1500)polls.push(fn);return 1;},clearTimeout(){}});
  for(const name of ['mobile-i18n.js','mobile.js'])vm.runInContext(fs.readFileSync(path.join(assets,name),'utf8'),context);
  return {el,window,state,requests,poll:async()=>{const fn=polls.shift();assert(fn);await fn();},post:fn=>postOverride=fn,read:fn=>readOverride=fn};
}
(async()=>{
  const s=setup();await tick();const e=s.el;
  assert.equal(s.requests.filter(r=>r.options.method==='POST').length,0,'Opening the page never starts playback.');
  assert.equal(e.speakerTest.disabled,false);
  let release;s.post(()=>new Promise(resolve=>release=resolve));
  const start=e.speakerTest.onclick();assert(e.speakerTest.disabled&&e.listen.disabled);assert.equal(e.speakerLabel.textContent,'Preparing…');
  await e.speakerTest.onclick();assert.equal(s.requests.filter(r=>r.options.method==='POST').length,1,'Repeated clicks must not overlap playback.');
  const write=s.requests.at(-1);assert.equal(write.url,'/api/media/audio/play');assert.equal(write.options.headers['X-Device-Test'],'1');assert.deepEqual(JSON.parse(write.options.body),{source:'duck'});
  s.state.audio.state='playing';release({ok:true,json:async()=>structuredClone(s.state)});await start;s.post(null);
  assert.equal(e.speakerLabel.textContent,'Stop playback');assert.equal(e.speakerTest.disabled,false);assert(e.listen.disabled);
  s.window.DuckI18n.setLanguage('zh');assert.equal(e.speakerLabel.textContent,'停止播放');
  await e.speakerTest.onclick();assert.equal(s.requests.at(-1).url,'/api/media/audio/stop');assert.equal(e.speakerLabel.textContent,'测试扬声器');assert.equal(e.voiceState.textContent,'扬声器测试已结束');assert.equal(e.listen.disabled,false);
  await e.speakerTest.onclick();s.state.audio.state='idle';await s.poll();assert.equal(e.speakerTest.attributes['aria-pressed'],'false');assert.equal(e.voiceState.textContent,'扬声器测试已结束');
  s.post(async()=>({ok:false,json:async()=>({error:'USB speaker unavailable'})}));await e.speakerTest.onclick();assert.equal(e.speakerTest.disabled,false);assert.equal(e.notice.textContent,'USB speaker unavailable');assert.equal(e.voiceState.textContent,'扬声器测试失败，请重试。');s.post(null);
  let stale;s.read(()=>new Promise(resolve=>stale=resolve));const poll=s.poll();await tick();
  const old=structuredClone(s.state);await e.speakerTest.onclick();stale({ok:true,json:async()=>old});await poll;s.read(null);
  assert.equal(e.speakerTest.attributes['aria-pressed'],'true','A stale status response must not clear a newly started playback.');
  await e.speakerTest.onclick();s.state.audio.available=false;await s.poll();assert(e.speakerTest.disabled,'Missing speaker disables its test.');
  assert(!s.requests.some(r=>r.url==='/api/media/upload'),'Speaker checks preserve uploaded audio.');
  assert(s.requests.filter(r=>r.options.method==='POST').every(r=>['/api/media/audio/play','/api/media/audio/stop'].includes(r.url)),'Speaker checks never command motion or change volume.');
  console.log('PASS: speaker play/stop/finish, prepare and device state, repeated clicks, error recovery, stale polling, zh/en labels, no upload overwrite, volume or motion commands.');
})().catch(error=>{console.error(error);process.exitCode=1;});
