/*
    This file is part of the Arduino_RPClite library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.
*/

#ifndef RPCLITE_DECODER_H
#define RPCLITE_DECODER_H

// MsgPack log level
#define DEBUGLOG_DEFAULT_LOG_LEVEL_WARN

#include "MsgPack.h"
#include "transport.h"
#include "rpclite_utils.h"
#include "error.h"
#include "request.h"

using namespace RpcUtils::detail;

#define MIN_RPC_BYTES   4

class IRpcDecoder {
public:
    virtual ~IRpcDecoder() = default;

    IRpcDecoder(const IRpcDecoder&) = delete;
    IRpcDecoder& operator=(const IRpcDecoder&) = delete;

    template<typename... Args>
    bool send_call(const int call_type, const MsgPack::str_t& method, uint32_t& msg_id, Args&&... args) {

        if (call_type!=CALL_MSG && call_type!=NOTIFY_MSG) return false;

        MsgPack::Packer packer;
        packer.clear();

        if (call_type == CALL_MSG){
            msg_id = _msg_id;
            MsgPack::arr_size_t call_size(REQUEST_SIZE);
            packer.serialize(call_size, call_type, msg_id, method);
        } else {
            MsgPack::arr_size_t call_size(NOTIFY_SIZE);
            packer.serialize(call_size, call_type, method);
        }

        MsgPack::arr_size_t arg_size(sizeof...(args));
        packer.serialize(arg_size, std::forward<Args>(args)...);

        if (send(reinterpret_cast<const uint8_t*>(packer.data()), packer.size()) == packer.size()){
            if (call_type == CALL_MSG)
            {
                pending_id_list[pending_id_list_size++] = msg_id;
                _msg_id++;
            }
            return true;
        }
        return false;
    }


    template<typename RType>
    bool get_response(const uint32_t msg_id, RType& result, RpcError& error) {

        if (!response_queued()) return false;

        MsgPack::Unpacker unpacker;
        unpacker.clear();

        if (!unpacker.feed(_raw_buffer + _response_offset, _response_size)) return false;

        MsgPack::arr_size_t resp_size;
        int resp_type;
        uint32_t resp_id;

        if (!unpacker.deserialize(resp_size, resp_type, resp_id)) return false;

        // ReSharper disable once CppDFAUnreachableCode
        if (resp_id != msg_id) return false;

        // msg_id OK packet will be consumed.
        if (resp_type != RESP_MSG) {
            // This should never happen
            error.code = PARSING_ERR;
            error.traceback = "Unexpected response type";
            drop_response();
            return true;
        }

        if (resp_size.size() != RESPONSE_SIZE) {
            // This should never happen
            error.code = PARSING_ERR;
            error.traceback = "Unexpected RPC response size";
            drop_response();
            return true;
        }

        MsgPack::object::nil_t nil;
        if (unpacker.unpackable(nil)){  // No error
            if (!unpacker.deserialize(nil, result)) {
                error.code = PARSING_ERR;
                error.traceback = "Result not parsable (check type)";
                drop_response();
                return true;
            }
        } else {                        // RPC returned an error
            if (!unpacker.deserialize(error, nil)) {
                error.code = PARSING_ERR;
                error.traceback = "RPC Error not parsable (check type)";
                drop_response();
                return true;
            }
        }

        if (_response_offset == 0) reset_packet();
        consume(_response_size, _response_offset);
        reset_response();

        return true;
    }

    void drop_response() {
        if (!response_queued()) return;
        const bool at_head = (_response_offset == 0);
        consume(_response_size, _response_offset);
        reset_response();
        if (at_head) reset_packet();
        _discarded_packets++;
    }

    bool send_response(const MsgPack::Packer& packer) const {
        return send(reinterpret_cast<const uint8_t*>(packer.data()), packer.size()) == packer.size();
    }

    MsgPack::str_t fetch_rpc_method(){

        if (!packet_incoming()){return "";}

        if (_packet_type != CALL_MSG && _packet_type != NOTIFY_MSG) {
            return ""; // No RPC
        }

        MsgPack::Unpacker unpacker;

        unpacker.clear();
        if (!unpacker.feed(_raw_buffer, _packet_size)) {    // feed should not fail at this point
            discard();
            return "";
        };

        int msg_type;
        MsgPack::str_t method;
        MsgPack::arr_size_t req_size;

        if (!unpacker.deserialize(req_size, msg_type)) {
            discard();
            return ""; // Header not unpackable
        }

        // ReSharper disable once CppDFAUnreachableCode
        if (msg_type == CALL_MSG && req_size.size() == REQUEST_SIZE) {
            uint32_t msg_id;
            if (!unpacker.deserialize(msg_id, method)) {
                discard();
                return ""; // Method not unpackable
            }
        } else if (msg_type == NOTIFY_MSG && req_size.size() == NOTIFY_SIZE) {
            if (!unpacker.deserialize(method)) {
                discard();
                return ""; // Method not unpackable
            }
        } else {
            discard();
            return ""; // Invalid request size/type
        }

        return method;

    }

