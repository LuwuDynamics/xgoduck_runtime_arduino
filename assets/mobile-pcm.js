class DuckPCM extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Float32Array(2048);this.used=0;this.running=true;this.port.onmessage=()=>{this.flush();this.running=false;this.port.postMessage({done:true});};}
  flush(){if(this.used){const data=this.samples.slice(0,this.used);this.port.postMessage({samples:data},[data.buffer]);this.used=0;}}
  process(inputs){if(this.running&&inputs[0]?.[0])for(const v of inputs[0][0]){this.samples[this.used++]=v;if(this.used===this.samples.length)this.flush();}return true;}
}
registerProcessor('duck-pcm',DuckPCM);
