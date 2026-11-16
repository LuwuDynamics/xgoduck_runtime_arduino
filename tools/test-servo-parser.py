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
struct k_mutex {};
#define K_FOREVER 0
inline void k_mutex_lock(k_mutex*,int) {}
inline void k_mutex_unlock(k_mutex*) {}
#define K_USEC(x) (x)
inline void k_sleep(unsigned) {}
inline void k_msleep(unsigned) {}
inline uint32_t micros(){static uint32_t now=0;return now+=100;}
struct FakeSerial {std::deque<uint8_t> rx;
int available(){return rx.size();} int read(){auto x=rx.front();rx.pop_front();return x;}
void flush(){} void write(const uint8_t*,size_t){} };
extern FakeSerial Serial1;
''')
    (p/'test.cpp').write_text('''#include <cassert>
#include <vector>
#include "scs_bus.h"
#include "protocol.h"
FakeSerial Serial1; k_mutex motorsMu; Motor motors[NUM_MOTORS]={};
void packet(uint8_t id,uint8_t err,std::vector<uint8_t> data,bool corrupt=false){
std::vector<uint8_t>b={255,255,id,(uint8_t)(data.size()+2),err};b.insert(b.end(),data.begin(),data.end());
uint8_t sum=0;for(size_t i=2;i<b.size();++i)sum+=b[i];b.push_back((uint8_t)(~sum)^(corrupt?1:0));
for(auto x:b)Serial1.rx.push_back(x);parseServoRx();}
int main(){
for(int i=0;i<NUM_MOTORS;i++)motors[i].id=MOTOR_IDS[i];
servoPingId=42;packet(41,0,{});assert(!servoPingSeen);packet(42,0,{},true);assert(!servoPingSeen);
packet(42,0,{});assert(servoPingSeen);servoPingId=0xFF;
servoReadId=42;parseServoRx();assert(!servoReadSeen);
packet(41,0,{0,8,0,0,0,0});assert(!servoReadSeen);
packet(42,0,{0,8,0,0,0,0},true);assert(!servoReadSeen);
packet(42,4,{0,8,0,0,0,0});assert(!servoReadSeen);
packet(42,0,{0,8,0,0,0,0});assert(servoReadSeen&&servoReadPos==2048&&servoRxMask==0);
servoReadSeen=false;servoReadId=0xFF;packet(42,0,{0,8,0,0,0,0});assert(!servoReadSeen);
for (uint8_t id : {0, 253}) {
servoPingId=id;servoPingSeen=false;packet(id,4,{});assert(servoPingSeen);
servoPingId=0xFF;servoPingSeen=false;packet(id,0,{});assert(!servoPingSeen);
servoReadId=id;servoReadSeen=false;packet(id,0,{0,8,0,0,0,0});assert(servoReadSeen&&servoReadPos==2048);
servoReadId=0xFF;servoReadSeen=false;packet(id,0,{0,8,0,0,0,0});assert(!servoReadSeen);
}
packet(10,0,{1,8,0,0,0,0});assert(servoRxMask==1&&motors[0].fbPos==2049);
servoParamsId=42;servoParamsSeen=false;
packet(41,0,{5,10});assert(!servoParamsSeen);
packet(42,4,{5,10});assert(!servoParamsSeen);
packet(42,0,{5,10});assert(servoParamsSeen&&servoParamKp==5&&servoParamKd==10);
}
''')
    subprocess.run(['c++','-std=c++11','-I'+td,'-I'+str(root/'sketch'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: arbitrary-ID Ping/read, no reply, bad checksum, wrong ID, device error, and normal feedback.')
