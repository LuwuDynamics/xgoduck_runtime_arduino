/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include "frame.h"

#include <string.h>

#include "crc32.h"

namespace seriallink {

const uint32_t BAUD_RATES[BAUD_RATES_COUNT] = {
    115200, 230400, 460800, 500000, 576000, 921600, 1000000,
    1152000, 1500000, 2000000, 2500000, 3000000, 3500000, 4000000,
};

uint16_t baudRateMask(uint32_t base, uint32_t maxRate) {
    uint16_t mask = 0;
    for (size_t i = 0; i < BAUD_RATES_COUNT; i++) {
        if (BAUD_RATES[i] > base && BAUD_RATES[i] <= maxRate) mask |= uint16_t(1u << i);
    }
    return mask;
}

uint32_t highestBaudRate(uint16_t mask) {
    for (size_t i = BAUD_RATES_COUNT; i-- > 0;) {
        if (mask & (1u << i)) return BAUD_RATES[i];
    }
    return 0;
}

void putUint32(uint8_t* b, uint32_t v) {
    b[0] = v;
    b[1] = v >> 8;
    b[2] = v >> 16;
    b[3] = v >> 24;
}

uint32_t getUint32(const uint8_t* b) {
    return uint32_t(b[0]) | uint32_t(b[1]) << 8 | uint32_t(b[2]) << 16 | uint32_t(b[3]) << 24;
}

size_t encodeFrame(const Frame& f, uint8_t* raw, uint8_t* dst) {
    raw[0] = PROTOCOL_VERSION << 4 | uint8_t(f.type);
    raw[1] = f.seq;
    raw[2] = f.ack;
    raw[3] = f.wnd;
    if (f.len > 0) memcpy(raw + HEADER_SIZE, f.payload, f.len);
    const size_t bodyLen = HEADER_SIZE + f.len;
    putUint32(raw + bodyLen, crc32(raw, bodyLen));
    const size_t n = cobs::encode(dst, raw, bodyLen + CRC_SIZE);
    dst[n] = 0;
    return n + 1;
}

DecodeResult decodeFrame(const uint8_t* encoded, size_t len, uint8_t* raw, size_t rawCap, Frame& f) {
    size_t rawLen;
    if (!cobs::decode(raw, rawCap, encoded, len, rawLen)) return DecodeResult::Corrupted;
    if (rawLen < HEADER_SIZE + CRC_SIZE) return DecodeResult::Corrupted;
    const size_t bodyLen = rawLen - CRC_SIZE;
    if (crc32(raw, bodyLen) != getUint32(raw + bodyLen)) return DecodeResult::Corrupted;
    if (raw[0] >> 4 != PROTOCOL_VERSION) return DecodeResult::Unsupported;
    const uint8_t type = raw[0] & 0x0F;
    if (type < uint8_t(FrameType::Data) || type > uint8_t(FrameType::Rst)) return DecodeResult::Unsupported;
    f.type = FrameType(type);
    f.seq = raw[1];
    f.ack = raw[2];
    f.wnd = raw[3];
    f.payload = raw + HEADER_SIZE;
    f.len = bodyLen - HEADER_SIZE;
    return DecodeResult::Ok;
}

size_t marshalSyn(const SynParams& p, bool withEcho, uint8_t* out) {
    out[0] = p.maxPayload;
    out[1] = p.maxPayload >> 8;
    out[2] = p.window;
    putUint32(out + 3, p.nonce);
    size_t n = SYN_SIZE;
    if (withEcho) {
        putUint32(out + n, p.echo);
        n = SYN_ACK_SIZE;
    }
    if (p.baudRates != 0) {
        out[n++] = uint8_t(p.baudRates);
        out[n++] = uint8_t(p.baudRates >> 8);
    }
    return n;
}

bool parseSyn(const uint8_t* b, size_t len, bool withEcho, SynParams& p) {
    const size_t size = withEcho ? SYN_ACK_SIZE : SYN_SIZE;
    if (len < size) return false;
    p.maxPayload = uint16_t(b[0] | b[1] << 8);
    p.window = b[2];
    p.nonce = getUint32(b + 3);
    p.echo = withEcho ? getUint32(b + 7) : 0;
    p.baudRates = 0;
    if (len >= size + BAUD_RATES_SIZE) p.baudRates = uint16_t(b[size] | b[size + 1] << 8) & BAUD_RATES_VALID;
    if (p.maxPayload < 1 || p.maxPayload > MAX_PAYLOAD_LIMIT) return false;
    if (p.window < 1 || p.window > MAX_WINDOW_LIMIT) return false;
    return true;
}

} // namespace seriallink
