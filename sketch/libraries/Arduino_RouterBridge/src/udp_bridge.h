/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once

#ifndef UDP_BRIDGE_H
#define UDP_BRIDGE_H

#define UDP_CONNECT_METHOD          "udp/connect"
#define UDP_CONNECT_MULTI_METHOD    "udp/connectMulticast"
#define UDP_CLOSE_METHOD            "udp/close"
#define UDP_BEGIN_PACKET_METHOD     "udp/beginPacket"
#define UDP_WRITE_METHOD            "udp/write"
#define UDP_END_PACKET_METHOD       "udp/endPacket"
#define UDP_AWAIT_PACKET_METHOD     "udp/awaitPacket"
#define UDP_READ_METHOD             "udp/read"
#define UDP_DROP_PACKET_METHOD      "udp/dropPacket"

#include <api/Udp.h>

#define DEFAULT_UDP_BUF_SIZE    4096
#define UDP_RESPONSE_HEADER_SIZE       20   // max size of msg_id plus RCP headers for a UDP response


struct BridgeUdpMeta {
    MsgPack::str_t host;
    uint16_t port;
    uint16_t size;

    BridgeUdpMeta() {
        host = "";
        port = 0;
        size = 0;
    }

    MSGPACK_DEFINE(size, host, port); // -> [code, traceback]
};

template<size_t BufferSize=DEFAULT_UDP_BUF_SIZE>
class BridgeUDP final: public UDP {

    BridgeClass* bridge;
    uint32_t connection_id{};
    uint32_t read_timeout = 1;
    RingBufferN<BufferSize> temp_buffer;
    struct k_mutex udp_mutex{};
    bool _connected = false;

    uint16_t _port{}; // local port to listen on

    // Outbound packets target
    String _targetHost{};
    uint16_t _targetPort{};

    // Inbound packet info
    IPAddress _remoteIP{}; // remote IP address for the incoming packet whilst it's being processed
    uint16_t _remotePort{}; // remote port for the incoming packet whilst it's being processed
    uint16_t _remaining{}; // remaining bytes of incoming packet yet to be processed
    BridgeUdpMeta packet_meta{};

public:

    explicit BridgeUDP(BridgeClass& bridge): bridge(&bridge) {}

    uint8_t begin(uint16_t port) override {
        if (!init()) {
            return 0;
        }

        k_mutex_lock(&udp_mutex, K_FOREVER);

        bool ok = false;
        if (!_connected) {
            String hostname = "0.0.0.0";
            ok = bridge->call(UDP_CONNECT_METHOD, hostname, port).result(connection_id);
            _connected = ok;
            if (_connected) _port = port;
        }

        k_mutex_unlock(&udp_mutex);

        return ok? 1 : 0;
    }

    void setTimeout(const uint32_t ms) {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        read_timeout = ms;
        k_mutex_unlock(&udp_mutex);
    }

    uint8_t beginMulticast(IPAddress ip, uint16_t port) override {
        (void)ip; // unused argument

        if (!init()) {
            return 0;
        }

        k_mutex_lock(&udp_mutex, K_FOREVER);

        bool ok = false;
        if (!_connected) {
            String hostname = "0.0.0.0";
            ok = bridge->call(UDP_CONNECT_METHOD, hostname, port).result(connection_id);
            _connected = ok;
            if (_connected) _port = port;
        }

        k_mutex_unlock(&udp_mutex);

        return ok? 1 : 0;
    }

