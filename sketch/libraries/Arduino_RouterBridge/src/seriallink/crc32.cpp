/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include "crc32.h"

namespace seriallink {

// Half-byte lookup table for the reflected polynomial 0xEDB88320: 64 bytes
// instead of the 1KB of the full table, fast enough for a serial line.
static const uint32_t crcTable[16] = {
    0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC,
    0x76DC4190, 0x6B6B51F4, 0x4DB26158, 0x5005713C,
    0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C,
    0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C,
};

uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc) {
    crc = ~crc;
    for (size_t i = 0; i < len; i++) {
        crc = crcTable[(crc ^ data[i]) & 0x0F] ^ (crc >> 4);
        crc = crcTable[(crc ^ (data[i] >> 4)) & 0x0F] ^ (crc >> 4);
    }
    return ~crc;
}

} // namespace seriallink
