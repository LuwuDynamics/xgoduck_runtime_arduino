/*
    This file is part of the Arduino_RPClite library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.
    
*/

#ifndef RPCLITE_CLIENT_H
#define RPCLITE_CLIENT_H
#include "error.h"
#include "decoder.h"
#include "decoder_manager.h"


class RPCClient {
    IRpcDecoder* decoder=nullptr;
    uint32_t timeout_ms = 0;

public:
    RpcError lastError;

    explicit RPCClient(ITransport& t) : decoder(RpcDecoderManager::getInstance().getDecoder(t)) {}

    explicit RPCClient(IRpcDecoder* dec) : decoder(dec) {}

    operator bool() const {return decoder != nullptr;}

    RPCClient& setTimeout(uint32_t value) {
        timeout_ms = value;
        return *this;
    }

    template<typename... Args>
    void notify(const MsgPack::str_t& method, Args&&... args)  {
        uint32_t _id;
        decoder->send_call(NOTIFY_MSG, method, _id, std::forward<Args>(args)...);
    }

    template<typename RType, typename... Args>
    bool call(const MsgPack::str_t& method, RType& result, Args&&... args) {

        uint32_t msg_id_wait;

        if(!send_rpc(method, msg_id_wait, std::forward<Args>(args)...)) {
            lastError.code = GENERIC_ERR;
            lastError.traceback = "Failed to send RPC call";
            return false;
        }

        // blocking call
        RpcError tmp_error;
        unsigned long start = millis();
        while (!get_response(msg_id_wait, result, tmp_error)) {
            if (timeout_ms > 0 && millis() - start > timeout_ms) {
                lastError.code = TIMEOUT_ERR;
                lastError.traceback = "RPC call timed out";
                cancel(msg_id_wait);
                return false;
            }
        }

        return (lastError.code == NO_ERR);

    }

    void cancel(uint32_t wait_id) {
        decoder->remove_pending_id(wait_id);
    }

    template<typename... Args>
    bool send_rpc(const MsgPack::str_t& method, uint32_t& wait_id, Args&&... args) {
        uint32_t msg_id;
        if (decoder->send_call(CALL_MSG, method, msg_id, std::forward<Args>(args)...)) {
            wait_id = msg_id;
            return true;
        }
        return false;
    }

    template<typename RType>
    bool get_response(const uint32_t wait_id, RType& result, RpcError& error) {
        decoder->decode();

        if (decoder->get_response(wait_id, result, error)) {
            lastError.copy(error);
            decoder->remove_pending_id(wait_id);
            return true;
        }
        return false;
    }

    uint32_t get_discarded_packets() const {return decoder->get_discarded_packets();}

    IRpcDecoder* get_decoder() {return decoder;}

};

#endif //RPCLITE_CLIENT_H
