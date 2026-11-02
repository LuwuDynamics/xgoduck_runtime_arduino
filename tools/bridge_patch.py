# Portions adapted from Arduino_RouterBridge and Arduino_RPClite.
# Copyright (C) Arduino s.r.l. and/or its affiliated companies.
# Upstream portions are subject to MPL-2.0; see LICENSES/MPL-2.0.txt.
# Modified for bounded RPC waits and retry-safe XGO Duck Bridge recovery.
"""Bounded, retry-safe changes for the pinned RouterBridge/RPClite versions."""
from pathlib import Path


def replace_once(source, old, new):
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError(f'Unexpected pinned library source: {old[:70]!r}')
    return source.replace(old, new, 1)


RPC_RESULT = '''    template<typename RType> bool result(RType& result) {
        // DUCK_BOUNDED_RPC: one deadline includes acquiring both RPC locks.
        if (!atomic_cas(&_executed, 0, 1)) return false;
        if (!client) { setError(GENERIC_ERR, "RPC client unavailable"); return false; }
        const uint32_t start = k_uptime_get_32();
        bool sent = false;
        while ((uint32_t)(k_uptime_get_32() - start) < DUCK_BRIDGE_RPC_TIMEOUT_MS) {
            if (k_mutex_lock(write_mutex, K_MSEC(5)) == 0) {
                sent = std::apply([this](const auto&... elems) {
                    return client->send_rpc(method, msg_id_wait, elems...);
                }, callback_params);
                k_mutex_unlock(write_mutex);
                break;
            }
            k_msleep(1);
        }
        if (!sent) { setError(GENERIC_ERR, "RPC send failed or timed out"); return false; }
        while ((uint32_t)(k_uptime_get_32() - start) < DUCK_BRIDGE_RPC_TIMEOUT_MS) {
            if (k_mutex_lock(read_mutex, K_MSEC(5)) == 0) {
                RpcError response_error;
                const bool received = client->get_response(msg_id_wait, result, response_error);
                k_mutex_unlock(read_mutex);
                if (received) { setError(response_error.code, response_error.traceback); return !isError(); }
            }
            k_msleep(1);
        }
        setError(GENERIC_ERR, "RPC response timed out");
        return false;
    }

'''

BRIDGE_BEGIN = '''    bool begin(unsigned long baud=DEFAULT_SERIAL_BAUD, bool serial_initialized=false) {
        // DUCK_RETRY_SAFE_BEGIN: allocate each transport/decoder/thread only once.
        if (!primitives_initialized) {
            k_mutex_init(&read_mutex);
            k_mutex_init(&write_mutex);
            k_mutex_init(&bridge_mutex);
            primitives_initialized = true;
        }
        if (k_mutex_lock(&bridge_mutex, K_MSEC(DUCK_BRIDGE_RPC_TIMEOUT_MS)) != 0) return false;
        if (started) { k_mutex_unlock(&bridge_mutex); return true; }
        if (!transport && !serial_initialized) serial_ptr->begin(baud);
        const uint32_t start = k_uptime_get_32();
        while (!*serial_ptr) {
            if ((uint32_t)(k_uptime_get_32() - start) >= DUCK_BRIDGE_RPC_TIMEOUT_MS) {
                k_mutex_unlock(&bridge_mutex); return false;
            }
            k_msleep(1);
        }
        if (!transport) {
            transport = new SerialTransport(*serial_ptr);
        }
        if (!client) client = new RPCClient(*transport);
        if (!server) server = new RPCServer(*transport);
        if (!*client || !*server) { k_mutex_unlock(&bridge_mutex); return false; }
        if (!upd_tid) {
            if (!upd_stack_area) upd_stack_area = k_thread_stack_alloc(UPDATE_THREAD_STACK_SIZE, 0);
            if (!upd_stack_area) { k_mutex_unlock(&bridge_mutex); return false; }
            upd_tid = k_thread_create(&upd_thread_data, upd_stack_area, UPDATE_THREAD_STACK_SIZE,
                                     updateEntryPoint, NULL, NULL, NULL,
                                     UPDATE_THREAD_PRIORITY, 0, K_NO_WAIT);
            k_thread_name_set(upd_tid, "bridge");
        }
        bool response = false;
        started = call(RESET_METHOD).result(response) && response;
        if (started) tryNotify(SET_BUF_METHOD, BRIDGE_RPC_BUFFER_SIZE);
        const bool ready = started;
        k_mutex_unlock(&bridge_mutex);
        return ready;
    }

    void invalidate() {
        k_mutex_lock(&bridge_mutex, K_FOREVER);
        started = false;
        k_mutex_unlock(&bridge_mutex);
    }

'''

BRIDGE_NOTIFY = '''    template<typename... Args>
    bool tryNotify(const MsgPack::str_t& method, Args&&... args) {
        if (!client || k_mutex_lock(&write_mutex, K_MSEC(5)) != 0) return false;
        const bool sent = client->notify_checked(method, std::forward<Args>(args)...);
        k_mutex_unlock(&write_mutex);
        return sent;
    }

    template<typename... Args>
    void notify(const MsgPack::str_t method, Args&&... args) {
        (void)tryNotify(method, std::forward<Args>(args)...);
    }

'''


