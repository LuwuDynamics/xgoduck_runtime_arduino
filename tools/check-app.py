"""Exercise WebUI APIs and MCU command acknowledgement; uses off/shadow only."""
import argparse
import json
import time
import urllib.request
import urllib.error

p=argparse.ArgumentParser()
p.add_argument('--url',default='http://127.0.0.1:9527')
a=p.parse_args()
def get():
    with urllib.request.urlopen(a.url+'/api/status',timeout=5) as r:return json.load(r)
def post(path,body):
    req=urllib.request.Request(a.url+'/api/'+path,data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
checks=[]
try:
    post('mode',{'mode':'off'});time.sleep(.25)
    before=get();time.sleep(.25);after=get()
    assert not after['enabled'] and after['mode']=='off'
    assert after['mcu_command_seq']>before['mcu_command_seq']
    assert after['mcu_command_age_us']<100000
    assert len(after['targets'])==15 and abs(after['targets'][2]+24)<.01
    checks.append('off mode and 15-target MCU acknowledgement')
    for path,body in [('command',{'twist':[2,0,0]}),('command',{'head':[0]}),
                      ('command',{'mouth':31}),('filter',{'leg':-1}),('mode',{'mode':'unknown'})]:
        try:post(path,body)
        except urllib.error.HTTPError as e:assert e.code==400
        else:raise AssertionError('invalid API request was accepted')
    checks.append('invalid command dimensions, bounds, filter and mode rejected')
    post('mode',{'mode':'shadow'})
    post('command',{'twist':[.12,.03,.2],'head':[.1,-.1,.05,0]})
    time.sleep(.3);changed=get()
    assert changed['command']==[.12,.03,.2] and changed['head']==[.1,-.1,.05,0]
    assert changed['inferred']>after['inferred'] and not changed['enabled']
    assert changed['mcu_command_seq']>after['mcu_command_seq']
    checks.append('Web motion/head command reaches running shadow policy and MCU')
    result=post('filter',{'leg':.4,'head':.5})
    assert result=={'leg':.4,'head':.5}
    checks.append('independent leg/head action smoothing')
finally:
    post('mode',{'mode':'off'})
    post('filter',{'leg':.45,'head':.45})
    post('command',{'twist':[0,0,0],'head':[0,0,0,0]})
post('mode',{'mode':'shadow'});time.sleep(.25)
result={'checks':checks,'status':get()}
print(json.dumps({'passed':checks,'final_mode':result['status']['mode']},indent=2))
