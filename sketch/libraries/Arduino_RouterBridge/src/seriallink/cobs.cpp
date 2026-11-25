/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include "cobs.h"

namespace seriallink {
namespace cobs {

size_t encode(uint8_t* dst, const uint8_t* src, size_t len) {
    size_t codeIdx = 0;
    size_t out = 1;
    uint8_t code = 1;
    for (size_t i = 0; i < len; i++) {
        if (src[i] != 0) {
            dst[out++] = src[i];
            code++;
            if (code != 0xFF) continue;
            if (i == len - 1) break; // a full block at the end of the input needs no further block
        }
        dst[codeIdx] = code;
        codeIdx = out++;
        code = 1;
    }
    dst[codeIdx] = code;
    return out;
}

bool decode(uint8_t* dst, size_t dstCap, const uint8_t* src, size_t len, size_t& outLen) {
    size_t out = 0;
    for (size_t i = 0; i < len;) {
        const uint8_t code = src[i++];
        if (code == 0) return false;
        const size_t end = i + code - 1;
        if (end > len) return false;
        if (out + (end - i) > dstCap) return false;
        for (; i < end; i++) {
            if (src[i] == 0) return false;
            dst[out++] = src[i];
        }
        if (code != 0xFF && i < len) {
            if (out >= dstCap) return false;
            dst[out++] = 0;
        }
    }
    outLen = out;
    return true;
}

} // namespace cobs
} // namespace seriallink
