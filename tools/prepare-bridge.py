"""Increase the pinned RouterBridge RPC thread stack for binary state/control RPC.

Run on Uno Q after profile libraries have been resolved, before compiling.
The original library file is backed up next to the patched header.
"""
from pathlib import Path

root = Path.home()/'.arduino15/internal'
matches = list(root.glob('Arduino_RouterBridge_0.4.3_*/Arduino_RouterBridge/src/bridge.h'))
if len(matches) != 1:
    raise SystemExit('Resolve sketch.yaml libraries first; expected one RouterBridge 0.4.3')
path = matches[0]
source = path.read_text()
old = '#define UPDATE_THREAD_STACK_SIZE    500'
new = '#define UPDATE_THREAD_STACK_SIZE    4096'
if new in source:
    print('RouterBridge 0.4.3 RPC stack already 4096 bytes')
elif source.count(old) == 1:
    backup = path.with_suffix('.h.original')
    if not backup.exists(): backup.write_text(source)
    path.write_text(source.replace(old, new))
    print('Patched RouterBridge 0.4.3 RPC stack: 500 -> 4096 bytes')
else:
    raise SystemExit('Unexpected Bridge source; refusing to patch a different version')

# RPClite tests every stream prefix with MsgPack::Unpacker. That decoder is not
# bounds safe on incomplete/invalid binary data, and allocates for every prefix.
# Frame complete objects first, preserving the official RPC envelopes and APIs.
patches = Path(__file__).resolve().parent/'patches'
matches = list(root.glob('Arduino_RPClite_0.3.1_*/Arduino_RPClite/src/decoder.h'))
if len(matches) != 1: raise SystemExit('Expected one RPClite 0.3.1')
path = matches[0]
source = path.read_text()
if 'DUCK_BOUNDED_FRAMING' not in source:
    start = source.index('    void parse_packet(){')
    end = source.index('    bool packet_incoming()', start)
    backup = path.with_suffix('.h.original')
    if not backup.exists(): backup.write_text(source)
    source = source[:start]+(patches/'parse_packet.txt').read_text()+source[end:]
    source = source.replace('#include "transport.h"', '#include "transport.h"\n#include "duck_msgpack_frame.h"')
    path.write_text(source)
    print('Patched RPClite 0.3.1 bounded MessagePack framing')
else:
    start = source.index('    void parse_packet(){')
    end = source.index('    bool packet_incoming()', start)
    updated = source[:start]+(patches/'parse_packet.txt').read_text()+source[end:]
    if updated != source: path.write_text(updated)
    print('RPClite 0.3.1 bounded framing installed / updated')
(path.parent/'duck_msgpack_frame.h').write_text((patches/'duck_msgpack_frame.h').read_text())