    size_t get_request(uint8_t* buffer, size_t buffer_size) {

        if (_packet_type != CALL_MSG && _packet_type != NOTIFY_MSG) {
            return 0; // No RPC
        }

        return pop_packet(buffer, buffer_size);
    }

    void decode(size_t max_rpc_size = RPCLITE_REQUEST_BUFFER_SIZE){
        advance();
        parse_packet(max_rpc_size);
    }

    void advance() {

        if (_transport->available() && !buffer_full()) {
            size_t bytes_read = _transport->read(_raw_buffer + _bytes_stored, _capacity - _bytes_stored);
            _bytes_stored += bytes_read;
        }

    }

    void parse_packet(size_t max_rpc_size = RPCLITE_REQUEST_BUFFER_SIZE){

        size_t offset = 0;

        if (packet_incoming()) {
            if (response_queued()) return;  // parsing complete
            offset = _response_offset;      // looking for a RESP
        } else if (response_queued() && _response_offset == 0) {
            _packet_size = _response_size;
            _packet_type = RESP_MSG;
            return;;
        }

        if (offset < _bytes_stored) {
            const uint8_t first = _raw_buffer[offset];
            if (first != 0x93 && first != 0x94) {
                resync(offset); return;
            }
            const int length = frameLength(_raw_buffer+offset, _bytes_stored-offset, max_rpc_size);
            if (length == 0) return;
            if (length < 0) { resync(offset); return; }
            MsgPack::Unpacker unpacker;
            size_t container_size = 0;
            uint32_t msg_id = 0;
            int type = NO_MSG;
            if (!unpacker.feed(_raw_buffer+offset, length) ||
                !isValidRpc(unpacker, type, container_size, msg_id)) {
                consume(length, offset); ++_discarded_packets; return;
                }
            if (offset == 0) { _packet_type=type; _packet_size=length; }
            if (type == RESP_MSG) {
                _response_offset=offset; _response_size=length;
                if (!is_pending_id(msg_id)) {
                    drop_response();
                }
            }
            else _response_offset=offset+length;
        }

    }

    bool packet_incoming() const { return _packet_size >= MIN_RPC_BYTES; }
    bool response_queued() const { return _response_size > 0; }
    int packet_type() const { return _packet_type; }
    size_t get_packet_size() const { return _packet_size;}
    size_t size() const {return _bytes_stored;}
    size_t buffer_size() const {return _capacity;}
    uint32_t get_discarded_packets() const {return _discarded_packets;}

    void flush()
    {
        if (_packet_size>0){_discarded_packets++;}
        if (_response_size>0){_discarded_packets++;}
        _dropped_bytes += _bytes_stored;

        _packet_type = NO_MSG;
        _packet_size = 0;
        _response_offset = 0;
        _response_size = 0;
        _bytes_stored = 0;
        _transport->flush();
    }

    void enableResync(bool value) {auto_resync = value;}

    void resync(size_t offset = 0) {
        if (!auto_resync) return;
        if (response_queued() || (offset== 0 && packet_incoming())) return;

        size_t bytes_to_consume = 1;    // 1st byte is always consumed
        for (size_t i = offset + 1; i < _bytes_stored; i++)
        {
            if (i + 1 >= _bytes_stored) {
                if (_raw_buffer[i] == 0x94 || _raw_buffer[i] == 0x93) break;
                bytes_to_consume++;
                continue;
            }
            if ((_raw_buffer[i] == 0x94 && _raw_buffer[i+1] == RESP_MSG) ||
                (_raw_buffer[i] == 0x94 && _raw_buffer[i+1] == CALL_MSG) ||
                (_raw_buffer[i] == 0x93 && _raw_buffer[i+1] == NOTIFY_MSG)) {
                break;
            }
            bytes_to_consume++;
        }
        consume(bytes_to_consume, offset);
        _dropped_bytes += bytes_to_consume;
    }

    void discard() {
        consume(_packet_size);
        reset_packet();
        _discarded_packets++;
    }

    void reset() {
        flush();
        _discarded_packets = 0;
        _dropped_bytes = 0;
        _msg_id = 0;
        pending_id_list_size = 0;
    }

    bool remove_pending_id(uint32_t msg_id) {
        for (size_t i = 0; i < pending_id_list_size; i++) {
            if (pending_id_list[i] == msg_id) {
                pending_id_list[i] = pending_id_list[--pending_id_list_size];
                return true;
            }
        }
        return false;
    }

    friend class RpcDecoderManager;
    friend class DecoderTester;

protected:
    IRpcDecoder(ITransport& transport, uint8_t* buffer, size_t capacity)
        : _transport(&transport), _raw_buffer(buffer), _capacity(capacity) {}

private:
    ITransport* _transport;
    uint8_t* _raw_buffer;
    size_t _capacity;
    size_t _bytes_stored = 0;
    int _packet_type = NO_MSG;
    size_t _packet_size = 0;
    size_t _response_offset = 0;
    size_t _response_size = 0;
    uint32_t _msg_id = 0;
    uint32_t _discarded_packets = 0;
    uint32_t _dropped_bytes = 0;
    bool auto_resync = true;
    uint32_t pending_id_list[RPCLITE_MAX_PENDING_CALLS]{};
    size_t pending_id_list_size = 0;