    void stop() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);

        if (_connected) {
            String msg;
            _connected = !bridge->call(UDP_CLOSE_METHOD, connection_id).result(msg);
        }

        k_mutex_unlock(&udp_mutex);
    }

    int beginPacket(IPAddress ip, uint16_t port) override {
        return beginPacket(ip.toString().c_str(), port);
    }

    int beginPacket(const char *host, uint16_t port) override {
        if (!connected()) return 0;
        bool ok = false;

        k_mutex_lock(&udp_mutex, K_FOREVER);

        _targetHost = host;
        _targetPort = port;
        bool res = false;
        ok = bridge->call(UDP_BEGIN_PACKET_METHOD, connection_id, _targetHost, _targetPort).result(res) && res;

        k_mutex_unlock(&udp_mutex);

        return ok? 1 : 0;
    }

    int endPacket() override {
        if (!connected()) return 0;
        bool ok = false;

        k_mutex_lock(&udp_mutex, K_FOREVER);
        int transmitted = 0;
        ok = bridge->call(UDP_END_PACKET_METHOD, connection_id).result(transmitted);

        if (ok) {
            _targetHost = "";
            _targetPort = 0;
        }

        k_mutex_unlock(&udp_mutex);

        return ok? 1 : 0;
    }

    size_t write(uint8_t c) override {
        return write(&c, 1);
    }

    size_t write(const uint8_t *buffer, size_t size) override {
        if (!connected()) return 0;

        MsgPack::arr_t<uint8_t> payload;

        for (size_t i = 0; i < size; ++i) {
            payload.push_back(buffer[i]);
        }

        size_t written;
        k_mutex_lock(&udp_mutex, K_FOREVER);
        const bool ok = bridge->call(UDP_WRITE_METHOD, connection_id, payload).result(written);
        k_mutex_unlock(&udp_mutex);

        return ok? written : 0;
    }

    using Print::write;

    int parsePacket() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);

        dropPacket();  // ensure previous packet is read

        int out = 0;

        const bool ret = _connected && bridge->call(UDP_AWAIT_PACKET_METHOD, connection_id, read_timeout).result(packet_meta);

        if (ret) {
            if (!_remoteIP.fromString(packet_meta.host)) {
                _remoteIP.fromString("0.0.0.0");
            }
            _remotePort = packet_meta.port;
            _remaining = packet_meta.size;
            out = _remaining;
        }

        k_mutex_unlock(&udp_mutex);

        return out;
    }

    int dropPacket() {
        if (!connected()) return 0;

        bool ok=false;

        k_mutex_lock(&udp_mutex, K_FOREVER);
        if (_remaining > temp_buffer.available()) {
            bool res = false;
            ok = bridge->call(UDP_DROP_PACKET_METHOD, connection_id).result(res) && res;
        }

        _remaining = 0;
        temp_buffer.clear();
        k_mutex_unlock(&udp_mutex);

        return ok? 1 : 0;
    }

    int available() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        const int size = min(temp_buffer.availableForStore(), BRIDGE_RPC_BUFFER_SIZE - UDP_RESPONSE_HEADER_SIZE);
        if (size > 0) _read(size);
        const int _available = temp_buffer.available();
        k_mutex_unlock(&udp_mutex);
        return _available;
    }

    int read() override {
        uint8_t c;
        if (!temp_buffer.available() && !available()) {
            return -1;
        }
        return read(&c, 1) == 1 ? c : -1;
    }

    // reading stops when the UDP package has been read completely (_remaining = 0)
    int read(unsigned char *buffer, size_t len) override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
       	size_t i = 0;
        while (_remaining && i < len) {
            if (!temp_buffer.available() && !available()) {
                k_msleep(1);
                continue;
            }
            buffer[i++] = temp_buffer.read_char();
            _remaining--;
        }
        k_mutex_unlock(&udp_mutex);
        return (int)i;
    }

    int read(char *buffer, size_t len) override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        size_t i = 0;
        while (_remaining && i < len) {
            if (!temp_buffer.available() && !available()) {
                k_msleep(1);
                continue;
            }
            buffer[i++] = static_cast<char>(temp_buffer.read_char());
            _remaining--;
        }
        k_mutex_unlock(&udp_mutex);
        return (int)i;
    }

    int peek() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        int out = -1;
        if (_remaining && temp_buffer.available()) {
            out = temp_buffer.peek();
        }
        k_mutex_unlock(&udp_mutex);
        return out;
    }

    void flush() override {
        // Implemented only when there's a TX buffer
    }

    IPAddress remoteIP() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        const IPAddress ip = _remoteIP;
        k_mutex_unlock(&udp_mutex);
        return ip;
    }

    uint16_t remotePort() override {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        const uint16_t port = _remotePort;
        k_mutex_unlock(&udp_mutex);
        return port;
    }

    bool connected() {
        k_mutex_lock(&udp_mutex, K_FOREVER);
        const bool ok = _connected;
        k_mutex_unlock(&udp_mutex);
        return ok;
    }

private:

    bool init() {
        k_mutex_init(&udp_mutex);
        if (!(*bridge)) {
            return bridge->begin();
        }
        return true;
    }

    void _read(size_t size) {
        if (size == 0) return;

        k_mutex_lock(&udp_mutex, K_FOREVER);

        MsgPack::arr_t<uint8_t> message;
        const bool ret = _connected && bridge->call(UDP_READ_METHOD, connection_id, size, read_timeout).result(message);

        if (ret) {
            for (size_t i = 0; i < message.size(); ++i) {
                temp_buffer.store_char(static_cast<char>(message[i]));
            }
        }

        k_mutex_unlock(&udp_mutex);
    }

};

#endif //UDP_BRIDGE_H
