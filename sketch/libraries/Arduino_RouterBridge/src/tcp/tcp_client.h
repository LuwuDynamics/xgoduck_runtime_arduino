/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once
#ifndef BRIDGE_TCP_CLIENT_H
#define BRIDGE_TCP_CLIENT_H

#define TCP_CONNECT_METHOD          "tcp/connect"
#define TCP_CONNECT_SSL_METHOD      "tcp/connectSSL"
#define TCP_CLOSE_METHOD            "tcp/close"
#define TCP_WRITE_METHOD            "tcp/write"
#define TCP_READ_METHOD             "tcp/read"

#include <api/RingBuffer.h>
#include <api/Client.h>
#include "../bridge.h"
#include "bridge_tcp_common.h"

#define DEFAULT_TCP_CLIENT_BUF_SIZE    512
#define TCP_RESPONSE_HEADER_SIZE       20


class BridgeTCPClientBase : public Client {
protected:
    BridgeClass* bridge;
    uint32_t connection_id{};
    uint32_t read_timeout = 0;
    struct k_mutex client_mutex{};
    bool _connected = false;
    BridgeTCPConnection* _server = nullptr;

    BridgeTCPClientBase(BridgeClass& bridge);
    BridgeTCPClientBase(BridgeClass& bridge, uint32_t connection_id, bool connected, BridgeTCPConnection* _server);

public:
    bool operator==(const BridgeTCPClientBase& rhs) const;

    bool begin();
    void setTimeout(uint32_t ms);

    int connect(IPAddress ip, uint16_t port) override;
    int connect(const char *host, uint16_t port) override;
    int connectSSL(const char *host, uint16_t port, const char *ca_cert);

    uint32_t getId();

    size_t write(uint8_t c) override;
    size_t write(const uint8_t *buffer, size_t size) override;

    void flush() override;
    void close();
    void stop() override;
    uint8_t connected() override;

    using Print::write;
};

template<size_t BufferSize=DEFAULT_TCP_CLIENT_BUF_SIZE>
class BridgeTCPClient : public BridgeTCPClientBase {
    RingBufferN<BufferSize> temp_buffer;

public:
    explicit BridgeTCPClient(BridgeClass& bridge): BridgeTCPClientBase(bridge) {}

    BridgeTCPClient(BridgeClass& bridge, uint32_t connection_id, bool connected=false, BridgeTCPConnection* _server=nullptr)
        : BridgeTCPClientBase(bridge, connection_id, connected, _server) {}

    int available() override {
        k_mutex_lock(&client_mutex, K_FOREVER);
        const int size = min(temp_buffer.availableForStore(), BRIDGE_RPC_BUFFER_SIZE - TCP_RESPONSE_HEADER_SIZE);
        if (size > 0) _read(size);
        const int _available = temp_buffer.available();
        k_mutex_unlock(&client_mutex);
        return _available;
    }

    int read() override {
        uint8_t c;
        if (!temp_buffer.available()) {
            return -1;
        }
        return read(&c, 1) == 1 ? c : -1;
    }

    int read(uint8_t *buf, size_t size) override {
        k_mutex_lock(&client_mutex, K_FOREVER);
        size_t i = 0;
        while (temp_buffer.available() && i < size) {
            buf[i++] = temp_buffer.read_char();
        }
        k_mutex_unlock(&client_mutex);
        return (int)i;
    }

    int peek() override {
        k_mutex_lock(&client_mutex, K_FOREVER);
        int out = -1;
        if (temp_buffer.available()) {
            out = temp_buffer.peek();
        }
        k_mutex_unlock(&client_mutex);
        return out;
    }

    operator bool() override {
        return available() || connected();
    }

    using BridgeTCPClientBase::write;
    using Print::write;

private:
    void _read(size_t size) {
        if (size == 0) return;

        k_mutex_lock(&client_mutex, K_FOREVER);

        if (!_connected) {
            k_mutex_unlock(&client_mutex);
            return;
        }

        MsgPack::arr_t<uint8_t> message;
        bool ret;
        int err;

        if (read_timeout > 0) {
            RpcCall async_rpc_timeout = bridge->call(TCP_READ_METHOD, connection_id, size, read_timeout);
            ret = async_rpc_timeout.result(message);
            err = async_rpc_timeout.getErrorCode();
        } else {
            RpcCall async_rpc = bridge->call(TCP_READ_METHOD, connection_id, size);
            ret = async_rpc.result(message);
            err = async_rpc.getErrorCode();
        }

        if (ret) {
            for (size_t i = 0; i < message.size(); ++i) {
                temp_buffer.store_char(static_cast<char>(message[i]));
            }
        }

        if (err > NO_ERR) {
            _connected = false;
        }

        k_mutex_unlock(&client_mutex);
    }
};

#endif //BRIDGE_TCP_CLIENT_H