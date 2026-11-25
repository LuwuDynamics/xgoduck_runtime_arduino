/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#pragma once

#ifndef ROUTERBRIDGE_SERIALLINK_TRANSPORT_H
#define ROUTERBRIDGE_SERIALLINK_TRANSPORT_H

#include <Arduino.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <Arduino_RPClite.h>

#include "seriallink/link.h"

extern void duckEnableRouterFifo();

// Measured peak usage is about 450 bytes.
#ifndef SERIALLINK_THREAD_STACK_SIZE
#define SERIALLINK_THREAD_STACK_SIZE    1024
#endif
// Higher than the bridge update thread: the link must keep running (acks,
// retransmissions, keep-alives) while the other threads are busy.
#define SERIALLINK_THREAD_PRIORITY      4

// An RPClite transport running the seriallink protocol (reliable, ordered and
// flow-controlled byte stream) over a serial port. A dedicated thread drives
// the protocol: it drains the serial port, runs the timers and sends the
// frames.
//
// Sessions: when the session is reset (the router restarted or the link timed
// out) a new session is started automatically. The data of the new session is
// held back until the consumer calls ackReset(), after it has discarded any
// state belonging to the old session (e.g. the RPC decoder buffer), so that
// the two sessions never mix. For the same reason, write() never splits a
// message across two sessions: if the session is reset while writing, the
// rest of the message is dropped, and lastWriteSession() tells in which
// session the last message was sent.
//
// Baud rate: every session starts at the base rate, then the link may switch
// to a faster rate negotiated with the router (see begin()).
class SerialLinkTransport final : public ITransport {

    HardwareSerial* serial;
    seriallink::Link link;

    struct k_mutex mutex{};
    struct k_condvar cond{};

    k_tid_t tid{};
    k_thread_stack_t* stack{};
    struct k_thread thread_data{};

    atomic_t resets = ATOMIC_INIT(0);   // number of session resets
    uint32_t acked_resets = 0;          // resets acknowledged by the consumer
    uint32_t write_session = 0;         // session of the last message written
    uint32_t entropy = 0;               // timing jitter, used for the session nonces

public:

    explicit SerialLinkTransport(HardwareSerial& serial)
        : serial(&serial), link(output, this) {
        k_mutex_init(&mutex);
        k_condvar_init(&cond);
    }

    // Starts the protocol thread. The serial port must already be open at
    // baseBaud. If maxBaud is higher than baseBaud, the link negotiates with
    // the router the highest rate supported by both, up to maxBaud.
    void begin(uint32_t baseBaud, uint32_t maxBaud) {
        k_mutex_lock(&mutex, K_FOREVER);
        duckEnableRouterFifo();
        if (maxBaud > baseBaud) {
            link.setBaudRates(set_baud, baseBaud, seriallink::baudRateMask(baseBaud, maxBaud));
        }
        link.begin(new_nonce(), k_uptime_get_32());
        k_mutex_unlock(&mutex);

        stack = k_thread_stack_alloc(SERIALLINK_THREAD_STACK_SIZE, 0);
        tid = k_thread_create(&thread_data, stack, SERIALLINK_THREAD_STACK_SIZE,
                              thread_entry, this, NULL, NULL,
                              SERIALLINK_THREAD_PRIORITY, 0, K_NO_WAIT);
        k_thread_name_set(tid, "seriallink");
    }

    void flush() override
    {
        serial->flush();
    }

    // Current session (number of session resets so far).
    uint32_t session() const {
        return atomic_get(&resets);
    }

    // Session in which the last message has been written.
    uint32_t lastWriteSession() {
        k_mutex_lock(&mutex, K_FOREVER);
        const uint32_t out = write_session;
        k_mutex_unlock(&mutex);
        return out;
    }

    // Called by the consumer after discarding the state of the sessions up to
    // the given one: the data of that session is delivered from now on.
    void ackReset(uint32_t session) {
        k_mutex_lock(&mutex, K_FOREVER);
        acked_resets = session;
        k_condvar_broadcast(&cond);
        k_mutex_unlock(&mutex);
    }

    // True if the session is established (and acknowledged by the consumer).
    bool connected() {
        k_mutex_lock(&mutex, K_FOREVER);
        const bool out = ready();
        k_mutex_unlock(&mutex);
        return out;
    }

    // Current baud rate of the serial port.
    uint32_t baudRate() {
        k_mutex_lock(&mutex, K_FOREVER);
        const uint32_t out = link.baudRate();
        k_mutex_unlock(&mutex);
        return out;
    }

