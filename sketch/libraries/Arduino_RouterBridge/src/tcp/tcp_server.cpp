/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (c) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include <Arduino.h>
#include "tcp_server.h"

void BridgeTCPServerBase::begin() {
    k_mutex_init(&server_mutex);
    if (!(*bridge)) {
        while (!bridge->begin());
    }
    k_mutex_lock(&server_mutex, K_FOREVER);
    if (!_listening) {
        String hostname = _addr.toString();
        _listening = bridge->call(TCP_LISTEN_METHOD, hostname, _port).result(listener_id);
    }
    k_mutex_unlock(&server_mutex);
}

void BridgeTCPServerBase::close() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    String msg;
    if (_listening) {
        _listening = !bridge->call(TCP_CLOSE_LISTENER_METHOD, listener_id).result(msg);
    }
    k_mutex_unlock(&server_mutex);
}

void BridgeTCPServerBase::disconnect() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    _connected = false;
    connection_id = 0;
    k_mutex_unlock(&server_mutex);
}

bool BridgeTCPServerBase::is_listening() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    bool out = _listening;
    k_mutex_unlock(&server_mutex);
    return out;
}

bool BridgeTCPServerBase::is_connected() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    bool out = _connected;
    k_mutex_unlock(&server_mutex);
    return out;
}

uint16_t BridgeTCPServerBase::getPort() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    uint16_t port = _port;
    k_mutex_unlock(&server_mutex);
    return port;
}

String BridgeTCPServerBase::getAddr() {
    k_mutex_lock(&server_mutex, K_FOREVER);
    String hostname = _addr.toString();
    k_mutex_unlock(&server_mutex);
    return hostname;
}

BridgeTCPServerBase::operator bool() const {
    return const_cast<BridgeTCPServerBase*>(this)->is_listening();
}
