/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once
#ifndef BRIDGE_TCP_SERVER_H
#define BRIDGE_TCP_SERVER_H

#define TCP_LISTEN_METHOD           "tcp/listen"
#define TCP_ACCEPT_METHOD           "tcp/accept"
#define TCP_CLOSE_LISTENER_METHOD   "tcp/closeListener"

#include <api/Server.h>
#include "../bridge.h"
#include "bridge_tcp_common.h"
#include "tcp_client.h"

#define DEFAULT_TCP_SERVER_BUF_SIZE    512

class BridgeTCPServerBase : public Server, public BridgeTCPConnection {
protected:
    BridgeClass* bridge;
    IPAddress _addr{};
    uint16_t _port;
    bool _listening = false;
    uint32_t listener_id = 0;
    uint32_t connection_id = 0;
    bool _connected = false;
    struct k_mutex server_mutex{};

    BridgeTCPServerBase(BridgeClass& bridge, const IPAddress& addr, uint16_t port)
        : bridge(&bridge), _addr(addr), _port(port) {}

public:
    void begin() override;

    void close();

    void disconnect() override;

    bool is_listening();

    bool is_connected();

    uint16_t getPort();

    String getAddr();

    operator bool() const;
};

template<size_t BufferSize=DEFAULT_TCP_SERVER_BUF_SIZE>
class BridgeTCPServer final : public BridgeTCPServerBase {
public:
    //using BridgeTCPServerBase::BridgeTCPServerBase;

    explicit BridgeTCPServer(BridgeClass& bridge_ptr, const IPAddress addr, uint16_t port): BridgeTCPServerBase(bridge_ptr, addr, port) {}

    BridgeTCPClient<BufferSize> accept() {
        k_mutex_lock(&server_mutex, K_FOREVER);

        if (!_listening) {
            k_mutex_unlock(&server_mutex);
            return BridgeTCPClient<BufferSize>(*bridge, 0, false, this);
        }

        if (_connected) {
            k_mutex_unlock(&server_mutex);
            return BridgeTCPClient<BufferSize>(*bridge, connection_id, true, this);
        }

        const bool ret = bridge->call(TCP_ACCEPT_METHOD, listener_id).result(connection_id);
        _connected = ret;

        k_mutex_unlock(&server_mutex);

        return ret ? BridgeTCPClient<BufferSize>(*bridge, connection_id, true, this)
                   : BridgeTCPClient<BufferSize>(*bridge, 0, false, this);
    }

    size_t write(uint8_t c) override { return write(&c, 1); }

    size_t write(const uint8_t *buf, size_t size) override {
        BridgeTCPClient<BufferSize> client = accept();
        if (!client) return 0;

        k_mutex_lock(&server_mutex, K_FOREVER);
        size_t written = 0;
        if (_connected) {
            written = client.write(buf, size);
        }
        k_mutex_unlock(&server_mutex);
        return written;
    }

    using Print::write;
};

#endif //BRIDGE_TCP_SERVER_H