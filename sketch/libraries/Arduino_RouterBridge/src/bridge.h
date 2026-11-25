/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once

#ifndef ROUTER_BRIDGE_H
#define ROUTER_BRIDGE_H

#define RESET_METHOD "$/reset"
#define BIND_METHOD "$/register"
#define GET_VERSION_METHOD "$/version"
#define SET_BUF_METHOD "$/setMaxMsgSize"

//#define BRIDGE_ERROR "$/bridgeLog"

#ifndef UPDATE_THREAD_STACK_SIZE
#define UPDATE_THREAD_STACK_SIZE    4096
#endif

#define UPDATE_THREAD_PRIORITY      5

#define DEFAULT_SERIAL_BAUD         115200
// With BridgeProtocol::Reliable, the default maximum rate negotiated with the
// router. 1000000 is generated exactly by the UNO Q UART, and it is the
// fastest rate tested to be reliable on that board.
#ifndef BRIDGE_DEFAULT_MAX_BAUD
#define BRIDGE_DEFAULT_MAX_BAUD     1000000
#endif
// With BridgeProtocol::Reliable every session starts at this rate.
#ifndef BRIDGE_BASE_BAUD
#define BRIDGE_BASE_BAUD            115200
#endif
#define BRIDGE_RPC_BUFFER_SIZE      DEFAULT_RPC_BUFFER_SIZE

#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <Arduino_RPClite.h>

#include <utility>
#include <vector>

#include "seriallink_transport.h"

// Protocol used on the serial port to talk with the router.
enum class BridgeProtocol {
    // Plain msgpack-rpc: no integrity check, no flow control.
    Raw,
    // msgpack-rpc over the seriallink protocol: integrity check,
    // retransmission and flow control. If the router restarts, the session
    // is re-established and the provided methods are registered again.
    // Requires a router with --serial-protocol set to "reliable" (or "auto").
    // The serial port starts at BRIDGE_BASE_BAUD, then the highest rate
    // supported by both sides (up to the baud passed to begin() and to the
    // router --serial-max-baudrate) is negotiated.
    Reliable,
};

// Default protocol: the UNO Q talks to the router over a real UART, that
// needs the integrity check and the flow control of the seriallink protocol.
// Other boards (e.g. the VENTUNO Q) use a serial port emulated over USB, that
// is already reliable.
#ifndef BRIDGE_DEFAULT_PROTOCOL
#if defined(ARDUINO_UNO_Q)
#define BRIDGE_DEFAULT_PROTOCOL BridgeProtocol::Reliable
#else
#define BRIDGE_DEFAULT_PROTOCOL BridgeProtocol::Raw
#endif
#endif


void updateEntryPoint(void *, void *, void *);

template<typename... Args>
class RpcCall {

    RpcError error;

    void setError(int code, MsgPack::str_t text) {
        k_mutex_lock(&call_mutex, K_FOREVER);
        error.code = code;
        error.traceback = std::move(text);
        k_mutex_unlock(&call_mutex);
    }

public:

    RpcCall(const MsgPack::str_t& m, RPCClient* c, struct k_mutex* rm, struct k_mutex* wm, SerialLinkTransport* l, Args&&... args): method(m), client(c), read_mutex(rm), write_mutex(wm), link(l), callback_params(std::forward_as_tuple(std::forward<Args>(args)...)) {
        k_mutex_init(&call_mutex);
        setError(GENERIC_ERR, "This call is not yet executed");
    }

    bool isError() {
        k_mutex_lock(&call_mutex, K_FOREVER);
        const bool out = error.code > NO_ERR;
        k_mutex_unlock(&call_mutex);
        return out;
    }

    int getErrorCode() {
        k_mutex_lock(&call_mutex, K_FOREVER);
        const int out = error.code;
        k_mutex_unlock(&call_mutex);
        return out;
    }

