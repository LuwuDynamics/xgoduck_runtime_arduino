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
function setup({locale='zh-CN',languages,savedLanguage}={}){
  const elements=Object.fromEntries(['openBinding','bindingDialog','closeBinding','bindingRegion','bindingStatus','regionCn','regionGlobal','regionCnCard','regionGlobalCard','regionMessage','bindingRegionChip','language'].map(id=>[id,new Element()]));
  const document=Object.assign(new EventTarget(),{hidden:false,documentElement:{},getElementById:id=>elements[id],querySelectorAll:()=>[]});
  const window=new EventTarget(),stored=new Map(savedLanguage?[['duck-cockpit-language',savedLanguage]]:[]),requests=[];
  const navigator={language:locale,languages};
  const state={config:{cloud_region:'cn',custom_endpoint:false,configured:true},voice:{onboarding:{cloud_region:'cn',bound:true},cloud_connected:true,session_cloud_region:'cn'}};
  let override=null;
  const ctx=vm.createContext({window,document,navigator,localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},CustomEvent:class extends Event{},AbortController,setTimeout,clearTimeout,
    setInterval:(f,n)=>{const timer=setInterval(f,n);timer.unref();return timer;},clearInterval,
    fetch:(url,options)=>{
      requests.push({url,options});if(override){const result=override(url,options);if(result!==undefined)return result;}
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
  return {elements,window,document,navigator,stored,requests,state,override:value=>override=value};
}
(async()=>{
  for(const [locale,languages,savedLanguage,expected] of [
    ['zh-CN',undefined,'en','zh'],['zh-TW',undefined,'en','zh'],['zh-HK',undefined,null,'zh'],
    ['zh-Hans-CN',undefined,null,'zh'],['zh',undefined,null,'zh'],['zh_CN',undefined,null,'zh'],
    ['en-US',undefined,'zh','en'],['fr-FR',undefined,'zh','en'],['ja-JP',undefined,null,'en'],
    ['en-US',['zh-Hant-TW','en-US'],'en','zh'],['zh-CN',['en-US','zh-CN'],'zh','en'],
    ['zh-CN',[],null,'zh'],['',[],null,'en']
  ]){
    const localeUI=setup({locale,languages,savedLanguage});await tick();
    assert.equal(localeUI.window.DuckI18n.language,expected,`System language ${locale}/${languages} takes precedence over an old preference.`);
    assert.equal(localeUI.document.documentElement.lang,expected==='zh'?'zh-CN':'en');
    localeUI.window.DuckI18n.setLanguage(expected==='zh'?'en':'zh');
    assert.equal(localeUI.stored.get('duck-cockpit-language'),savedLanguage||undefined,'Manual changes are temporary and must not persist.');
    localeUI.navigator.languages=['zh-CN'];localeUI.window.dispatchEvent(new Event('languagechange'));
    assert.equal(localeUI.window.DuckI18n.language,'zh');
    localeUI.navigator.languages=['de-DE','zh-CN'];localeUI.window.dispatchEvent(new Event('languagechange'));
    assert.equal(localeUI.window.DuckI18n.language,'en');
    assert(localeUI.requests.every(r=>!r.options.method),'Following system language must not change the cloud region.');
  }
  const s=setup(),e=s.elements;
  assert(s.requests.every(r=>!r.options.method));await tick();assert.equal(e.bindingRegionChip.textContent,'国内');e.openBinding.click();await tick();
  assert.equal(e.bindingRegion.textContent,'国内版');assert.equal(e.bindingStatus.textContent,'已绑定 · 语音在线');
  assert(e.regionCn.checked);assert(!e.regionCn.disabled);assert(s.requests.every(r=>!r.options.method));
  s.window.DuckI18n.setLanguage('en');assert.equal(e.bindingRegion.textContent,'China');
  assert(s.requests.every(r=>!r.options.method),'UI language must not switch servers.');
  e.regionCn.change();assert(s.requests.every(r=>!r.options.method),'Selecting the current server must not reconnect.');
  e.regionGlobal.change();assert.equal(e.bindingRegion.textContent,'China','The current region changes only after the server acknowledges the save.');
  assert.equal(s.requests.filter(r=>r.options.method==='POST').length,1,'Selection must immediately save the server.');
  assert(e.regionCn.disabled&&e.regionGlobal.disabled);e.regionGlobal.change();e.regionCn.change();await tick();
  const writes=s.requests.filter(r=>r.options.method==='POST');assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/api/voice/region');assert.equal(writes[0].options.headers['X-Device-Test'],'1');
  assert.deepEqual(JSON.parse(writes[0].options.body),{cloud_region:'global'});
  assert.equal(e.bindingRegion.textContent,'International');assert.notEqual(e.bindingStatus.dataset.state,'online','Old China connection must not be presented as the new server being online.');
  s.state.voice={onboarding:{cloud_region:'global',bound:false},cloud_connected:false,session_cloud_region:null};
  s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'unbound');
  s.state.voice={onboarding:{cloud_region:'global',bound:true},cloud_connected:true,session_cloud_region:'global'};
  s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'online');
  e.regionCn.change();e.closeBinding.click();s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(s.requests.filter(r=>r.options.method==='POST').length,2);
  e.openBinding.click();await tick();assert.equal(e.regionCn.checked,true,'Closing the dialog must not cancel a server selection.');
  s.override(()=>Promise.resolve({ok:false}));s.window.dispatchEvent(new Event('focus'));await tick();assert.equal(e.bindingStatus.dataset.state,'unavailable');e.closeBinding.click();
  const u=setup();u.state.config={cloud_region:null,custom_endpoint:false,configured:false};u.state.voice={cloud_connected:false};u.elements.openBinding.click();await tick();
  assert.equal(u.elements.bindingRegion.textContent,'尚未选择');u.elements.regionCn.change();await tick();assert.equal(u.state.config.cloud_region,'cn');u.elements.closeBinding.click();
  const custom=setup();custom.state.config.custom_endpoint=true;custom.elements.openBinding.click();await tick();assert.equal(custom.elements.bindingRegion.textContent,'自定义服务器');custom.elements.regionCn.change();await tick();assert.equal(custom.state.config.custom_endpoint,false);custom.elements.closeBinding.click();
  const failed=setup();await tick();failed.elements.openBinding.click();await tick();
  failed.override((url,options)=>options.method==='POST'?Promise.resolve({ok:false}):undefined);
  failed.elements.regionGlobal.change();await tick();
  assert.equal(failed.state.config.cloud_region,'cn');assert(failed.elements.regionCn.checked&&!failed.elements.regionGlobal.checked,'A failed save must restore the actual selection.');
  assert.equal(failed.elements.regionGlobalCard.dataset.current,'false');assert(!failed.elements.regionGlobal.disabled);
  assert.match(failed.elements.regionMessage.textContent,/未能确认切换/);failed.elements.closeBinding.click();
  const delayed=setup();await tick();delayed.elements.openBinding.click();await tick();let finish;
  delayed.override((url,options)=>options.method==='POST'?new Promise(resolve=>finish=resolve):undefined);
  delayed.elements.regionGlobal.change();delayed.elements.closeBinding.click();delayed.elements.openBinding.click();
  assert(delayed.elements.regionGlobal.disabled);delayed.elements.regionCn.change();
  assert.equal(delayed.requests.filter(r=>r.options.method==='POST').length,1,'Reopening or clicking during a save must not start another switch.');
  delayed.state.config={cloud_region:'global',custom_endpoint:false,configured:true};finish({ok:true,json:async()=>({...delayed.state.config,changed:true})});await tick();
  assert.equal(delayed.elements.bindingRegion.textContent,'国际版');assert(delayed.elements.regionGlobal.checked);delayed.elements.closeBinding.click();
  const uncertain=setup();await tick();uncertain.elements.openBinding.click();await tick();
  uncertain.override((url,options)=>{if(options.method==='POST'){uncertain.state.config={cloud_region:'global',custom_endpoint:false,configured:true};return Promise.reject(Error('Response lost after saving'));}});
  uncertain.elements.regionGlobal.change();await tick();assert.equal(uncertain.elements.bindingRegion.textContent,'国际版','Reconcile a lost response with the actual persisted region.');assert(uncertain.elements.regionGlobal.checked);uncertain.elements.closeBinding.click();
  const r=setup();let complete;
  r.override((url)=>url==='/api/voice/status'?new Promise(resolve=>complete=resolve):Promise.resolve({ok:true,json:async()=>r.state.config}));
  r.elements.openBinding.click();r.elements.closeBinding.click();complete({ok:true,json:async()=>r.state.voice});await tick();assert.equal(r.elements.bindingStatus.dataset.state,'pending');
  const html=fs.readFileSync(path.join(assets,'mobile.html'),'utf8');
  assert(!html.includes('id="applyRegion"'),'No extra Apply button is required.');
  for(const url of ['https://agent.xgorobot.com/devices','https://agent.luwudynamics.ai/'])assert(html.includes(`href="${url}" target="_blank" rel="noopener noreferrer"`));
  console.log('PASS: 13 system-language cases, temporary manual toggle, system-language changes, immediate region save, duplicate suppression, failure rollback, lost-response reconciliation, close/reopen during save, no language/link writes, stale connection isolation, idempotency, first setup and custom URL.');
})().catch(error=>{console.error(error);process.exitCode=1;});
