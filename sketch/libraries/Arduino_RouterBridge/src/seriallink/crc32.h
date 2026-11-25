/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#ifndef SERIALLINK_CRC32_H
#define SERIALLINK_CRC32_H

#include <stddef.h>
#include <stdint.h>

namespace seriallink {

// Computes the CRC-32 (IEEE 802.3, the same used by zlib, Ethernet and Go's
// crc32.ChecksumIEEE) of len bytes. To compute the CRC of data split in
// several chunks, pass the result of the previous chunk as crc.
uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc = 0);

} // namespace seriallink

#endif // SERIALLINK_CRC32_H
