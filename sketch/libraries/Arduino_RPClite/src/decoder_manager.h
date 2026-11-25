/*
    This file is part of the Arduino_RPClite library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.
    
*/

// This is a static implementation of the decoder manager

#ifndef RPCLITE_DECODER_MANAGER_H
#define RPCLITE_DECODER_MANAGER_H

#include <array>
#include "transport.h"
#include "decoder.h"

class RpcDecoderManager {

public:

    RpcDecoderManager(const RpcDecoderManager&) = delete;
    RpcDecoderManager& operator=(const RpcDecoderManager&) = delete;

    template<size_t RpcSize = RPCLITE_DECODER_BUFFER_SIZE>
    IRpcDecoder* getDecoder(ITransport& transport) {
        for (auto& entry : decoders_) {
            if (entry.transport == &transport) {
                return entry.decoder;
            }

            if (entry.transport == nullptr) {
                entry.transport = &transport;
                entry.decoder = new RpcDecoder<RpcSize>(transport);

                decoders_count++;
                return entry.decoder;
            }
        }

        return nullptr;
    }

    size_t getDecodersCount() const {
        return decoders_count;
    }

    static RpcDecoderManager& getInstance() {
        static RpcDecoderManager instance; // thread-safe in C++11+
        return instance;
    }

private:

    RpcDecoderManager(){};

    struct Entry {
        ITransport* transport = nullptr;
        IRpcDecoder* decoder = nullptr;
    };

    std::array<Entry, RPCLITE_MAX_TRANSPORTS> decoders_;
    size_t decoders_count{0};
};

#endif //RPCLITE_DECODER_MANAGER_H