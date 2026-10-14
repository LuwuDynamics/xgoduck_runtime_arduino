const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'../assets');
let stopped=0,closed=0,permissionError=false;
class Context {
 constructor(){this.sampleRate=48000;this.state='running';this.audioWorklet={addModule:async()=>{}};}
 async resume(){}
 async close(){this.state='closed';closed++;}
 createMediaStreamSource(){return {connect(node){return node;},disconnect(){}};}
 createGain(){return {gain:{value:1},connect(){},disconnect(){}};}
}
class Node {
 constructor(){this.port={postMessage:()=>this.port.onmessage({data:{done:true}})};}
 connect(node){return node;}disconnect(){}
}
const context={window:{},Blob,Float32Array,ArrayBuffer,DataView,AudioContext:Context,AudioWorkletNode:Node,setTimeout,
 navigator:{mediaDevices:{getUserMedia:async()=>{if(permissionError)throw new Error('denied');return {getTracks:()=>[{stop:()=>stopped++}]};}}}};
vm.runInNewContext(fs.readFileSync(path.join(root,'mobile-audio.js'),'utf8'),context);
(async()=>{
 const {wav,Capture}=context.window.DuckAudio;
 const data=new DataView(await wav([new Float32Array([-2,-1,0,1,2])],48000).arrayBuffer());
 assert.equal(data.byteLength,54);assert.equal(data.getUint32(24,true),48000);assert.equal(data.getUint32(40,true),10);
 assert.equal(data.getInt16(44,true),-32768);assert.equal(data.getInt16(48,true),0);assert.equal(data.getInt16(52,true),32767);
 const capture=new Capture();await capture.start();capture.node.port.onmessage({data:{samples:new Float32Array(480)}});
 const out=await capture.stop();assert.equal(out.seconds,.01);assert.equal(out.blob.size,1004);assert.equal(stopped,1);assert.equal(closed,1);
 permissionError=true;const denied=new Capture();await assert.rejects(denied.start(),/denied/);assert.equal(closed,2);
 permissionError=false;const limited=new Capture();await limited.start();limited.node.port.onmessage({data:{samples:new Float32Array(48000*31)}});assert.equal(limited.count,48000*30);await limited.close();
 let Processor;const messages=[];
 vm.runInNewContext(fs.readFileSync(path.join(root,'mobile-pcm.js'),'utf8'),{
  Float32Array,AudioWorkletProcessor:class{constructor(){this.port={postMessage:d=>messages.push(d)};}},registerProcessor:(name,cls)=>Processor=cls});
 const p=new Processor();p.process([[new Float32Array(128).fill(.5)]]);p.port.onmessage();assert.equal(messages[0].samples.length,128);assert.equal(messages[0].samples[0],.5);assert.equal(messages[1].done,true);
 p.process([[new Float32Array(128)]]);assert.equal(messages.length,2);
 console.log('PASS: PCM WAV clipping/header, capture cleanup, denied permission cleanup, 30 s cap, worklet flush/stop.');
})().catch(e=>{console.error(e);process.exitCode=1;});