    MsgPack::str_t getErrorMessage() {
        k_mutex_lock(&call_mutex, K_FOREVER);
        MsgPack::str_t out = error.traceback;
        k_mutex_unlock(&call_mutex);
        return out;
    }

    template<typename RType> bool result(RType& result) {

        if (!atomic_cas(&_executed, 0, 1)){
            // this thread lost the race
            setError(GENERIC_ERR, "This call is no longer available");
            return false;
        }

        while (true) {
            if (k_mutex_lock(write_mutex, K_MSEC(10)) == 0) {
                std::apply([this](const auto&... elems) {
                    client->send_rpc(method, msg_id_wait, elems...);
                }, callback_params);
                if (link) sent_session = link->lastWriteSession();
                k_mutex_unlock(write_mutex);
                break;
            } else {
                k_yield();
            }
        }

        while(true) {
            if (link && link->session() != sent_session) {
                // The session in which the call was sent has been reset: the
                // response will never arrive
                setError(GENERIC_ERR, "Connection with the router reset");
                break;
            }
            if (k_mutex_lock(read_mutex, K_MSEC(10)) == 0 ) {
                RpcError temp_err;
                if (client->get_response(msg_id_wait, result, temp_err)) {
                    k_mutex_unlock(read_mutex);
                    // if (error.code == PARSING_ERR) {
                    //     k_mutex_lock(write_mutex, K_FOREVER);
                    //     client->notify(BRIDGE_ERROR, error.traceback);
                    //     k_mutex_unlock(write_mutex);
                    // }
                    setError(temp_err.code, temp_err.traceback);
                    break;
                }
                k_mutex_unlock(read_mutex);
                k_msleep(1);
            } else {
                k_yield();
            }
        }

        return !isError();
    }

    bool result() {
        MsgPack::object::nil_t nil;
        return result(nil);
    }

    ~RpcCall(){
        result();
    }

    operator bool() {
        return result();
    }

private:
    uint32_t msg_id_wait{};
    atomic_t _executed = ATOMIC_INIT(0);;

    MsgPack::str_t method;
    RPCClient* client;
    struct k_mutex* read_mutex;
    struct k_mutex* write_mutex;
    SerialLinkTransport* link;
    uint32_t sent_session = 0;
    struct k_mutex call_mutex{};
    std::tuple<Args...> callback_params;
};

class BridgeClass {

    RPCClient* client = nullptr;
    RPCServer* server = nullptr;
    HardwareSerial* serial_ptr = nullptr;
    ITransport* transport = nullptr;
    SerialLinkTransport* link_transport = nullptr; // only with BridgeProtocol::Reliable
    uint32_t seen_session = 0;
    std::vector<MsgPack::str_t> provided;          // methods to register again after a session reset

    struct k_mutex read_mutex{};
    struct k_mutex write_mutex{};
    struct k_mutex bridge_mutex{};

    k_tid_t upd_tid{};
    k_thread_stack_t *upd_stack_area{};
    struct k_thread upd_thread_data{};

    bool started = false;

public:

    explicit BridgeClass(HardwareSerial& serial) {
        serial_ptr = &serial;
    }

    operator bool() {
        return is_started();
    }

    bool is_started() {
        k_mutex_lock(&bridge_mutex, K_FOREVER);
        bool out = started;
        k_mutex_unlock(&bridge_mutex);
        return out;
    }

