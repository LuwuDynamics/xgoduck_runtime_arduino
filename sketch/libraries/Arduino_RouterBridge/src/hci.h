/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once

#ifndef BRIDGE_HCI_H
#define BRIDGE_HCI_H

#include <string.h>
#include "bridge.h"

#define HCI_OPEN_METHOD     "hci/open"
#define HCI_CLOSE_METHOD    "hci/close"
#define HCI_SEND_METHOD     "hci/send"
#define HCI_RECV_METHOD     "hci/recv"
#define HCI_AVAIL_METHOD    "hci/avail"
#define HCI_BUFFER_SIZE     1024    // Matches Linux kernel HCI_MAX_ACL_SIZE (1024 bytes)

// Lightweight binary view to avoid dynamic allocation during serialization
struct BinaryView {
    const uint8_t *data;
    size_t size;

    BinaryView(const uint8_t *d, size_t s) : data(d), size(s) {

    }

    // MsgPack serialization support
    void to_msgpack(MsgPack::Packer &packer) const {
        packer.pack(data, size);
    }
};

template<size_t BufferSize=HCI_BUFFER_SIZE> class BridgeHCI {
    BridgeClass *bridge;
    struct k_mutex hci_mutex;
    bool initialized = false;
    MsgPack::bin_t<uint8_t> recv_buffer;

public:
    explicit BridgeHCI(BridgeClass &bridge): bridge(&bridge) {

    }

    bool begin(const char *device = "hci0") {
        k_mutex_init(&hci_mutex);

        k_mutex_lock(&hci_mutex, K_FOREVER);
        // Pre-allocate recv buffer to avoid allocations during recv calls
        recv_buffer.reserve(BufferSize);

        if (!(*bridge) && !bridge->begin()) {
            k_mutex_unlock(&hci_mutex);
            return false;
        }

        bool result;
        if (bridge->call(HCI_OPEN_METHOD, String(device)).result(result)) {
            initialized = result;
        }

        k_mutex_unlock(&hci_mutex);
        return result;
    }

    void end() {
        k_mutex_lock(&hci_mutex, K_FOREVER);

        if (!initialized) {
            k_mutex_unlock(&hci_mutex);
            return;
        }

        bool result;
        bridge->call(HCI_CLOSE_METHOD).result(result);
        initialized = false;

        k_mutex_unlock(&hci_mutex);
    }

    explicit operator bool() const {
        k_mutex_lock(&hci_mutex, K_FOREVER);
        bool out = initialized;
        k_mutex_unlock(&hci_mutex);
        return out;
    }

    int send(const uint8_t *buffer, size_t size) {
        k_mutex_lock(&hci_mutex, K_FOREVER);

        if (!initialized) {
            k_mutex_unlock(&hci_mutex);
            return -1;
        }

        BinaryView send_buffer(buffer, size);
        size_t bytes_sent;
        const bool ret = bridge->call(HCI_SEND_METHOD, send_buffer).result(bytes_sent);

        k_mutex_unlock(&hci_mutex);

        if (ret) {
            return bytes_sent;
        }
        return -1;
    }

    int recv(uint8_t *buffer, size_t max_size) {
        k_mutex_lock(&hci_mutex, K_FOREVER);

        if (!initialized) {
            k_mutex_unlock(&hci_mutex);
            return -1;
        }

        recv_buffer.clear();
        bool ret = bridge->call(HCI_RECV_METHOD, max_size).result(recv_buffer);

        if (ret) {
            size_t bytes_to_copy = recv_buffer.size() < max_size ? recv_buffer.size() : max_size;
            // Use memcpy for faster bulk copy
            if (bytes_to_copy > 0) {
                memcpy(buffer, recv_buffer.data(), bytes_to_copy);
            }
            k_mutex_unlock(&hci_mutex);
            return bytes_to_copy;
        }

        k_mutex_unlock(&hci_mutex);
        return 0;
    }

    int available() {

        k_mutex_lock(&hci_mutex, K_FOREVER);

        if (!initialized) {
            k_mutex_unlock(&hci_mutex);
            return 0;
        }

        bool result;
        bool ret = bridge->call(HCI_AVAIL_METHOD).result(result);

        k_mutex_unlock(&hci_mutex);

        return ret && result;
    }

};

namespace RouterBridge {
    inline BridgeHCI<> HCI(Bridge);
}

#ifndef ARDUINO_ROUTERBRIDGE_PROVIDES_SERIAL
// Make available in global namespace for backward compatibility
// but not on platforms that require this library in all builds
// to avoid clashes with ArduinoBLE
using RouterBridge::HCI;
#endif

#endif // BRIDGE_HCI_H
