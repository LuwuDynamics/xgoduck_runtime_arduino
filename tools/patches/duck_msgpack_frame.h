// Bounded, allocation-free MessagePack framing for RPClite 0.3.1.
// Return a complete object's byte count, 0 for incomplete, -1 for invalid/oversize.
#pragma once
#include <stddef.h>
#include <stdint.h>
static int duckMsgpackFrame(const uint8_t* data, size_t size, size_t limit) {
    size_t pos = 0;
    uint64_t pending = 1;
    uint64_t remaining[16] = {1};
    unsigned depth = 0;
    while (pending) {
        while (!remaining[depth]) --depth;
        if (pos >= size) return 0;
        const uint8_t c = data[pos++];
        --pending;
        --remaining[depth];
        uint64_t payload = 0, children = 0;
        unsigned width = 0;
        bool variable = false, map = false, container = false, extension = false;
        if (c <= 0x7f || c >= 0xe0 || c == 0xc0 || c == 0xc2 || c == 0xc3) {}
        else if ((c & 0xf0) == 0x80) children = 2*(c & 15);
        else if ((c & 0xf0) == 0x90) children = c & 15;
        else if ((c & 0xe0) == 0xa0) payload = c & 31;
        else switch (c) {
            case 0xc4: case 0xd9: width=1; variable=true; break;
            case 0xc5: case 0xda: width=2; variable=true; break;
            case 0xc6: case 0xdb: width=4; variable=true; break;
            case 0xc7: width=1; variable=true; extension=true; break;
            case 0xc8: width=2; variable=true; extension=true; break;
            case 0xc9: width=4; variable=true; extension=true; break;
            case 0xca: case 0xce: case 0xd2: payload=4; break;
            case 0xcb: case 0xcf: case 0xd3: payload=8; break;
            case 0xcc: case 0xd0: payload=1; break;
            case 0xcd: case 0xd1: payload=2; break;
            case 0xd4: payload=2; break;
            case 0xd5: payload=3; break;
            case 0xd6: payload=5; break;
            case 0xd7: payload=9; break;
            case 0xd8: payload=17; break;
            case 0xdc: width=2; container=true; break;
            case 0xdd: width=4; container=true; break;
            case 0xde: width=2; container=true; map=true; break;
            case 0xdf: width=4; container=true; map=true; break;
            default: return -1; // reserved 0xc1
        }
        if (width) {
            if (width > size-pos) return 0;
            uint64_t length=0;
            for (unsigned i=0; i<width; ++i) length=(length<<8)|data[pos++];
            if (container) children=length*(map ? 2 : 1);
            if (variable) payload=length+(extension ? 1 : 0);
        }
        pending += children;
        if (pos > limit || payload > limit-pos || pending > limit-pos-payload) return -1;
        if (children) {
            if (++depth >= 16) return -1;
            remaining[depth] = children;
        }
        if (payload > size-pos) return 0;
        pos += (size_t)payload;
    }
    return (int)pos;
}
