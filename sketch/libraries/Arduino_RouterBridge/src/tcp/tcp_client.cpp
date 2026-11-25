/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (c) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include <Arduino.h>
#include "tcp_client.h"

BridgeTCPClientBase::BridgeTCPClientBase(BridgeClass& bridge): bridge(&bridge) {}

BridgeTCPClientBase::BridgeTCPClientBase(BridgeClass& bridge, uint32_t connection_id, bool connected, BridgeTCPConnection* _server)
    : bridge(&bridge), connection_id(connection_id), _connected(connected), _server(_server) {}

bool BridgeTCPClientBase::operator==(const BridgeTCPClientBase& rhs) const {
    return connection_id == rhs.connection_id;
}

bool BridgeTCPClientBase::begin() {
    k_mutex_init(&client_mutex);
    if (!(*bridge)) {
        return bridge->begin();
    }
    return true;
}

void BridgeTCPClientBase::setTimeout(uint32_t ms) {
    k_mutex_lock(&client_mutex, K_FOREVER);
    read_timeout = ms;
    k_mutex_unlock(&client_mutex);
}

int BridgeTCPClientBase::connect(IPAddress ip, uint16_t port) {
    return connect(ip.toString().c_str(), port);
}

int BridgeTCPClientBase::connect(const char *host, uint16_t port) {
    k_mutex_lock(&client_mutex, K_FOREVER);

    String hostname = host;
    const bool ok = _connected || bridge->call(TCP_CONNECT_METHOD, hostname, port).result(connection_id);
    _connected = ok;

    k_mutex_unlock(&client_mutex);
    return ok ? 0 : -1;
}

int BridgeTCPClientBase::connectSSL(const char *host, uint16_t port, const char *ca_cert) {
    k_mutex_lock(&client_mutex, K_FOREVER);

    String hostname = host;
    String ca_cert_str = ca_cert;

    const bool ok = _connected || bridge->call(TCP_CONNECT_SSL_METHOD, hostname, port, ca_cert_str).result(connection_id);
    _connected = ok;

    k_mutex_unlock(&client_mutex);
    return ok ? 0 : -1;
}

uint32_t BridgeTCPClientBase::getId() {
    k_mutex_lock(&client_mutex, K_FOREVER);
    const uint32_t out = connection_id;
    k_mutex_unlock(&client_mutex);
    return out;
}

size_t BridgeTCPClientBase::write(uint8_t c) {
    return write(&c, 1);
}

size_t BridgeTCPClientBase::write(const uint8_t *buffer, size_t size) {
    if (!connected()) return 0;

    MsgPack::arr_t<uint8_t> payload;
    for (size_t i = 0; i < size; ++i) {
        payload.push_back(buffer[i]);
    }

    size_t written;
    k_mutex_lock(&client_mutex, K_FOREVER);
    const bool ok = bridge->call(TCP_WRITE_METHOD, connection_id, payload).result(written);
    k_mutex_unlock(&client_mutex);
    if (!ok) {
        stop();
    }
    return ok ? written : 0;
}

void BridgeTCPClientBase::flush() {
    // No-op: flush is implemented for Client subclasses using an output buffer
}

void BridgeTCPClientBase::close() {
    stop();
}

void BridgeTCPClientBase::stop() {
    k_mutex_lock(&client_mutex, K_FOREVER);
    String msg;
    if (_connected) {
        _connected = !bridge->call(TCP_CLOSE_METHOD, connection_id).result(msg);
    }
    if (_server) {
        _server->disconnect();   // virtual call — no cast, no free function
    }
    k_mutex_unlock(&client_mutex);
}

uint8_t BridgeTCPClientBase::connected() {
    k_mutex_lock(&client_mutex, K_FOREVER);
    const uint8_t out = _connected ? 1 : 0;
    k_mutex_unlock(&client_mutex);
    return out;
}