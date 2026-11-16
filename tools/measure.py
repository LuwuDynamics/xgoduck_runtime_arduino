"""Read-only HTTP timing capture. Does not enable motors or change app mode."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

p=argparse.ArgumentParser()
p.add_argument('--url',default='http://127.0.0.1:9527')
p.add_argument('--seconds',type=float,default=60)
p.add_argument('--output',default='')
a=p.parse_args()
samples=[];started=time.monotonic()
while time.monotonic()-started<a.seconds:
    with urllib.request.urlopen(a.url+'/api/status',timeout=5) as r: s=json.load(r)
    samples.append(s)
    if len(samples)%20==0:
        print(f"t={time.monotonic()-started:.1f}s feedback={s['feedback_hz']:.2f}Hz inference={s['inference_hz']:.2f}Hz ids={s.get('servo_ids')} imu={s.get('imu_ok')} gaps={s['sequence_gaps']}",flush=True)
    time.sleep(.25)
first,last=samples[0],samples[-1];dt=last['uptime_s']-first['uptime_s']
summary=dict(duration_s=dt,feedback_hz=(last['received']-first['received'])/dt,
             inference_hz=(last['inferred']-first['inferred'])/dt,
             sent_hz=(last['sent']-first['sent'])/dt,
             acknowledged_hz=(last.get('mcu_command_seq',0)-first.get('mcu_command_seq',0))/dt,
             max_command_age_ms=max(s.get('mcu_command_age_us',0) for s in samples)/1000,
             inference_ms=last.get('inference_ms',{}),
             mcu_work_us={'min':min(s.get('mcu_work_us',0) for s in samples),'max':max(s.get('mcu_work_us',0) for s in samples)},
             mcu_invalid_commands=last.get('mcu_invalid_commands',0)-first.get('mcu_invalid_commands',0),
             bad_frames=last['bad_frames']-first['bad_frames'],
             sequence_gaps=last['sequence_gaps']-first['sequence_gaps'],
             mcu_overruns=last.get('mcu_overruns',0)-first.get('mcu_overruns',0),
             mcu_report_drops=last.get('mcu_report_drops',0)-first.get('mcu_report_drops',0),
             imu_ok_all=all(s.get('imu_ok') for s in samples),
             observed_servo_sets=[list(t) for t in sorted(set(tuple(s.get('servo_ids',[])) for s in samples))],
             motors_enabled_any=any(s.get('enabled') for s in samples))
if a.output:
    out=Path(a.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'summary':summary,'samples':samples},indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
