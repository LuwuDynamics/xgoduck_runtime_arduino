"""Verify an Uno Q loadable sketch has no short branches to unresolved symbols."""
import argparse
from pathlib import Path
import subprocess

p=argparse.ArgumentParser()
p.add_argument('elf',nargs='?',default=str(Path(__file__).resolve().parents[1]/'.cache/sketch/sketch.ino.elf'))
a=p.parse_args()
tool=Path.home()/'.arduino15/packages/zephyr/tools/arm-zephyr-eabi/1.0.1/bin/arm-zephyr-eabi-readelf'
def read(*args):return subprocess.check_output([str(tool),*args,a.elf],text=True)
undefined={line.split()[-1] for line in read('-Ws').splitlines() if ' UND ' in line and len(line.split())>7}
bad=[]
for line in read('-Wr').splitlines():
    parts=line.split()
    if len(parts)>=5 and parts[2] in ('R_ARM_THM_JUMP24','R_ARM_THM_CALL','R_ARM_CALL','R_ARM_JUMP24') and parts[4] in undefined:
        bad.append(line)
if bad:raise SystemExit('Unsafe RAM-to-firmware branch relocations:\n'+'\n'.join(bad))
print('PASS: no short-branch relocations to unresolved firmware symbols')
