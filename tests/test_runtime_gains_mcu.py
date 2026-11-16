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
        writer=bus[bus.index('static void servoSetKpKd('):bus.index('static void servoSetProtectionCurrent(')]
        receiver=sketch[sketch.index('static void receiveServo('):sketch.index('static void fillRawPacket(')]
        dispatch=sketch[sketch.index('constexpr uint32_t SERVO_TEST_KEEPALIVE_US'):sketch.index('static void controlTask(')]
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
uint32_t clockUs=42;
uint32_t micros(){return clockUs;}
ServoPacket pendingServo;bool servoPending=false;
struct {void flush(){}} Serial1;
int torqueWrites=0,savedWrites=0,liveWrites=0,readAddress=0;
uint8_t savedKp=7,savedKd=19,liveKp=7,liveKd=19;
bool rejectLive=false;
uint8_t storedProtection=PROTECTION_ENABLED | 0x20;
int protectionWrites=0;
void scsWriteBuf(uint8_t id,uint8_t op,const uint8_t *data,uint8_t len){
 assert(id==33&&op==3&&len==3);
 if(data[0]==ADDR_PERM_KP_KD){
  savedKp=data[1];savedKd=data[2];++savedWrites;
 }else{
  assert(data[0]==ADDR_TEMP_KP);
  if(!rejectLive){liveKp=data[1];liveKd=data[2];}
  ++liveWrites;
 }
}
void k_msleep(int){}
void setTorqueEnable(bool){++torqueWrites;}
struct ServoWrite { uint8_t id; uint16_t value; };
std::vector<ServoWrite> testTorque, testGoals;
void scsWriteByte(uint8_t id,uint8_t reg,uint8_t value){
 if (reg==ADDR_TORQUE) testTorque.push_back({id,value});
 if (reg==ADDR_UNLOAD_CONDITION){
  assert(id==33);
  storedProtection=value;
  ++protectionWrites;
 }
}
void writeSingleGoalRaw(uint8_t id,uint16_t value){testGoals.push_back({id,value});}
int motorIndexById(uint8_t id){
 for (int i=0;i<NUM_MOTORS;++i) if (MOTOR_IDS[i]==id) return i;
 return -1;
}
uint16_t satPos(int32_t value){return value<POS_MIN?POS_MIN:value>POS_MAX?POS_MAX:value;}
void servoUnlock(uint8_t){} void servoSetId(uint8_t,uint8_t){}
void servoSetProtectionCurrent(uint16_t){}
bool pingServo(uint8_t){return true;}
bool readServoPresent(uint8_t,uint16_t &p){p=2048;return true;}
bool readServoGains(uint8_t,uint8_t &p,uint8_t &d,uint8_t address=ADDR_PERM_KP_KD){
 readAddress=address;
 p=address==ADDR_TEMP_KP ? liveKp : savedKp;
 d=address==ADDR_TEMP_KP ? liveKd : savedKd;
 return true;
}
bool readServoProtection(uint8_t id,uint8_t &flags,uint16_t &current){
 if (id!=33) return false;
 flags=storedProtection;
 current=308;
 return true;
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
 applyServoOp(p, false);assert(!lastReply.ok&&savedWrites==0&&torqueWrites==0);
 p.op=SERVO_READ_RUN_GAINS;p.targetId=33;
 receive(p);assert(servoPending);
 applyServoOp(p, false);assert(lastReply.ok&&readAddress==50&&lastReply.kp==7&&lastReply.kd==19);
 assert(savedWrites==0&&torqueWrites==0);
 ServoPacket protection={};protection.op=SERVO_READ_PROTECTION;protection.targetId=33;
 receive(protection);assert(servoPending);
 applyServoOp(protection, true);assert(!lastReply.ok&&torqueWrites==0);
 hostMode=HOST_CAL;applyServoOp(protection, false);assert(!lastReply.ok);
 hostMode=HOST_NORMAL;
 applyServoOp(protection, false);
 assert(lastReply.ok&&lastReply.protectionCurrent==308&&torqueWrites==0&&protectionWrites==0);
 ServoPacket protectionSwitch=protection;
 protectionSwitch.op=SERVO_SET_PROTECTION_SWITCH;protectionSwitch.protectionMask=PROTECTION_VOLTAGE;
 applyServoOp(protectionSwitch, false);assert(!lastReply.ok&&protectionWrites==0);
 p.op=SERVO_ENTER;applyServoOp(p, false);assert(lastReply.ok&&hostMode==HOST_SERVO_DEBUG);
 applyServoOp(protection, false);
 assert(lastReply.ok&&lastReply.protectionFlags==(PROTECTION_ENABLED|0x20)&&lastReply.protectionCurrent==308);
 protection.targetId=34;applyServoOp(protection, false);assert(!lastReply.ok&&lastReply.protectionCurrent==0);
 protection.targetId=254;receive(protection);assert(!servoPending);
 protection.op=SERVO_SET_PROTECTION_SWITCH;protection.protectionMask=PROTECTION_VOLTAGE;
 receive(protection);assert(!servoPending);
 protection.targetId=34;applyServoOp(protection, false);assert(!lastReply.ok&&protectionWrites==0);
 protection.targetId=33;protection.protectionFlags=PROTECTION_OVERCURRENT;
 receive(protection);assert(!servoPending);
 protection.protectionFlags=0;receive(protection);assert(servoPending);
 applyServoOp(protection, false);
 assert(lastReply.ok&&lastReply.protectionFlags==0x2C&&lastReply.protectionCurrent==308&&protectionWrites==3);
 applyServoOp(protection, false);assert(lastReply.ok&&protectionWrites==3);
 protection.protectionMask=PROTECTION_OVERCURRENT;receive(protection);assert(servoPending);
 applyServoOp(protection, false);assert(lastReply.ok&&lastReply.protectionFlags==0x24&&protectionWrites==6);
 protection.protectionFlags=PROTECTION_OVERCURRENT;receive(protection);assert(servoPending);
 applyServoOp(protection, false);assert(lastReply.ok&&lastReply.protectionFlags==0x2C&&protectionWrites==9);
 protection.protectionMask=PROTECTION_VOLTAGE | PROTECTION_OVERCURRENT;
 receive(protection);assert(!servoPending);
 assert(savedWrites==0&&liveWrites==0&&liveKp==7&&liveKd==19);
 p.op=SERVO_READ_RUN_GAINS;applyServoOp(p, false);
 assert(lastReply.ok&&readAddress==50&&lastReply.kp==7&&lastReply.kd==19);
 assert(hostMode==HOST_SERVO_DEBUG&&savedWrites==0&&liveWrites==0);
 p.op=SERVO_SET_PERM_KP_KD;p.kp=9;p.kd=23;
 receive(p);assert(servoPending);
 applyServoOp(p, false);assert(lastReply.ok&&savedWrites==3&&savedKp==9&&savedKd==23);
 assert(liveWrites==1&&liveKp==9&&liveKd==23);
 rejectLive=true;p.kp=10;applyServoOp(p, false);
 assert(!lastReply.ok&&savedKp==10&&liveKp==9);
 p.op=SERVO_EXIT;applyServoOp(p, false);assert(lastReply.ok&&hostMode==HOST_NORMAL);
 p.op=SERVO_READ_RUN_GAINS;applyServoOp(p, false);
 assert(lastReply.ok&&lastReply.kp==9&&lastReply.kd==23&&savedWrites==6&&liveWrites==2);
 hostMode=HOST_CAL;applyServoOp(p, false);assert(!lastReply.ok);
 hostMode=HOST_NORMAL;servoMidId=34;applyServoOp(p, false);assert(!lastReply.ok);

 ServoPacket test={};test.op=SERVO_TEST_START;test.targetId=33;test.testSession=100;
 receive(test);assert(servoPending);
 applyServoOp(test, false);assert(!lastReply.ok&&testTorque.empty());
 p.op=SERVO_ENTER;applyServoOp(p, false);
 applyServoOp(test, false);
 assert(lastReply.ok&&lastReply.testSession==100&&lastReply.rawPos==2048);
 assert(lastReply.testFeedbackLimit==10&&lastReply.testOffsetLimitDeg==30);
 assert(lastReply.testIdleTimeoutSec==10);
 assert(testTorque.size()==1&&testTorque.back().id==33&&testTorque.back().value==1);
 assert(testGoals.size()==1&&testGoals.back().value==2048);
 protection.protectionMask=PROTECTION_OVERCURRENT;
 applyServoOp(protection, false);assert(!lastReply.ok&&protectionWrites==9);

 test.targetId=34;applyServoOp(test, false);
 assert(!lastReply.ok&&testTorque.size()==1);
 test.op=SERVO_TEST_MOVE;test.targetId=33;test.testOffset=41;
 receive(test);assert(!servoPending);
 test.testOffset=235;receive(test);assert(!servoPending);
 test.testOffset=40;receive(test);assert(servoPending);
 applyServoOp(test, false);assert(lastReply.ok&&servoTest.goal==2389);
 test.testOffset=236;receive(test);assert(servoPending);
 applyServoOp(test, false);assert(lastReply.ok&&servoTest.goal==1707);
 test.testOffset=10;receive(test);assert(servoPending);
 applyServoOp(test, false);assert(lastReply.ok&&servoTest.goal==2048);
 test.testOffset=20;test.testSession=99;
 applyServoOp(test, false);assert(!lastReply.ok);
 test.testSession=100;receive(test);assert(servoPending);
 applyServoOp(test, false);assert(lastReply.ok&&servoTest.goal==2161);
 clockUs+=TICK_US;advanceServoTest();
 assert(testGoals.size()==2&&testGoals.back().id==33&&testGoals.back().value==2050);

 const uint16_t testMask=1u<<motorIndexById(33);
 const uint16_t missingMask=0x7FFFu & ~testMask;
 for (int frame=0;frame<10;++frame) {
  checkServoTestFeedback(missingMask);
  clockUs+=TICK_US;advanceServoTest();
  assert(servoTest.session==100&&testGoals.size()==2&&testTorque.size()==1);
 }
 test.testOffset=0;applyServoOp(test, false);
 assert(lastReply.ok&&servoTest.goal==1935);
 advanceServoTest();assert(testGoals.size()==2);
 checkServoTestFeedback(testMask);
 clockUs+=TICK_US;advanceServoTest();
 assert(servoTest.missedFeedback==0&&testGoals.size()==3&&testGoals.back().value==2048);
 for (int frame=0;frame<10;++frame) {
  checkServoTestFeedback(missingMask);
  clockUs+=TICK_US;advanceServoTest();
  assert(servoTest.session==100&&testGoals.size()==3&&testTorque.size()==1);
 }
 checkServoTestFeedback(missingMask);
 assert(!servoTest.session&&testTorque.size()==2&&testTorque.back().id==33&&testTorque.back().value==0);
 checkServoTestFeedback(testMask);
 applyServoOp(test, false);assert(!lastReply.ok&&lastReply.testFeedbackLimit==10&&testGoals.size()==3);
 assert(!lastReply.testIdleTimeoutSec);

 test.op=SERVO_TEST_START;test.testOffset=0;test.testSession=101;
 applyServoOp(test, false);assert(lastReply.ok);
 clockUs+=SERVO_TEST_KEEPALIVE_US;advanceServoTest();
 assert(testTorque.size()==4&&testTorque.back().id==33&&testTorque.back().value==0);
 test.op=SERVO_TEST_MOVE;applyServoOp(test, false);assert(!lastReply.ok&&!lastReply.testFeedbackLimit);
 test.op=SERVO_TEST_START;test.testSession=102;
 applyServoOp(test, false);assert(lastReply.ok);
 test.op=SERVO_TEST_STOP;test.testSession=100;
 applyServoOp(test, false);assert(servoTest.session==102);
 test.testSession=102;applyServoOp(test, false);
 assert(lastReply.ok&&!servoTest.session&&testTorque.back().value==0);

 // Identical angle heartbeats do not extend the hold, including across micros() rollover.
 constexpr uint32_t idleUs=10000000u, heartbeatUs=150000u;
 clockUs=UINT32_MAX-idleUs/2;
 const uint32_t started=clockUs;
 test.op=SERVO_TEST_START;test.testSession=103;test.testOffset=0;
 applyServoOp(test, false);assert(lastReply.ok);
 test.op=SERVO_TEST_MOVE;test.testOffset=10;
 for (uint32_t elapsed=heartbeatUs;elapsed<idleUs;elapsed+=heartbeatUs) {
  clockUs=started+elapsed;
  advanceServoTest();
  applyServoOp(test, false);assert(lastReply.ok&&servoTest.session==103);
 }
 const size_t torqueBeforeIdle=testTorque.size();
 clockUs=started+idleUs;
 advanceServoTest();
 assert(!servoTest.session&&testTorque.size()==torqueBeforeIdle+1);
 assert(testTorque.back().id==33&&testTorque.back().value==0);
 test.testOffset=20;
 applyServoOp(test, false);assert(!lastReply.ok&&lastReply.testIdleTimeoutSec==10&&!lastReply.testFeedbackLimit);
 assert(testTorque.size()==torqueBeforeIdle+1);

 // A new angle starts a fresh window. A move at the next deadline cannot restart it.
 const uint32_t restarted=clockUs;
 test.op=SERVO_TEST_START;test.testSession=104;test.testOffset=0;
 applyServoOp(test, false);assert(lastReply.ok);
 test.op=SERVO_TEST_MOVE;test.testOffset=10;
 for (uint32_t elapsed=heartbeatUs;elapsed<idleUs;elapsed+=heartbeatUs) {
  clockUs=restarted+elapsed;
  applyServoOp(test, false);assert(lastReply.ok);
 }
 const uint32_t changed=restarted+idleUs-50000u;
 clockUs=changed;test.testOffset=20;
 applyServoOp(test, false);assert(lastReply.ok&&servoTest.goal==2161);
 clockUs=restarted+idleUs;advanceServoTest();
 assert(servoTest.session==104&&testTorque.back().value==1);
 for (uint32_t elapsed=heartbeatUs;elapsed<idleUs;elapsed+=heartbeatUs) {
  clockUs=changed+elapsed;
  applyServoOp(test, false);assert(lastReply.ok);
 }
 clockUs=changed+idleUs;test.testOffset=30;
 applyServoOp(test, false);assert(!lastReply.ok&&lastReply.testIdleTimeoutSec==10);
 assert(!servoTest.session&&testTorque.back().id==33&&testTorque.back().value==0);
 assert(savedWrites==6&&liveWrites==2);
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
