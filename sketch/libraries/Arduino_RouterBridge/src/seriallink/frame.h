/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#ifndef SERIALLINK_FRAME_H
#define SERIALLINK_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "cobs.h"

namespace seriallink {

constexpr uint8_t PROTOCOL_VERSION = 1;
constexpr size_t HEADER_SIZE = 4;
constexpr size_t CRC_SIZE = 4;
constexpr size_t MAX_PAYLOAD_LIMIT = 4096;
constexpr size_t MAX_WINDOW_LIMIT = 127;
constexpr size_t SYN_SIZE = 7;       // without the optional fields
constexpr size_t SYN_ACK_SIZE = 11;  // without the optional fields
constexpr size_t BAUD_RATES_SIZE = 2;
constexpr size_t RST_SIZE = 4;
// Every receiver must accept control frames (SYN, SYN_ACK, RST) with a payload
// of up to this size, whatever its max payload.
constexpr size_t CONTROL_PAYLOAD_LIMIT = 32;

// Baud rates that can be negotiated: bit i of a baud rate mask stands for
// BAUD_RATES[i]. Bits 14 and 15 are reserved.
constexpr size_t BAUD_RATES_COUNT = 14;
extern const uint32_t BAUD_RATES[BAUD_RATES_COUNT];
constexpr uint16_t BAUD_RATES_VALID = 0x3FFF;

// Returns the mask of the rates r with base < r <= maxRate.
uint16_t baudRateMask(uint32_t base, uint32_t maxRate);

// Returns the highest rate in mask, 0 if mask is empty.
uint32_t highestBaudRate(uint16_t mask);

// Size of a decoded frame (header, payload and CRC).
constexpr size_t rawFrameSize(size_t payloadLen) {
    return HEADER_SIZE + payloadLen + CRC_SIZE;
}

// Maximum size of an encoded frame, including the 0x00 delimiter.
constexpr size_t maxEncodedFrameSize(size_t payloadLen) {
    return cobs::maxEncodedLen(rawFrameSize(payloadLen)) + 1;
}

enum class FrameType : uint8_t {
    Data = 1,
    Ack = 2,
    Nak = 3,
    Syn = 4,
    SynAck = 5,
    Rst = 6,
};

struct Frame {
    FrameType type;
    uint8_t seq;
    uint8_t ack;
    uint8_t wnd;
    const uint8_t* payload;
    size_t len;
};

// Encodes the frame, followed by the 0x00 delimiter, into dst. raw is a
// scratch buffer of at least rawFrameSize(f.len) bytes, dst must have room for
// maxEncodedFrameSize(f.len) bytes. Returns the encoded length.
size_t encodeFrame(const Frame& f, uint8_t* raw, uint8_t* dst);

enum class DecodeResult : uint8_t {
    Ok,
    Corrupted,   // bad COBS encoding, bad CRC, too short or too long
    Unsupported, // valid, but of an unknown protocol version or frame type
};

// Decodes a COBS-encoded frame (without the delimiter) using raw, of rawCap
// bytes, as buffer for the decoded frame. On success f.payload points into
// raw.
DecodeResult decodeFrame(const uint8_t* encoded, size_t len, uint8_t* raw, size_t rawCap, Frame& f);

// Session parameters exchanged in SYN and SYN_ACK frames.
struct SynParams {
    uint16_t maxPayload;
    uint8_t window;
    uint32_t nonce;
    uint32_t echo;      // only in SYN_ACK
    uint16_t baudRates; // optional, omitted if 0
};

// Writes the SYN or SYN_ACK payload (at most CONTROL_PAYLOAD_LIMIT bytes),
// returns its length.
size_t marshalSyn(const SynParams& p, bool withEcho, uint8_t* out);

// Parses and validates a SYN or SYN_ACK payload. Optional fields may be
// missing, and unknown trailing bytes are ignored (future extensions).
bool parseSyn(const uint8_t* b, size_t len, bool withEcho, SynParams& p);

void putUint32(uint8_t* b, uint32_t v);
uint32_t getUint32(const uint8_t* b);

} // namespace seriallink

#endif // SERIALLINK_FRAME_H
