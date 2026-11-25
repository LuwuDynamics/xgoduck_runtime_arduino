"""Compile and test the real MCU receive parser with a host C++ compiler."""
from pathlib import Path
import tempfile, subprocess
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='duck-servo-check-') as td:
    p=Path(td)
    (p/'Arduino.h').write_text('''#pragma once
#include <cstdint>
#include <cstddef>
#include <deque>
#include <vector>
struct k_mutex {};
#define K_FOREVER 0
inline void k_mutex_lock(k_mutex*,int) {}
inline void k_mutex_unlock(k_mutex*) {}
#define K_USEC(x) (x)
inline void k_sleep(unsigned) {}
inline void k_msleep(unsigned) {}
inline uint32_t micros(){static uint32_t now=0;return now+=100;}
struct FakeSerial {
std::deque<uint8_t> rx;
std::vector<uint8_t> reply, lastRequest;
bool echo=false;
unsigned writes=0, dropReplies=0;
int available(){return rx.size();} int read(){auto x=rx.front();rx.pop_front();return x;}
void flush(){}
void write(const uint8_t* buf,size_t length){
  ++writes;
  lastRequest.assign(buf,buf+length);
  if(echo) rx.insert(rx.end(),buf,buf+length);
  if(dropReplies) --dropReplies;
  else rx.insert(rx.end(),reply.begin(),reply.end());
}
};
extern FakeSerial Serial1;
''')
    (p/'test.cpp').write_text('''#include <cassert>
#include <vector>
#include "scs_bus.h"
#include "protocol.h"
FakeSerial Serial1; k_mutex motorsMu; Motor motors[NUM_MOTORS]={};
std::vector<uint8_t> statusPacket(uint8_t id,uint8_t err,std::vector<uint8_t> data,bool corrupt=false){
std::vector<uint8_t>b={255,255,id,(uint8_t)(data.size()+2),err};b.insert(b.end(),data.begin(),data.end());
uint8_t sum=0;for(size_t i=2;i<b.size();++i)sum+=b[i];b.push_back((uint8_t)(~sum)^(corrupt?1:0));
return b;}
void packet(uint8_t id,uint8_t err,std::vector<uint8_t> data,bool corrupt=false){
auto b=statusPacket(id,err,data,corrupt);
Serial1.rx.insert(Serial1.rx.end(),b.begin(),b.end());parseServoRx();}
int main(){
for(int i=0;i<NUM_MOTORS;i++)motors[i].id=MOTOR_IDS[i];
for(bool echo : {false,true}) {
  Serial1.echo=echo;
  Serial1.reply.clear();
  for(unsigned id=0;id<=253;++id) assert(!pingServo(id));
  for(uint8_t id : {0,1,2,3,4,8,9,10,11,42,253}) {
    Serial1.reply=statusPacket(id,0,{id});
    assert(pingServo(id));
    auto expected=statusPacket(id,0x02,{ADDR_ID,1});
    expected.insert(expected.begin(),0xff);
    assert(Serial1.lastRequest==expected);
    Serial1.reply=statusPacket(id,0,{static_cast<uint8_t>(id+1)});
    assert(!pingServo(id));
  }
  for(const auto& invalid : {statusPacket(41,0,{42}),statusPacket(42,0,{42},true),
                              statusPacket(42,4,{42}),statusPacket(42,0,{}),
                              statusPacket(42,0,{42,0})}) {
    Serial1.reply=invalid;
    assert(!pingServo(42));
  }
  Serial1.reply=statusPacket(42,0,{42});
  assert(pingServo(42));
  Serial1.reply.clear();
  assert(!pingServo(42));
}
Serial1.echo=false;
servoReadId=42;parseServoRx();assert(!servoReadSeen);
packet(41,0,{0,8,0,0,0,0});assert(!servoReadSeen);
packet(42,0,{0,8,0,0,0,0},true);assert(!servoReadSeen);
packet(42,4,{0,8,0,0,0,0});assert(servoReadSeen && servoReadPos==2048 && servoReadAlarm==4);
packet(42,0,{0,8,0,0,0,0});assert(servoReadSeen&&servoReadPos==2048&&servoRxMask==0);
servoReadSeen=false;servoReadId=0xFF;packet(42,0,{0,8,0,0,0,0});assert(!servoReadSeen);
for (uint8_t id : {0, 253}) {
servoReadId=id;servoReadSeen=false;packet(id,0,{0,8,0,0,0,0});assert(servoReadSeen&&servoReadPos==2048);
servoReadId=0xFF;servoReadSeen=false;packet(id,0,{0,8,0,0,0,0});assert(!servoReadSeen);
}
packet(10,0,{1,8,0,0,0,0});assert(servoRxMask==1&&motors[0].fbPos==2049);
const int joint=motorIndexById(21);
const uint16_t bit=1u<<joint;
motors[joint].sign=-1;
beginStateRead();parseServoRx();
assert(!abnormalStateReply && servoRxMask==0 && servoReplyDetail.error[joint]==0);
packet(21,0x24,{1,8,0,0,0,0,75},true);
assert(servoReplyDetail.badChecksumMask==bit && servoReplyDetail.error[joint]==0 && servoRxMask==0);
beginStateRead();packet(21,0x24,{1,8,2,0,0,0,75});
assert(abnormalStateReply && servoReplyDetail.error[joint]==0x24 && servoRxMask==bit);
assert(motors[joint].fbPos==2049 && motors[joint].fbAngleDeg<0 && motors[joint].fbVelDeg<0);
packet(21,0,{1,8,0,0,0,0,75});
assert(servoRxMask==bit && servoReplyDetail.error[joint]==0x24);
beginStateRead();packet(21,0xff,{});
assert(servoReplyDetail.error[joint]==0xff && servoReplyDetail.unexpectedLengthMask==bit && !servoRxMask);
beginStateRead();packet(21,0,{1,2,3});
assert(servoReplyDetail.unexpectedLengthMask==bit && !servoReplyDetail.error[joint] && !servoRxMask);
for(uint8_t alarm : {0, 0x24}) {
  beginStateRead();packet(21,alarm,{0,16,0,0,0,0,75});
  assert(!servoRxMask && servoReplyDetail.invalidPositionMask==bit && motors[joint].fbPos==2049);
}
beginStateRead();packet(21,0x24,{255,15,0,0,0,0});
assert(servoRxMask==bit && motors[joint].fbPos==4095);
beginStateRead();packet(21,0x24,{0,0,0,0,0,0});
assert(servoRxMask==bit && motors[joint].fbPos==0);
beginStateRead();collectingStateReplies=false;packet(21,0x10,{});
assert(!abnormalStateReply && !servoReplyDetail.error[joint]);
Serial1.reply=statusPacket(21,0x24,{1,8,0,0,0,0,75});
uint16_t position=0;
assert(readServoPresent(21,position) && position==2049);
assert(!readServoPresent(21,position,true));
Serial1.reply.clear();
servoParamsId=42;servoParamsSeen=false;
packet(41,0,{5,10});assert(!servoParamsSeen);
packet(42,4,{5,10});assert(!servoParamsSeen);
packet(42,0,{5,10});assert(servoParamsSeen&&servoParamKp==5&&servoParamKd==10);
packet(42,0,{0});assert(!servoByteSeen);
servoByteReadId=42;
packet(41,0,{0});assert(!servoByteSeen);
packet(42,0,{0},true);assert(!servoByteSeen);
packet(42,4,{0});assert(!servoByteSeen);
packet(42,0,{});assert(!servoByteSeen);
packet(42,0,{1});assert(servoByteSeen&&servoByteValue==1);
servoByteSeen=false;
packet(42,0,{0});assert(servoByteSeen&&servoByteValue==0);
// The request echo survives even when several consecutive status packets are missing.
Serial1.echo=true;
Serial1.reply=statusPacket(10,0,{10});
Serial1.dropReplies=SETUP_READ_ATTEMPTS-1; Serial1.writes=0;
assert(pingServo(10) && Serial1.writes==SETUP_READ_ATTEMPTS);
assert(Serial1.lastRequest==statusPacket(10,0x02,{ADDR_ID,1}));
Serial1.reply.clear(); Serial1.writes=0;
assert(!pingServo(10) && Serial1.writes==SETUP_READ_ATTEMPTS);
uint8_t kp=0,kd=0;
Serial1.reply=statusPacket(10,0,{5,10}); Serial1.dropReplies=1; Serial1.writes=0;
assert(readServoGains(10,kp,kd,ADDR_PERM_KP_KD,SETUP_READ_ATTEMPTS));
assert(kp==5 && kd==10 && Serial1.writes==2);
Serial1.dropReplies=1; Serial1.writes=0;
assert(!readServoGains(10,kp,kd) && Serial1.writes==1);
uint16_t setupPos=0;
Serial1.reply=statusPacket(10,0,{1,8,0,0,0,0,85}); Serial1.dropReplies=1;
assert(readServoPresent(10,setupPos,false,SETUP_READ_ATTEMPTS) && setupPos==2049);
}
''')
    subprocess.run(['c++','-std=c++11','-I'+td,'-I'+str(root/'sketch'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: ID register discovery, empty and echo-only buses, torque readback, invalid replies, and normal feedback.')
