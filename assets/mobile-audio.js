/* PCM WAV keeps phone recordings compatible with the board's ALSA player. */
window.DuckAudio = (() => {
  function wav(chunks,rate){
    const length=chunks.reduce((n,c)=>n+c.length,0), buffer=new ArrayBuffer(44+length*2), v=new DataView(buffer);
    const str=(at,text)=>{for(let i=0;i<text.length;i++)v.setUint8(at+i,text.charCodeAt(i));};
    str(0,'RIFF');v.setUint32(4,36+length*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,length*2,true);
    let offset=44;for(const c of chunks)for(const value of c){const x=Math.max(-1,Math.min(1,value));v.setInt16(offset,x<0?x*32768:x*32767,true);offset+=2;}return new Blob([buffer],{type:'audio/wav'});
  }
  class Capture {
    async start(){
      this.chunks=[];this.count=0;
      this.context=new AudioContext();await this.context.resume();
      try{
        this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
        await this.context.audioWorklet.addModule('/mobile-pcm.js');
        this.input=this.context.createMediaStreamSource(this.stream);this.node=new AudioWorkletNode(this.context,'duck-pcm');this.silent=this.context.createGain();this.silent.gain.value=0;
        this.node.port.onmessage=({data})=>{if(data.samples&&this.count<this.context.sampleRate*30){const c=data.samples.subarray(0,this.context.sampleRate*30-this.count);this.chunks.push(c);this.count+=c.length;}if(data.done)this.finished?.();};
        this.input.connect(this.node).connect(this.silent).connect(this.context.destination);
      }catch(e){await this.close();throw e;}
    }
    async stop(){
      if(this.node)await new Promise(resolve=>{this.finished=resolve;this.node.port.postMessage('stop');setTimeout(resolve,500);});
      const result={blob:wav(this.chunks,this.context.sampleRate),seconds:this.count/this.context.sampleRate};await this.close();return result;
    }
    async close(){this.stream?.getTracks().forEach(t=>t.stop());this.input?.disconnect();this.node?.disconnect();this.silent?.disconnect();if(this.context&&this.context.state!=='closed')await this.context.close();}
  }
  // Play incoming S16_LE mono PCM immediately. Bound the queue so a network
  // stall catches up to live sound instead of accumulating seconds of delay.
  class LivePCM {
    constructor(context, rate=24000){this.context=context;this.rate=rate;this.next=0;this.tail=null;this.nodes=new Set();}
    push(bytes){
      if(this.tail!==null){const joined=new Uint8Array(bytes.length+1);joined[0]=this.tail;joined.set(bytes,1);bytes=joined;}
      this.tail=bytes.length%2?bytes[bytes.length-1]:null;
      const count=Math.floor(bytes.length/2);if(!count)return;
      const now=this.context.currentTime, max=Math.floor(this.rate*.2), length=Math.min(count,max);
      if(this.next<now||this.next+length/this.rate>now+.3||count>max)this.clear();
      const buffer=this.context.createBuffer(1,length,this.rate), samples=buffer.getChannelData(0);
      const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
      for(let i=0;i<length;i++)samples[i]=view.getInt16((count-length+i)*2,true)/32768;
      const node=this.context.createBufferSource();node.buffer=buffer;node.connect(this.context.destination);
      node.onended=()=>{this.nodes.delete(node);node.disconnect();};this.nodes.add(node);
      const at=Math.max(now+.04,this.next);node.start(at);this.next=at+length/this.rate;
    }
    clear(){for(const node of this.nodes){try{node.stop();}catch{}node.disconnect();}this.nodes.clear();this.next=0;}
    stop(){this.clear();this.tail=null;}
  }
  return {wav,Capture,LivePCM};
})();
