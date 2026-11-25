/*
    This file is part of the Arduino_RPClite library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.
    
*/

#ifndef RPCLITE_SERVER_H
#define RPCLITE_SERVER_H

#include <utility>

#include "request.h"
#include "error.h"
#include "dispatcher.h"
#include "decoder.h"
#include "decoder_manager.h"

#ifndef RPCLITE_SERVER_MAX_CALLBACKS
#define RPCLITE_SERVER_MAX_CALLBACKS   100
#endif

class RPCServer {

public:
    explicit RPCServer(ITransport& t) : decoder(RpcDecoderManager::getInstance().getDecoder(t)) {}

    explicit RPCServer(IRpcDecoder* dec) : decoder(dec) {}

    operator bool() const {return decoder != nullptr;}

    template<typename F>
    bool bind(const MsgPack::str_t& name, F&& func, MsgPack::str_t tag=""){
        return dispatcher.bind(name, func, tag);
    }

    bool hasTag(const MsgPack::str_t& name, MsgPack::str_t tag) const {
        return dispatcher.hasTag(name, tag);
    }

    template<size_t RpcSize = RPCLITE_REQUEST_BUFFER_SIZE>
    bool run() {

        RPCRequest<RpcSize> req;

        if (!get_rpc(req)) return false; // Populate local request

        process_request(req);      // Process local data

        return send_response(req);           // Send local data

    }

    template<size_t RpcSize = RPCLITE_REQUEST_BUFFER_SIZE>
    bool get_rpc(RPCRequest<RpcSize>& req, MsgPack::str_t tag="") {
        decoder->decode(RpcSize);

        const MsgPack::str_t method = decoder->fetch_rpc_method();

        if (method == "") return false;
        if (tag != "" && !dispatcher.hasTag(method, tag)) return false;

        req.size = decoder->get_request(req.buffer, RpcSize);
        return req.size > 0;
    }

    template<size_t RpcSize = RPCLITE_REQUEST_BUFFER_SIZE>
    void process_request(RPCRequest<RpcSize>& req) {

        if (!req.unpack_request_headers()) {
            req.reset();
            return;
        }

        req.pack_response_headers();

        dispatcher.call(req.method, req.unpacker, req.packer);

    }

    template<size_t RpcSize = RPCLITE_REQUEST_BUFFER_SIZE>
    bool send_response(const RPCRequest<RpcSize>& req) const {

        if (req.type == NO_MSG || req.packer.size() == 0) {
            return true; // No response to send
        }

        if (req.type == NOTIFY_MSG) return true;

        return decoder->send_response(req.packer);

    }

    uint32_t get_discarded_packets() const {return decoder->get_discarded_packets();}

    IRpcDecoder* get_decoder() {return decoder;}

private:
    IRpcDecoder* decoder = nullptr;
    RpcFunctionDispatcher<RPCLITE_SERVER_MAX_CALLBACKS> dispatcher{};
    
};

#endif //RPCLITE_SERVER_H
