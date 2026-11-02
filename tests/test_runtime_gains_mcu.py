"""Execute the MCU dispatch with user-selected gains and a simulated bus."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'),'A host C++ compiler is required')
class RuntimeGainsMCUTest(unittest.TestCase):
    def test_manual_tuning_and_reads_survive_debug_entry_exit_and_retired_profile_requests(self):
        sketch=(ROOT/'sketch/sketch.ino').read_text();bus=(ROOT/'sketch/scs_bus.h').read_text()
        writer=bus[bus.index('static void servoSetPermKpKd('):bus.index('static void servoSetProtection(')]
        receiver=sketch[sketch.index('static void receiveServo('):sketch.index('static void fillRawPacket(')]
        dispatch=sketch[sketch.index('static void applyServoOp('):sketch.index('static void controlTask(')]
        code=r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "duck_config.h"
#include "protocol.h"
namespace MsgPack {template<typename T> using bin_t=std::vector<T>;}
int hostMu,commandMu,bridgeReady=1,lastHostRxUs,invalidCommands;
constexpr int K_FOREVER=-1;
void k_mutex_lock(int*,int){} void k_mutex_unlock(int*){}
int atomic_get(int *v){return *v;} void atomic_set(int *v,int x){*v=x;}
uint32_t micros(){return 42;}
ServoPacket pendingServo;bool servoPending=false;
struct {void flush(){}} Serial1;
int torqueWrites=0,savedWrites=0,readAddress=0,userKp=7,userKd=19;
void scsWriteBuf(uint8_t id,uint8_t op,const uint8_t *data,uint8_t len){
 assert(id==33&&op==3&&len==3&&data[0]==21);
 userKp=data[1];userKd=data[2];++savedWrites;
}
void k_msleep(int){}
void setTorqueEnable(bool){++torqueWrites;}
void scsWriteByte(uint8_t,uint8_t,uint8_t){}
void servoUnlock(uint8_t){} void servoSetId(uint8_t,uint8_t){}
void servoSetProtection(uint8_t){} void servoSetProtectionCurrent(uint16_t){}
bool pingServo(uint8_t){return true;}
bool readServoPresent(uint8_t,uint16_t &p){p=2048;return true;}
bool readServoGains(uint8_t,uint8_t &p,uint8_t &d,uint8_t address=ADDR_PERM_KP_KD){
 readAddress=address;p=userKp;d=userKd;return true;
}
HostBusMode hostMode=HOST_NORMAL;
bool rawNotifyEveryTick=false;
uint8_t servoMidId=255,servoMidCount=0;
uint32_t servoMidNextUs=0;
ServoReplyPacket lastReply;
void publishServoReply(const ServoReplyPacket &r){lastReply=r;}
''' + writer+receiver+dispatch+r'''
void receive(ServoPacket p){
 memcpy(p.magic,"DQV2",4);
 MsgPack::bin_t<uint8_t> bytes(sizeof p);memcpy(bytes.data(),&p,sizeof p);
 servoPending=false;receiveServo(bytes);
}
int main(){
 ServoPacket p={};p.op=SERVO_APPLY_RUN_GAINS;p.targetId=254;p.kp=5;p.kd=10;
 receive(p);assert(!servoPending&&invalidCommands==1);
 applyServoOp(p);assert(!lastReply.ok&&savedWrites==0&&torqueWrites==0);
 p.op=SERVO_READ_RUN_GAINS;p.targetId=33;
 receive(p);assert(servoPending);
 applyServoOp(p);assert(lastReply.ok&&readAddress==50&&lastReply.kp==7&&lastReply.kd==19);
 assert(savedWrites==0&&torqueWrites==0);
 p.op=SERVO_ENTER;applyServoOp(p);assert(lastReply.ok&&hostMode==HOST_SERVO_DEBUG);
 assert(savedWrites==0&&userKp==7&&userKd==19);
 p.op=SERVO_SET_PERM_KP_KD;p.kp=9;p.kd=23;
 receive(p);assert(servoPending);
 applyServoOp(p);assert(lastReply.ok&&savedWrites==3&&userKp==9&&userKd==23);
 p.op=SERVO_EXIT;applyServoOp(p);assert(lastReply.ok&&hostMode==HOST_NORMAL);
 p.op=SERVO_READ_RUN_GAINS;applyServoOp(p);
 assert(lastReply.ok&&lastReply.kp==9&&lastReply.kd==23&&savedWrites==3);
 hostMode=HOST_CAL;applyServoOp(p);assert(!lastReply.ok);
 hostMode=HOST_NORMAL;servoMidId=34;applyServoOp(p);assert(!lastReply.ok);
}
'''
        with tempfile.TemporaryDirectory(prefix='duck-manual-gains-') as directory:
            path=Path(directory);(path/'test.cpp').write_text(code)
            (path/'Arduino.h').write_text('#pragma once\n#include <stdint.h>\n')
            compiled=subprocess.run(['c++','-std=c++17','-I',str(path),'-I',str(ROOT/'sketch'),str(path/'test.cpp'),'-o',str(path/'test')],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            run=subprocess.run([str(path/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr)


if __name__=='__main__':unittest.main()
