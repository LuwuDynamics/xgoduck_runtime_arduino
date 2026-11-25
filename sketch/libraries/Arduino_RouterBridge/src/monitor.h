/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once

#ifndef BRIDGE_MONITOR_H
#define BRIDGE_MONITOR_H

#include <api/RingBuffer.h>
#include "bridge.h"

#define MON_CONNECTED_METHOD    "mon/connected"
#define MON_RESET_METHOD        "mon/reset"
#define MON_READ_METHOD         "mon/read"
#define MON_WRITE_METHOD        "mon/write"

#define DEFAULT_MONITOR_BUF_SIZE    512

template<size_t BufferSize=DEFAULT_MONITOR_BUF_SIZE>
class BridgeMonitor: public Stream {

    BridgeClass* bridge;
    RingBufferN<BufferSize> temp_buffer;
    struct k_mutex monitor_mutex{};
    bool _connected = false;
    bool _compatibility_mode = true;

public:
    explicit BridgeMonitor(BridgeClass& bridge): bridge(&bridge) {}

    using Print::write;

    bool begin(unsigned long _legacy_baud=0, uint16_t _legacy_config=0) {
	// unused parameters for compatibility with Stream
	(void)_legacy_baud;
	(void)_legacy_config;

        k_mutex_init(&monitor_mutex);

        if (is_connected()) return true;

        k_mutex_lock(&monitor_mutex, K_FOREVER);
        bool bridge_started = (*bridge);
        if (!bridge_started) {
            bridge_started = bridge->begin();
        }

        if (!bridge_started) {
            k_mutex_unlock(&monitor_mutex);
            return false;
        }

        bool out = false;
        _connected = bridge->call(MON_CONNECTED_METHOD).result(out) && out;
        MsgPack::str_t ver;
        _compatibility_mode = !bridge->getRouterVersion(ver);
        k_mutex_unlock(&monitor_mutex);
        return out;
    }

    bool is_connected() {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        bool out = _connected;
        k_mutex_unlock(&monitor_mutex);
        return out;
    }

    explicit operator bool() {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        bool out = _connected;
        if (!_connected) {
            bridge->call(MON_CONNECTED_METHOD).result(out);
            _connected = out;
        }
        k_mutex_unlock(&monitor_mutex);
        return out;
    }

    int read() override {
        uint8_t c = 0;
        int cch_read;

        cch_read = read(&c, 1);
        return cch_read? c : -1;
    }

    int read(uint8_t* buffer, size_t size) {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        size_t i = 0;
        while (temp_buffer.available() && i < size) {
            buffer[i++] = temp_buffer.read_char();
        }
        k_mutex_unlock(&monitor_mutex);
        return (int)i;
    }

    int available() override {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        int size = temp_buffer.availableForStore();
        if (size > 0) _read(size);
        int available = temp_buffer.available();
        k_mutex_unlock(&monitor_mutex);
        return available;
    }

    int peek() override {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        int out = -1;
        if (temp_buffer.available()) {
            out = temp_buffer.peek();
        }
        k_mutex_unlock(&monitor_mutex);
        return out;
    }

    size_t write(uint8_t c) override {
        return write(&c, 1);
    }

    size_t write(const uint8_t* buffer, size_t size) override {

        if (!*this) { return 0; }

        String send_buffer;

        for (size_t i = 0; i < size; ++i) {
            send_buffer += static_cast<char>(buffer[i]);
        }

        size_t written = 0;

        if (_compatibility_mode) {
            bridge->call(MON_WRITE_METHOD, send_buffer).result(written);
        } else {
            bridge->notify(MON_WRITE_METHOD, send_buffer);
            written = size;
        }

        return written;
    }

    bool reset() {
        k_mutex_lock(&monitor_mutex, K_FOREVER);
        bool res = false;
        bridge->call(MON_RESET_METHOD).result(res);
        if (res) {_connected = false;}
        k_mutex_unlock(&monitor_mutex);
        return res;
    }

private:
    void _read(size_t size) {

        if (size == 0) return;

        if (!*this) return;

        k_mutex_lock(&monitor_mutex, K_FOREVER);

        MsgPack::arr_t<uint8_t> message;
        RpcCall async_rpc = bridge->call(MON_READ_METHOD, size);
        const bool ret = async_rpc.result(message);

        if (ret) {
            for (size_t i = 0; i < message.size(); ++i) {
                temp_buffer.store_char(static_cast<char>(message[i]));
            }
        }

        // if (async_rpc.getErrorCode() > NO_ERR) {
        //     _connected = false;
        // }

        k_mutex_unlock(&monitor_mutex);
    }

};

extern BridgeMonitor<> Monitor;

#ifdef ARDUINO_ROUTERBRIDGE_PROVIDES_SERIAL
/* 'Monitor' is aliased to 'Serial' for compatibility with existing sketches.
 * Both identifiers will refer to the same BridgeMonitor instance.
 */
extern BridgeMonitor<> Serial;
#endif

#endif // BRIDGE_MONITOR_H
