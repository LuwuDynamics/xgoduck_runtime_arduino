const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../assets/mobile-fullscreen.js'), 'utf8');
function fixture({legacy=false, unsupported=false}={}) {
  const listeners={}, attrs={}, elements={full:{setAttribute:(k,v)=>attrs[k]=v,addEventListener:(k,f)=>listeners[k]=f},fullLabel:{},notice:{hidden:true}};
  const doc={getElementById:id=>elements[id],addEventListener:(k,f)=>listeners[k]=f,documentElement:{}};
  const key=legacy?'webkitFullscreenElement':'fullscreenElement';
  let calls=0, reject=false;
  if(!unsupported)doc.documentElement[legacy?'webkitRequestFullscreen':'requestFullscreen']=function(options){
    calls++; assert.equal(this,doc.documentElement); assert.equal(options.navigationUI,'hide');
    if(reject)return Promise.reject(Error('Permission denied'));
    doc[key]=this;return Promise.resolve();
  };
  doc[legacy?'webkitExitFullscreen':'exitFullscreen']=async()=>{doc[key]=null;};
  vm.runInNewContext(source,{document:doc,window:{addEventListener:(k,f)=>listeners[k]=f},screen:{orientation:{lock:()=>new Promise(()=>{}),unlock:()=>{}}}});
  return {listeners,attrs,elements,doc,key,get calls(){return calls;},set reject(value){reject=value;}};
}
(async()=>{
  for(const legacy of [false,true]) {
    const f=fixture({legacy});
    const entered=f.listeners.click();
    assert.equal(f.calls,1,'request must run synchronously during the click');
    await entered;
    assert.equal(f.attrs['aria-pressed'],'true');
    await f.listeners.click();
    assert.equal(f.attrs['aria-pressed'],'false','a pending orientation lock must not block exit');
    await f.listeners.click();
    f.doc[f.key]=null;f.listeners[legacy?'webkitfullscreenchange':'fullscreenchange']();
    assert.equal(f.elements.fullLabel.textContent,'Fullscreen','browser-driven exit updates UI');
    f.reject=true;await f.listeners.click();
    assert.match(f.elements.notice.textContent,/Permission denied/);
    assert.equal(f.elements.notice.hidden,false);
    f.reject=false;await f.listeners.click();
    assert.equal(f.attrs['aria-pressed'],'true','retry works after rejection');
  }
  const f=fixture({unsupported:true});await f.listeners.click();
  assert.match(f.elements.notice.textContent,/does not support/);
  console.log('Fullscreen: native and prefixed APIs, user gesture, exit, failure and retry passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