    int frameLength(const uint8_t* data, size_t size, size_t limit) {
        size_t pos = 0;
        uint64_t pending = 1;
        while (pending) {
            if (pos >= size) return 0;
            const uint8_t c = data[pos++];
            --pending;
            uint64_t payload = 0, children = 0;
            unsigned width = 0;
            bool variable = false, map = false, container = false, extension = false;
            if (c <= 0x7f || c >= 0xe0 || c == 0xc0 || c == 0xc2 || c == 0xc3) {}
            else if ((c & 0xf0) == 0x80) children = 2*(c & 15);
            else if ((c & 0xf0) == 0x90) children = c & 15;
            else if ((c & 0xe0) == 0xa0) payload = c & 31;
            else switch (c) {
                case 0xc4: case 0xd9: width=1; variable=true; break;
                case 0xc5: case 0xda: width=2; variable=true; break;
                case 0xc6: case 0xdb: width=4; variable=true; break;
                case 0xc7: width=1; variable=true; extension=true; break;
                case 0xc8: width=2; variable=true; extension=true; break;
                case 0xc9: width=4; variable=true; extension=true; break;
                case 0xca: case 0xce: case 0xd2: payload=4; break;
                case 0xcb: case 0xcf: case 0xd3: payload=8; break;
                case 0xcc: case 0xd0: payload=1; break;
                case 0xcd: case 0xd1: payload=2; break;
                case 0xd4: payload=2; break;
                case 0xd5: payload=3; break;
                case 0xd6: payload=5; break;
                case 0xd7: payload=9; break;
                case 0xd8: payload=17; break;
                case 0xdc: width=2; container=true; break;
                case 0xdd: width=4; container=true; break;
                case 0xde: width=2; container=true; map=true; break;
                case 0xdf: width=4; container=true; map=true; break;
                default: return -1; // reserved 0xc1
            }
            if (width) {
                if (width > size-pos) return 0;
                uint64_t length=0;
                for (unsigned i=0; i<width; ++i) length=(length<<8)|data[pos++];
                if (container) children=length*(map ? 2 : 1);
                if (variable) payload=length+(extension ? 1 : 0);
            }
            pending += children;
            if (pos > limit || payload > limit-pos || pending > limit-pos-payload) return -1;
            if (payload > size-pos) return 0;
            pos += (size_t)payload;
        }
        return (int)pos;
    }

    bool buffer_full() const { return _bytes_stored == _capacity; }

    bool buffer_empty() const { return _bytes_stored == 0;}

    // This is a blocking send, under the assumption _transport->write will always succeed eventually
    size_t send(const uint8_t* data, const size_t size) const {

        size_t offset = 0;

        while (offset < size) {
            size_t bytes_written = _transport->write(data + offset, size - offset);
            offset += bytes_written;
        }

        return offset;
    }

    size_t pop_packet(uint8_t* buffer, size_t buffer_size) {
        if (!packet_incoming()) return 0;

        size_t packet_size = get_packet_size();
        if (packet_size > buffer_size) return 0;

        memcpy(buffer, _raw_buffer, packet_size);

        reset_packet();
        return consume(packet_size);
    }

    void reset_packet() {
        _packet_type = NO_MSG;
        _packet_size = 0;
    }

    void reset_response() {
        _response_offset = 0;
        _response_size = 0;
    }

    size_t consume(size_t size, size_t offset = 0) {
        // Boundary checks
        if (size == 0 || size > _bytes_stored || offset > _bytes_stored - size) return 0;

        const size_t tail = _bytes_stored - offset - size;   // bytes after the removed region
        if (tail > 0) {
            memmove(_raw_buffer + offset, _raw_buffer + offset + size, tail);
        }

        if (_response_offset >= offset + size) {
            _response_offset -= size;
        }

        _bytes_stored -= size;
        return size;
    }

    void set_pending_id(uint32_t msg_id) {
        if (pending_id_list_size < RPCLITE_MAX_PENDING_CALLS) {
            pending_id_list[pending_id_list_size++] = msg_id;
        }
    }

    bool is_pending_id(uint32_t msg_id) const {
        for (size_t i = 0; i < pending_id_list_size; i++) {
            if (pending_id_list[i] == msg_id) return true;
        }
        return false;
    }
};

template<size_t BufferSize = RPCLITE_DECODER_BUFFER_SIZE>
class RpcDecoder final : public IRpcDecoder {
public:
    static constexpr size_t buffer_size = BufferSize;

private:
    explicit RpcDecoder(ITransport& transport)
        : IRpcDecoder(transport, _storage, BufferSize) {}

    uint8_t _storage[BufferSize]{};

    friend class RpcDecoderManager;
    friend class DecoderTester;
};

#endif
