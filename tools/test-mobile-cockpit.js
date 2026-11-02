/* Browser regression with simulated hardware; never connects to a robot. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.join(__dirname,'../assets'),origin='http://duck-mobile.test';
const output=process.env.DUCK_UI_SCREENSHOTS;
const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');

(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const context=await browser.newContext({locale:'zh-CN',viewport:{width:390,height:844}});
    const page=await context.newPage(),errors=[],writes=[];
    page.on('pageerror',error=>errors.push(error.message));
    const media={camera:{available:true,running:false,ready:false,device:'/dev/video2',frames:0,fps:0,error:'',light:{supported:true,enabled:false,error:''}},audio:{available:true,volume:100,state:'idle'}};
    const tof={state:'ranging',fresh:true,age_ms:10,seq:7,frequency_hz:5,center_mm:750,min_mm:120,
      grid_mm:Array.from({length:8},(_,y)=>Array.from({length:8},(_,x)=>y+x===0?null:120+(y*8+x)*30))};
    let tofHTTP=200,tofDelay=0;
    let holdNext=false,releaseStatus,capturedStatus;
    await page.addInitScript(()=>{
      window.__phoneMicCalls=0;window.__liveOpened=0;window.__liveClosed=0;
      Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{window.__phoneMicCalls++;throw Error('Phone capture must not be requested.');}}});
      const original=window.fetch.bind(window);
      window.fetch=(url,options={})=>{
        if(String(url)!=='/api/media/audio/stream')return original(url,options);
        window.__liveOpened++;
        let timer,closed=false;
        const finish=()=>{if(!closed){closed=true;window.__liveClosed++;clearInterval(timer);}};
        const stream=new ReadableStream({start(controller){
          timer=setInterval(()=>controller.enqueue(new Uint8Array(960)),20);
          options.signal.addEventListener('abort',()=>{finish();controller.error(new DOMException('Aborted','AbortError'));},{once:true});
        },cancel(){finish();}});
        return Promise.resolve(new Response(stream,{headers:{'Content-Type':'application/octet-stream'}}));
      };
    });
    await page.route(origin+'/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname.startsWith('/api/')){
        if(request.method()==='POST'){
          const body=request.postDataJSON();writes.push({path:url.pathname,body});
          if(url.pathname==='/api/media/camera/light')media.camera.light.enabled=body.enabled;
          else if(url.pathname==='/api/media/camera/start')Object.assign(media.camera,{running:true,ready:true,fps:30});
          else if(url.pathname==='/api/media/camera/stop')Object.assign(media.camera,{running:false,ready:false});
          return route.fulfill({json:media});
        }
        if(url.pathname==='/api/status')return route.fulfill({json:{mode:'shadow',enabled:false,feedback_hz:100}});
        if(url.pathname==='/api/tof/status'){
          const snapshot={...tof,seq:++tof.seq};
          if(tofDelay)await new Promise(resolve=>setTimeout(resolve,tofDelay));
          return route.fulfill({status:tofHTTP,json:snapshot});
        }
        if(url.pathname==='/api/gamepad/status')return route.fulfill({json:{input:{connected:false},control:{enabled:false},usb:{available:true,host:true}}});
        if(url.pathname==='/api/media/status'){
          const snapshot=structuredClone(media);
          if(holdNext){holdNext=false;capturedStatus();await new Promise(resolve=>releaseStatus=resolve);}
          return route.fulfill({json:snapshot});
        }
        if(url.pathname==='/api/media/camera/stream')return route.fulfill({contentType:'image/png',body:pixel});
        return route.fulfill({status:404,json:{error:'Not found'}});
      }
      const name=url.pathname==='/'?'mobile.html':url.pathname.slice(1),file=path.join(root,name);
      if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
      const type=name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':name.endsWith('.html')?'text/html':'application/json';
      return route.fulfill({contentType:type,body:fs.readFileSync(file)});
    });
    await page.goto(origin+'/');
    assert.equal(page.url(),origin+'/');
    await page.getByRole('button',{name:'开启摄像头',exact:true}).waitFor();
    await page.waitForFunction(()=>!document.getElementById('startCamera').disabled);
    assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
    assert.equal(new URL(await page.locator('.brand').getAttribute('href')).port,'9527');
    assert.equal(await page.locator('#talk,#micSetup,#prepareMic,#flagAddress').count(),0);
    assert.equal(writes.length,0,'Opening the cockpit must not send hardware commands.');
    await page.waitForFunction(()=>document.getElementById('tofCenter').textContent==='75.0');
    assert.equal(await page.locator('.tof-cell').count(),64);
    assert.equal(await page.locator('.tof-cell.is-valid').count(),63);
    assert.equal(await page.locator('#tofNearest').innerText(),'12.0');
    assert.equal(await page.locator('#welcome').isHidden(),true);
    await page.locator('#language').click();
    assert.equal(await page.locator('#startCamera').innerText(),'Start camera');
    await page.reload();await page.getByRole('button',{name:'Start camera',exact:true}).waitFor();
    assert.equal(await page.locator('html').getAttribute('lang'),'en');
    await page.locator('#settings').click();await page.getByRole('heading',{name:'Speaker',exact:true}).waitFor();
    assert.equal(await page.getByText('Phone microphone',{exact:true}).count(),0);
    await page.locator('#closeConfig').click();assert.equal(writes.length,0);

    const captured=new Promise(resolve=>capturedStatus=resolve);holdNext=true;
    await Promise.race([captured,new Promise((_,reject)=>setTimeout(()=>reject(Error('No status poll')),4000))]);
    await page.locator('#lightOn').click();await page.getByText('Light on',{exact:true}).waitFor();
    releaseStatus();await page.waitForTimeout(100);
    assert.equal(await page.locator('#lightState').innerText(),'Light on','An older poll must not undo a completed command.');
    await page.locator('#startCamera').click();await page.waitForFunction(()=>!document.getElementById('feed').hidden);
    assert.equal(await page.locator('#stopCamera').isDisabled(),false);
    await page.locator('#lightOff').click();await page.getByText('Light off',{exact:true}).waitFor();
    assert.equal(media.camera.running,true,'Switching the lamp must preserve video.');
    await page.locator('#stopCamera').click();await page.waitForFunction(()=>document.getElementById('feed').hidden);
    await page.locator('#tofToggle').click();await page.waitForFunction(()=>!document.getElementById('welcome').hidden);
    assert.equal(await page.locator('#tofBody').isHidden(),true);
    await page.locator('#tofToggle').click();
    assert.deepEqual(writes.map(x=>[x.path,x.body]),[
      ['/api/media/camera/light',{enabled:true}],['/api/media/camera/start',{}],
      ['/api/media/camera/light',{enabled:false}],['/api/media/camera/stop',{}]
    ]);

    await page.locator('#listen').click();await page.getByRole('button',{name:/Stop listening/}).waitFor();
    await page.locator('#language').click();await page.getByRole('button',{name:/停止监听/}).waitFor();
    await page.locator('#listen').click();
    await page.waitForFunction(()=>window.__liveClosed===1);
    assert.equal(await page.evaluate(()=>window.__phoneMicCalls),0);
    assert.equal(await page.evaluate(()=>window.__liveOpened),1);
    assert.equal(await page.locator('#voiceState').innerText(),'实时监听已停止');

    await page.locator('#full').click();await page.waitForFunction(()=>!!document.fullscreenElement);
    assert.equal(await page.locator('#fullLabel').innerText(),'退出全屏');
    await page.locator('#language').click();assert.equal(await page.locator('#fullLabel').innerText(),'Exit fullscreen');
    await page.locator('#full').click();await page.waitForFunction(()=>!document.fullscreenElement);

    // Stale/absent/missing-RPC/network data must never look like a live zero.
    for(const [state,fresh,age,expected] of [['ranging',true,1500,'stale'],['absent',false,0,'absent'],['unavailable',false,0,'unavailable']]){
      Object.assign(tof,{state,fresh,age_ms:age,error_message:state==='unavailable'?'method duck_tof not available':''});
      await page.waitForFunction(expected=>document.getElementById('tofPanel').dataset.state===expected,expected);
      assert.equal(await page.locator('#tofCenter').innerText(),'—');
      assert.equal(await page.locator('.tof-cell.is-valid').count(),0);
    }
    tofHTTP=503;await page.waitForFunction(()=>document.getElementById('tofPanel').dataset.state==='disconnected');
    tofHTTP=200;Object.assign(tof,{state:'ranging',fresh:true,age_ms:10,error_message:''});
    await page.waitForFunction(()=>document.getElementById('tofCenter').textContent==='75.0');
    tofDelay=1100;await page.waitForTimeout(1600);
    assert.equal(await page.locator('#tofPanel').getAttribute('data-state'),'stale','Delayed HTTP replies cannot refresh old measurements.');
    assert.equal(await page.locator('#tofCenter').innerText(),'—');
    tofDelay=0;await page.waitForFunction(()=>document.getElementById('tofCenter').textContent==='75.0');
    await page.locator('#clean').click();assert.equal(await page.locator('.tof-layer').isVisible(),false);
    await page.locator('#restoreHud').click();assert.equal(await page.locator('.tof-layer').isVisible(),true);

    if(output)fs.mkdirSync(output,{recursive:true});
    for(const language of ['zh','en']){
      await page.evaluate(language=>window.DuckI18n.setLanguage(language),language);
      for(const [name,width,height] of [['portrait',390,844],['compact',320,568],['landscape',844,390],['desktop',1280,800]]){
        await page.setViewportSize({width,height});await page.waitForTimeout(100);
        const boxes=await page.evaluate(()=>['startCamera','stopCamera','lightOn','lightOff','snapshot','clean','language','listen','torqueOff'].map(id=>({id,...document.getElementById(id).getBoundingClientRect().toJSON()})));
        for(const box of boxes)assert(box.x>=0&&box.y>=0&&box.right<=width+1&&box.bottom<=height+1,`${language}/${name}: ${box.id} off screen`);
        const bounds=await page.evaluate(()=>({panel:document.getElementById('tofPanel').getBoundingClientRect().toJSON(),top:document.querySelector('.top').getBoundingClientRect().bottom,bottom:document.querySelector('.bottom').getBoundingClientRect().top,values:['tofCenter','tofNearest'].map(id=>document.getElementById(id).getBoundingClientRect().toJSON())}));
        assert(bounds.panel.top>=bounds.top-1&&bounds.panel.bottom<=bounds.bottom+1,`${language}/${name}: ToF overlaps controls`);
        for(const value of bounds.values)assert(value.top>=bounds.panel.top&&value.bottom<=bounds.panel.bottom,`${language}/${name}: ToF distance clipped`);
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${language}/${name}: horizontal overflow`);
        if(output)await page.screenshot({path:path.join(output,language+'-'+name+'.png')});
      }
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: zh/en and persistence; camera/light; live audio cleanup; fullscreen; 64-zone ToF, live/stale/absent/RPC/network recovery, collapse/clean view; eight viewport/language layouts.');
    await context.close();
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