    // Initialize the bridge. With BridgeProtocol::Raw, baud is the rate of the
    // serial port (DEFAULT_SERIAL_BAUD if 0). With BridgeProtocol::Reliable,
    // it is the maximum rate that can be negotiated with the router
    // (BRIDGE_DEFAULT_MAX_BAUD if 0, every session starts at
    // BRIDGE_BASE_BAUD).
    bool begin(unsigned long baud=0, BridgeProtocol protocol=BRIDGE_DEFAULT_PROTOCOL) {
        k_mutex_init(&read_mutex);
        k_mutex_init(&write_mutex);
        k_mutex_init(&bridge_mutex);

        if (is_started()) return true;

        k_mutex_lock(&bridge_mutex, K_FOREVER);

        if (baud == 0) {
            baud = protocol == BridgeProtocol::Reliable ? BRIDGE_DEFAULT_MAX_BAUD : DEFAULT_SERIAL_BAUD;
        }
        serial_ptr->begin(protocol == BridgeProtocol::Reliable ? BRIDGE_BASE_BAUD : baud);
        while (!*serial_ptr){
            k_yield();
        }
        if (protocol == BridgeProtocol::Reliable) {
            link_transport = new SerialLinkTransport(*serial_ptr);
            link_transport->begin(BRIDGE_BASE_BAUD, baud);
            transport = link_transport;
        } else {
            transport = new SerialTransport(*serial_ptr);
        }

        client = new RPCClient(*transport);
        server = new RPCServer(*transport);

        upd_stack_area = k_thread_stack_alloc(UPDATE_THREAD_STACK_SIZE, 0);
        upd_tid = k_thread_create(&upd_thread_data, upd_stack_area,
                                UPDATE_THREAD_STACK_SIZE,
                                updateEntryPoint,
                                NULL, NULL, NULL,
                                UPDATE_THREAD_PRIORITY, 0, K_NO_WAIT);
        k_thread_name_set(upd_tid, "bridge");

        bool res = false;
        while (true) {
            started = call(RESET_METHOD).result(res) && res;
            if (started || !link_transport) break;
            if (link_transport->session() == seen_session) {
                k_msleep(250);
                continue;
            }
            // The session has been reset during the call: retry in the new one
            drop_session_state();
        }
        notify(SET_BUF_METHOD, BRIDGE_RPC_BUFFER_SIZE);     // no effect if Router is not exposing SET_BUF_METHOD
        k_mutex_unlock(&bridge_mutex);
        return res;
    }

    // The seriallink transport (for diagnostics), nullptr with BridgeProtocol::Raw.
    SerialLinkTransport* getLinkTransport() {
        return link_transport;
    }

    bool getRouterVersion(MsgPack::str_t& version) {
        return call(GET_VERSION_METHOD).result(version);
    }

    template<typename F>
    bool provide(const MsgPack::str_t& name, F&& func) {
        k_mutex_lock(&bridge_mutex, K_FOREVER);
        bool res;
        bool out = call(BIND_METHOD, name).result(res) && res && server->bind(name, func);
        if (out) provided.push_back(name);
        k_mutex_unlock(&bridge_mutex);
        return out;
    }

    template<typename F>
    bool provide_safe(const MsgPack::str_t& name, F&& func) {
        k_mutex_lock(&bridge_mutex, K_FOREVER);
        bool res;
        bool out = call(BIND_METHOD, name).result(res) && res && server->bind(name, func, "__safe__");
        if (out) provided.push_back(name);
        k_mutex_unlock(&bridge_mutex);
        return out;
    }

    void update() {

        check_session();

        // Lock read mutex
        if (k_mutex_lock(&read_mutex, K_MSEC(10)) != 0 ) return;

        const uint32_t session = current_session();
        RPCRequest<BRIDGE_RPC_BUFFER_SIZE> req;
        if (!server->get_rpc(req)) {
            k_mutex_unlock(&read_mutex);
            k_msleep(1);
            return;
        }

        k_mutex_unlock(&read_mutex);

        server->process_request(req);

        // The request came from a session that has been reset: nobody is
        // waiting for the response anymore
        if (current_session() != session) return;

        // Lock write mutex
        while (true) {

            if (k_mutex_lock(&write_mutex, K_MSEC(10)) == 0){
                server->send_response(req);
                k_mutex_unlock(&write_mutex);
                break;
            } else {
                k_yield();
            }

        }

    }

    template<typename... Args>
    RpcCall<Args...> call(const MsgPack::str_t& method, Args&&... args) {
       return RpcCall<Args...>(method, client, &read_mutex, &write_mutex, link_transport, std::forward<Args>(args)...);
    }