    seriallink::Stats stats() {
        k_mutex_lock(&mutex, K_FOREVER);
        const seriallink::Stats out = link.stats();
        k_mutex_unlock(&mutex);
        return out;
    }

    // Writes the whole message, blocking until the session is established and
    // the peer has room for it. Always returns size: if the session is reset
    // while writing, the rest of the message is dropped (see lastWriteSession).
    size_t write(const uint8_t* data, size_t size) override {
        k_mutex_lock(&mutex, K_FOREVER);
        while (!ready()) {
            k_condvar_wait(&cond, &mutex, K_FOREVER);
        }
        const uint32_t session = atomic_get(&resets);
        write_session = session;
        size_t done = 0;
        while (done < size && atomic_get(&resets) == session) {
            if (link.state() != seriallink::State::Established) break;
            const size_t n = link.write(data + done, size - done);
            if (n > 0) {
                done += n;
                link.poll(k_uptime_get_32()); // send now, do not wait for the thread
            } else {
                k_condvar_wait(&cond, &mutex, K_MSEC(10));
            }
        }
        k_mutex_unlock(&mutex);
        return size;
    }

    // Queue one whole Duck notification or drop it before touching the byte stream.
    bool tryWritePacket(const uint8_t* data, size_t len, uint32_t expectedSession) {
        if (k_mutex_lock(&mutex, K_MSEC(1)) != 0) return false;
        const bool sent = ready() && session() == expectedSession && link.writable() >= len &&
                          link.write(data, len) == len;
        k_mutex_unlock(&mutex);
        return sent;
    }

    size_t read(uint8_t* buffer, size_t size) override {
        k_mutex_lock(&mutex, K_FOREVER);
        const size_t n = acked_resets == session() ? link.read(buffer, size) : 0;
        k_mutex_unlock(&mutex);
        return n;
    }

    size_t read_byte(uint8_t& r) override {
        return read(&r, 1);
    }

    bool available() override {
        k_mutex_lock(&mutex, K_FOREVER);
        const bool out = acked_resets == session() && link.available() > 0;
        k_mutex_unlock(&mutex);
        return out;
    }

private:

    // Must be called with the mutex held.
    bool ready() const {
        return link.state() == seriallink::State::Established && acked_resets == atomic_get(&resets);
    }

    static void output(void* ctx, const uint8_t* data, size_t len) {
        static_cast<SerialLinkTransport*>(ctx)->serial->write(data, len);
    }

    // Called by the link with the mutex held. begin() also clears the RX
    // buffer: what is in there has been received at the old rate.
    static void set_baud(void* ctx, uint32_t rate) {
        HardwareSerial* serial = static_cast<SerialLinkTransport*>(ctx)->serial;
        serial->flush(); // wait until everything has been transmitted
        serial->begin(rate);
        duckEnableRouterFifo();
    }

    static void thread_entry(void* p1, void*, void*) {
        static_cast<SerialLinkTransport*>(p1)->run();
    }

    // Mixes the available entropy into a nonce. There is no hardware RNG
    // available to sketches: the timing of the received bytes adds some
    // randomness to the boot time. A repeated nonce is tolerated by the
    // protocol anyway, except in the very first moments of a session.
    uint32_t new_nonce() {
        uint32_t h = entropy ^ sys_clock_cycle_get_32() ^ (uint32_t(atomic_get(&resets)) * 0x9E3779B9u);
        h ^= h >> 16;
        h *= 0x85EBCA6Bu;
        h ^= h >> 13;
        h *= 0xC2B2AE35u;
        h ^= h >> 16;
        return h;
    }

    void run() {
        uint8_t buf[64];
        while (true) {
            k_mutex_lock(&mutex, K_FOREVER);
            const uint32_t now = k_uptime_get_32();

            // The serial port has no RX notification: poll it
            size_t n = 0;
            while (serial->available() > 0) {
                buf[n++] = uint8_t(serial->read());
                if (n == sizeof(buf)) {
                    link.onRx(buf, n, now);
                    n = 0;
                }
                entropy = entropy * 31 + sys_clock_cycle_get_32();
            }
            if (n > 0) link.onRx(buf, n, now);

            link.poll(now);
            if (link.state() == seriallink::State::Closed) {
                // The session has been reset: start a new one
                atomic_inc(&resets);
                link.begin(new_nonce(), now);
                link.poll(now);
            }
            k_condvar_broadcast(&cond);
            k_mutex_unlock(&mutex);
            k_msleep(1);
        }
    }

};

#endif // ROUTERBRIDGE_SERIALLINK_TRANSPORT_H
