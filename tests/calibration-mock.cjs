// Local-only calibration simulator. It never opens a robot connection.
// Run: node tests/calibration-mock.cjs [port]
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const MOTORS = [10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
const ASSETS = path.resolve(__dirname, '../assets');

async function createMock(port=0) {
  const state = {mode:'shadow', held:Array(15).fill(false), mask:32767, imu:false,
    calls:[], saved:new Set(), failCenter:null, statusDown:false, beats:0,
    delayStatus:false, releaseStatus:null, delayCenter:false, releaseCenter:null,
    guideFailure:null, imuOk:true, imuStartError:null, imuSampleError:null,
    imuResult:{state:'idle', progress:0, calibrated:false}};
  let imuTimer = null;
  let imuStartedAt = 0;
  function imuSnapshot() {
    const progress = imuTimer ? Math.min((Date.now() - imuStartedAt) / 1500, 1) : state.imuResult.progress;
    return {...state.imuResult, progress, active:state.imu, seconds:1.5};
  }
  const server = http.createServer(async (req,res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    function json(code, body) {
      res.writeHead(code, {'Content-Type':'application/json', 'Cache-Control':'no-store'});
      res.end(JSON.stringify(body));
    }
    try {
      if (req.method === 'GET' && url.pathname === '/api/status') {
        if (state.statusDown) return json(503, {detail:'offline'});
        const snapshot = {mode:state.mode, cal_done:[...state.held], raw_mask:state.mask,
          raw_pos:MOTORS.map((_,i)=>state.held[i]?2048:2200+i),
          enabled:false, imu_ok:state.imuOk, feedback_age_ms:10,
          imu_calibration:imuSnapshot(), last_error:''};
        if (state.delayStatus) {
          state.delayStatus = false;
          await new Promise(resolve=>{state.releaseStatus=resolve;});
          state.releaseStatus = null;
        }
        return json(200, snapshot);
      }
      if (req.method === 'POST' && (url.pathname.startsWith('/api/cal/') || url.pathname === '/api/imu-calibration')) {
        let raw = '';
        for await (const chunk of req) raw += chunk;
        const body = JSON.parse(raw || '{}');
        state.calls.push({path:url.pathname, body});
        switch (url.pathname) {
          case '/api/cal/enter':
            if (state.imu) return json(400, {detail:'IMU calibration is active'});
            state.mode = 'calibrate'; state.held.fill(false);
            return json(200, {ok:true});
          case '/api/cal/exit':
            if (state.imu && state.mode === 'calibrate') {
              clearTimeout(imuTimer);
              imuTimer = null;
              state.imu = false;
              state.imuResult.state = 'cancelled';
            }
            state.mode = 'shadow'; state.held.fill(false);
            return json(200, {ok:true});
          case '/api/cal/heartbeat':
            if (state.mode !== 'calibrate') return json(400, {detail:'not in calibrate mode'});
            state.beats++;
            return json(200, {ok:true});
          case '/api/cal/center': {
            const i = body.index;
            if (!Number.isInteger(i) || i<0 || i>=15) return json(400, {detail:'joint index out of range'});
            if (state.mode !== 'calibrate') return json(400, {detail:'not in calibrate mode'});
            if (state.imu) return json(400, {detail:'IMU calibration is in progress.'});
            if (state.delayCenter) {
              state.delayCenter=false;
              await new Promise(resolve=>{state.releaseCenter=resolve;});
              state.releaseCenter=null;
            }
            if (state.failCenter===i || !(state.mask & (1<<i))) {
              return json(400, {detail:`No servo replied at ID ${MOTORS[i]}; centering was not started`});
            }
            if (state.mode==='calibrate') { state.held[i]=true; state.saved.add(i); }
            return json(200, {ok:true, index:i, id:MOTORS[i], hold_pos:2048});
          }
          case '/api/imu-calibration':
            if (body.action !== 'start') return json(400, {detail:'unknown IMU calibration action'});
            if (state.imu) return json(200, imuSnapshot());
            if (!['off','shadow','hold','calibrate'].includes(state.mode)) return json(400, {detail:'Stop motion before calibrating.'});
            if (state.mode === 'calibrate' && state.held.some(Boolean)) return json(400, {detail:'Unload all joints before calibrating the IMU.'});
            if (!state.imuOk) return json(400, {detail:'required servo feedback / IMU missing'});
            if (state.imuStartError) return json(400, {detail:state.imuStartError});
            state.imu = true;
            imuStartedAt = Date.now();
            state.imuResult = {...state.imuResult, state:'sampling', progress:0};
            imuTimer = setTimeout(() => {
              state.imu = false;
              imuTimer = null;
              state.imuResult = state.imuSampleError
                ? {...state.imuResult, state:'error', message:'Previous calibration kept. ' + state.imuSampleError}
                : {state:'saved', progress:1, calibrated:true};
            }, 1500);
            return json(200, imuSnapshot());
        }
        return json(404, {detail:'unknown calibration command'});
      }
      if (req.method!=='GET') return json(405, {detail:'method not allowed'});
      if (url.pathname==='/calibrate-joints.json' && state.guideFailure) {
        if (state.guideFailure==='missing') return json(404, {detail:'not found'});
        if (state.guideFailure==='slow') await new Promise(resolve=>setTimeout(resolve,3500));
        if (state.guideFailure==='invalid') {
          const guide=JSON.parse(await fs.readFile(path.join(ASSETS,'calibrate-joints.json'),'utf8'));
          guide.zones[0].ids.reverse();
          return json(200,guide);
        }
      }
      const file=path.resolve(ASSETS, '.'+(url.pathname==='/'?'/calibrate.html':decodeURIComponent(url.pathname)));
      if (!file.startsWith(ASSETS+path.sep)) return json(403, {detail:'outside assets'});
      const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8',
        '.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.webp':'image/webp','.png':'image/png'};
      const data=await fs.readFile(file);
      res.writeHead(200, {'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
      res.end(data);
    } catch (error) {
      if (!res.headersSent) json(error.code==='ENOENT'?404:500, {detail:'mock request failed'});
      else res.end();
    }
  });
  await new Promise(resolve=>server.listen(port,'127.0.0.1',resolve));
  return {state, url:`http://127.0.0.1:${server.address().port}`,
    close:()=>new Promise(resolve=>{
      clearTimeout(imuTimer);
      state.releaseStatus?.();
      state.releaseCenter?.();
      server.close(resolve);
      server.closeAllConnections();
    })};
}

module.exports={createMock,MOTORS};
if (require.main===module) {
  // Keep the same implementation for an interactive preview; bind only to loopback.
  createMock(Number(process.argv[2]||0)).then(mock=>{
    console.log(`Calibration SIMULATOR (no hardware): ${mock.url}/calibrate.html`);
    process.on('SIGINT',()=>mock.close().then(()=>process.exit()));
    process.on('SIGTERM',()=>mock.close().then(()=>process.exit()));
  });
}