    // Keep the report task out of blocking stream writes. Serialize the same
    // RPClite notification as notify(), and retain control/cal replies on failure.
    bool tryNotify(const MsgPack::str_t& method, const MsgPack::bin_t<uint8_t>& bytes) {
        const uint32_t session = link_transport->session();
        MsgPack::Packer packer;
        packer.serialize(MsgPack::arr_size_t(NOTIFY_SIZE), NOTIFY_MSG, method);
        packer.serialize(MsgPack::arr_size_t(1), bytes);
        if (k_mutex_lock(&write_mutex, K_MSEC(1)) != 0) return false;
        const bool sent = link_transport->tryWritePacket(
            reinterpret_cast<const uint8_t*>(packer.data()), packer.size(), session);
        k_mutex_unlock(&write_mutex);
        return sent;
    }

    template<typename... Args>
    void notify(const MsgPack::str_t method, Args&&... args)  {
        while (true) {
            if (k_mutex_lock(&write_mutex, K_MSEC(10)) == 0) {
                client->notify(method, std::forward<Args>(args)...);
                k_mutex_unlock(&write_mutex);
                break;
            }
            k_yield();
        }
    }

private:

    uint32_t current_session() const {
        return link_transport ? link_transport->session() : 0;
    }

    // Discards the data received in the sessions that have been reset, and
    // lets the transport deliver the data of the new session.
    void drop_session_state() {
        const uint32_t session = link_transport->session();
        k_mutex_lock(&read_mutex, K_FOREVER);
        client->get_decoder()->reset();
        k_mutex_unlock(&read_mutex);
        seen_session = session;
        link_transport->ackReset(session);
    }

    // Handles a session reset (the router restarted or the link timed out):
    // the router sees a new connection, so the provided methods must be
    // registered again.
    void check_session() {
        if (!link_transport || link_transport->session() == seen_session) return;
        drop_session_state();
        k_mutex_lock(&bridge_mutex, K_FOREVER);
        for (const auto& name : provided) {
            bool res;
            call(BIND_METHOD, name).result(res);
        }
        notify(SET_BUF_METHOD, BRIDGE_RPC_BUFFER_SIZE);
        k_mutex_unlock(&bridge_mutex);
    }

    void update_safe() {

        // Lock read mutex
        if (k_mutex_lock(&read_mutex, K_MSEC(10)) != 0 ) return;

        RPCRequest<BRIDGE_RPC_BUFFER_SIZE> req;
        const uint32_t session = current_session();
        if (!server->get_rpc(req, "__safe__")) {
            k_mutex_unlock(&read_mutex);
            k_msleep(1);
            return;
        }

        k_mutex_unlock(&read_mutex);

        server->process_request(req);

        // The request came from a session that has been reset: nobody is
        // waiting for the response anymore
        if (current_session() != session) return;

        // Lock write mutex
        while (true) {

            if (k_mutex_lock(&write_mutex, K_MSEC(10)) == 0){
                server->send_response(req);
                k_mutex_unlock(&write_mutex);
                break;
            } else {
                k_yield();
            }

        }

    }

    friend class BridgeClassUpdater;

};

extern BridgeClass Bridge;

class BridgeClassUpdater {
public:
    static void safeUpdate(BridgeClass* bridge) {
        if (*bridge) {
            bridge->update_safe();
        }
    }

private:
    BridgeClassUpdater() = delete; // prevents instantiation
};

inline void updateEntryPoint(void *, void *, void *){
    while (true) {
        if (Bridge) {
            Bridge.update();
        }
        k_yield();
    }
}

static void safeUpdate(){
    BridgeClassUpdater::safeUpdate(&Bridge);
}

// leave as is
void __attribute__((weak)) __loopHook(void){
    k_yield();
    safeUpdate();
}

#endif // ROUTER_BRIDGE_H