def patch_bridge_source(source):
    source = replace_once(source, '#define UPDATE_THREAD_PRIORITY      5',
                          '#define UPDATE_THREAD_PRIORITY      5\n#define DUCK_BRIDGE_RPC_TIMEOUT_MS 1000')
    start = source.index('    template<typename RType> bool result(RType& result) {')
    end = source.index('    bool result() {', start)
    source = source[:start] + RPC_RESULT + source[end:]
    source = replace_once(source, '    bool started = false;',
                          '    bool primitives_initialized = false;\n    bool started = false;')
    start = source.index('    bool begin(unsigned long baud=DEFAULT_SERIAL_BAUD')
    end = source.index('    bool getRouterVersion(', start)
    source = source[:start] + BRIDGE_BEGIN + source[end:]
    source = replace_once(source, '        k_mutex_lock(&bridge_mutex, K_FOREVER);\n        bool out = started;',
                          '        if (!primitives_initialized || k_mutex_lock(&bridge_mutex, K_NO_WAIT) != 0) return false;\n        bool out = started;')
    source = replace_once(source, '&& res && server->bind(name, func);',
                          '&& res && (server->isBound(name) || server->bind(name, func));')
    source = replace_once(source, '&& res && server->bind(name, func, "__safe__");',
                          '&& res && (server->isBound(name) || server->bind(name, func, "__safe__"));')
    start = source.index('    template<typename... Args>\n    bool tryNotify(') if '    bool tryNotify(' in source else source.index('    template<typename... Args>\n    void notify(')
    end = source.index('private:', start)
    source = source[:start] + BRIDGE_NOTIFY + source[end:]
    old = '''        while (true) {

            if (k_mutex_lock(&write_mutex, K_MSEC(10)) == 0){
                server->send_response(req);
                k_mutex_unlock(&write_mutex);
                break;
            } else {
                k_yield();
            }

        }'''
    new = '''        // DUCK_BOUNDED_RESPONSE: never wait indefinitely behind a sender.
        if (k_mutex_lock(&write_mutex, K_MSEC(5)) == 0) {
            server->send_response(req);
            k_mutex_unlock(&write_mutex);
        }'''
    if old in source:
        assert source.count(old) == 2
        source = source.replace(old, new)
    elif source.count(new) != 2:
        raise ValueError('Unexpected Bridge response locking code')
    start = source.index('inline void updateEntryPoint(void *, void *, void *){')
    end = source.index('static void safeUpdate()', start)
    source = source[:start] + '''inline void updateEntryPoint(void *, void *, void *){
    // The thread is created only after client/server initialization. Keep
    // consuming incoming requests during retries, or queued notifications can
    // fill the shared decoder and hide the reset RPC response behind them.
    while (true) {
        Bridge.update(); // Sleeps 1 ms when there is no request to process.
        k_yield();
    }
}

''' + source[end:]
    return source


def patch_libraries(root):
    def one(pattern):
        matches = list(root.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one pinned library: {pattern}')
        return matches[0]

    bridge = one('Arduino_RouterBridge_0.4.3_*/Arduino_RouterBridge/src/bridge.h')
    bridge.write_text(patch_bridge_source(bridge.read_text()))
    rpc = one('Arduino_RPClite_0.3.1_*/Arduino_RPClite/src/client.h').parent
    path = rpc / 'client.h'
    source = path.read_text()
    source = replace_once(source, '    RpcError lastError;', '''    RpcError lastError;
    template<typename... Args>
    bool notify_checked(const MsgPack::str_t& method, Args&&... args) {
        uint32_t ignored_id = 0;
        return decoder && decoder->send_call(NOTIFY_MSG, method, ignored_id, std::forward<Args>(args)...);
    }''')
    path.write_text(source)
    path = rpc / 'server.h'
    source = replace_once(path.read_text(), '    operator bool() const {return decoder != nullptr;}',
                          '    operator bool() const {return decoder != nullptr;}\n    bool isBound(const MsgPack::str_t& name) const {return dispatcher.isBound(name);}')
    path.write_text(source)
    path = rpc / 'decoder.h'
    source = replace_once(path.read_text(), '        if (resp_id != msg_id) return false;', '''        // DUCK_LATE_RESPONSE: a timed-out reply must not block the next RPC.
        if (resp_id != msg_id) {
            if ((int32_t)(resp_id - msg_id) < 0) {
                const bool was_first = _response_offset == 0;
                consume(_response_size, _response_offset);
                if (was_first) reset_packet();
                ++_discarded_packets;
            }
            return false;
        }''')
    path.write_text(source)


if __name__ == '__main__':
    patch_libraries(Path.home() / '.arduino15/internal')
    print('Bounded RPC, retry-safe Bridge initialization, and stale-response recovery installed')
