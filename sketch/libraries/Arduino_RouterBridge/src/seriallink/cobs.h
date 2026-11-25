/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#ifndef SERIALLINK_COBS_H
#define SERIALLINK_COBS_H

#include <stddef.h>
#include <stdint.h>

// Consistent Overhead Byte Stuffing (COBS).
//
// The encoding removes every 0x00 byte from the data, so that 0x00 can be used
// as a frame delimiter (the delimiter is not part of the encoding). See the
// documentation of the internal/cobs package of arduino-router for a
// description of the algorithm.
namespace seriallink {
namespace cobs {

// Maximum length of the encoding of n bytes (without delimiter).
constexpr size_t maxEncodedLen(size_t n) {
    return n + n / 254 + 1;
}

// Encodes len bytes from src into dst, that must have room for at least
// maxEncodedLen(len) bytes. Returns the length of the encoding.
size_t encode(uint8_t* dst, const uint8_t* src, size_t len);

// Decodes len COBS-encoded bytes (without delimiter) from src into dst, that
// has room for dstCap bytes. On success returns true and sets outLen to the
// decoded length, returns false if the encoding is invalid or does not fit.
bool decode(uint8_t* dst, size_t dstCap, const uint8_t* src, size_t len, size_t& outLen);

} // namespace cobs
} // namespace seriallink

#endif // SERIALLINK_COBS_H
