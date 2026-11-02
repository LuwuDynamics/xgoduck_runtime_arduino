/* Run real UI/i18n scripts with an in-memory DOM and cloud; never contacts hardware. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assets=path.join(__dirname,'../assets');
class Element extends EventTarget {
  constructor(){super();this.open=false;this.textContent='';this.dataset={};this.attributes={};this.disabled=false;this.checked=false;}
  setAttribute(k,v){this.attributes[k]=v;}
  showModal(){this.open=true;}
  close(){this.open=false;this.dispatchEvent(new Event('close'));}
  click(){this.dispatchEvent(new Event('click'));}
  change(){this.dispatchEvent(new Event('change'));}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
  const elements=Object.fromEntries(['openBinding','bindingDialog','closeBinding','bindingRegion','bindingStatus','regionCn','regionGlobal','regionCnCard','regionGlobalCard','applyRegion','regionMessage','bindingRegionChip','language'].map(id=>[id,new Element()]));
  const document=Object.assign(new EventTarget(),{hidden:false,documentElement:{},getElementById:id=>elements[id],querySelectorAll:()=>[]});
  const window=new EventTarget(),stored=new Map(),requests=[];
  const state={config:{cloud_region:'cn',custom_endpoint:false,configured:true},voice:{onboarding:{cloud_region:'cn',bound:true},cloud_connected:true,session_cloud_region:'cn'}};
  let override=null;
  const ctx=vm.createContext({window,document,navigator:{language:'zh-CN'},localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},CustomEvent:class extends Event{},AbortController,setTimeout,clearTimeout,
    setInterval:(f,n)=>{const timer=setInterval(f,n);timer.unref();return timer;},clearInterval,
    fetch:(url,options)=>{
      requests.push({url,options});if(override)return override(url,options);
      if(options.method==='POST'){
        const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['cloud_region']);
        const changed=state.config.cloud_region!==body.cloud_region||state.config.custom_endpoint;
        state.config={cloud_region:body.cloud_region,custom_endpoint:false,configured:true};
        return Promise.resolve({ok:true,json:async()=>({...state.config,changed,reconnecting:changed})});
      }
      const value=structuredClone(url==='/api/voice/region'?state.config:state.voice);
      return Promise.resolve({ok:true,json:async()=>value});
    }});
  for(const name of ['mobile-i18n.js','mobile-binding.js'])vm.runInContext(fs.readFileSync(path.join(assets,name),'utf8'),ctx);
  return {elements,window,document,requests,state,override:value=>override=value};
}
(async()=>{
  const s=setup(),e=s.elements;
  assert(s.requests.every(r=>!r.options.method));await tick();assert.equal(e.bindingRegionChip.textContent,'国内');e.openBinding.click();await tick();
  assert.equal(e.bindingRegion.textContent,'国内版');assert.equal(e.bindingStatus.textContent,'已绑定 · 语音在线');
  assert(e.regionCn.checked);assert(e.applyRegion.disabled);assert(s.requests.every(r=>!r.options.method));
  s.window.DuckI18n.setLanguage('en');assert.equal(e.bindingRegion.textContent,'China');
  assert(s.requests.every(r=>!r.options.method),'UI language must not switch servers.');
  e.regionGlobal.change();assert.equal(e.bindingRegion.textContent,'China');assert.equal(s.state.config.cloud_region,'cn');
  assert.equal(s.requests.filter(r=>r.options.method==='POST').length,0,'Selection alone must not write config.');
  assert.equal(e.applyRegion.disabled,false);e.applyRegion.click();e.applyRegion.click();await tick();
  const writes=s.requests.filter(r=>r.options.method==='POST');assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/api/voice/region');assert.equal(writes[0].options.headers['X-Device-Test'],'1');
  assert.deepEqual(JSON.parse(writes[0].options.body),{cloud_region:'global'});
  assert.equal(e.bindingRegion.textContent,'International');assert.notEqual(e.bindingStatus.dataset.state,'online','Old China connection must not be presented as the new server being online.');
  s.state.voice={onboarding:{cloud_region:'global',bound:false},cloud_connected:false,session_cloud_region:null};
  s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'unbound');
  s.state.voice={onboarding:{cloud_region:'global',bound:true},cloud_connected:true,session_cloud_region:'global'};
  s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'online');
  e.regionCn.change();e.closeBinding.click();const count=s.requests.length;s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(s.requests.filter(r=>r.options.method==='POST').length,1);
  e.openBinding.click();await tick();assert.equal(e.regionGlobal.checked,true,'Reopening must use the saved region, not an abandoned draft.');
  s.override(()=>Promise.resolve({ok:false}));s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'unavailable');e.closeBinding.click();
  const u=setup();u.state.config={cloud_region:null,custom_endpoint:false,configured:false};u.state.voice={cloud_connected:false};u.elements.openBinding.click();await tick();
  assert.equal(u.elements.bindingRegion.textContent,'尚未选择');u.elements.regionCn.change();u.elements.applyRegion.click();await tick();assert.equal(u.state.config.cloud_region,'cn');u.elements.closeBinding.click();
  const custom=setup();custom.state.config.custom_endpoint=true;custom.elements.openBinding.click();await tick();assert.equal(custom.elements.bindingRegion.textContent,'自定义服务器');custom.elements.regionCn.change();assert.equal(custom.elements.applyRegion.disabled,false);custom.elements.applyRegion.click();await tick();assert.equal(custom.state.config.custom_endpoint,false);custom.elements.closeBinding.click();
  const r=setup();let complete;
  r.override((url)=>url==='/api/voice/status'?new Promise(resolve=>complete=resolve):Promise.resolve({ok:true,json:async()=>r.state.config}));
  r.elements.openBinding.click();r.elements.closeBinding.click();complete({ok:true,json:async()=>r.state.voice});await tick();assert.equal(r.elements.bindingStatus.dataset.state,'pending');
  const html=fs.readFileSync(path.join(assets,'mobile.html'),'utf8');
  for(const url of ['https://agent.xgorobot.com/devices','https://agent.luwudynamics.ai/'])assert(html.includes(`href="${url}" target="_blank" rel="noopener noreferrer"`));
  console.log('PASS: current/draft regions, explicit apply, no automatic writes, exact links, preserved language, stale connection isolation, idempotency, first setup, custom URL and request cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
