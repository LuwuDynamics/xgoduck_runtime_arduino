/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#ifndef SERIALLINK_LINK_H
#define SERIALLINK_LINK_H

#include <stddef.h>
#include <stdint.h>

#include "frame.h"

// Maximum number of stream bytes carried by a frame. The effective value is
// the minimum between the two peers.
#ifndef SERIALLINK_MAX_PAYLOAD
#define SERIALLINK_MAX_PAYLOAD 256
#endif

// Maximum number of unacknowledged frames in flight, and size of the receive
// buffer in frames. The serial port RX buffer should be able to hold a full
// window, so that frames are not lost while the link is busy.
#ifndef SERIALLINK_WINDOW
#define SERIALLINK_WINDOW 4
#endif

static_assert(SERIALLINK_MAX_PAYLOAD >= 1 && SERIALLINK_MAX_PAYLOAD <= seriallink::MAX_PAYLOAD_LIMIT, "invalid SERIALLINK_MAX_PAYLOAD");
static_assert(SERIALLINK_WINDOW >= 1 && SERIALLINK_WINDOW <= seriallink::MAX_WINDOW_LIMIT, "invalid SERIALLINK_WINDOW");

// A reliable, ordered, flow-controlled byte stream over an unreliable serial
// line. This is the MCU side implementation of the protocol implemented by the
// internal/seriallink package of arduino-router: see its documentation for the
// specification of the wire format and of the protocol.
//
// The Link is a pure state machine: it does no I/O by itself, it does not use
// the heap and it does not depend on any OS. The caller must:
//  - feed the bytes received from the serial port with onRx()
//  - call poll() at least when the time it returned has elapsed, and after
//    every other call (onRx, read, write, ...) to send the resulting frames
//  - provide a monotonic time in milliseconds to every call (it may wrap)
// Frames are sent synchronously, from poll(), through the output function.
//
// Baud rate: if enabled with setBaudRates(), the link negotiates a faster
// rate with the peer once the session is established, and changes it
// synchronously from poll() and begin() through the baud rate function.
// Every session starts at the base rate.
//
// The Link is not thread-safe: if it is used by several threads, the caller
// must serialize the calls (for example with a mutex).
namespace seriallink {

enum class State : uint8_t {
    Idle,        // begin() not called yet
    Connecting,  // waiting for the handshake to complete
    Established, // the session is up, data can be exchanged
    Closed,      // the session terminated, see error(). Call begin() to start a new one
};

enum class Error : uint8_t {
    None,
    Closed,       // close() has been called
    SessionReset, // the peer restarted or aborted the session
    LinkTimeout,  // the peer stopped responding
    BaudRate,     // the negotiated baud rate does not work
};

struct Timing {
    // Time without progress after which the unacknowledged frames are sent
    // again. It must be larger than the time needed to transmit a full window.
    uint32_t retransmitTimeoutMs = 250;
    // Number of consecutive retransmission timeouts after which the link is
    // declared dead.
    uint8_t maxRetries = 10;
    // How long an acknowledgement is delayed waiting to be piggybacked.
    uint32_t ackDelayMs = 10;
    // Interval of the keep-alive frames sent while idle.
    uint32_t keepAliveMs = 1000;
    // Time without receiving anything after which the link is declared dead.
    uint32_t linkTimeoutMs = 4000;
};

struct Stats {
    uint32_t txFrames = 0;    // frames sent
    uint32_t rxFrames = 0;    // valid frames received
    uint32_t rxErrors = 0;    // frames dropped because corrupted
    uint32_t retransmits = 0; // DATA frames sent again
    uint32_t baudSwitches = 0;  // successful switches to the negotiated baud rate
    uint32_t baudFailures = 0;  // failed attempts to switch to the negotiated baud rate
    uint32_t baudFallbacks = 0; // fallbacks to the base rate after a silence at the negotiated rate
};

class Link {
public:
    // Output function: must write the encoded frame to the serial port.
    typedef void (*OutputFn)(void* ctx, const uint8_t* data, size_t len);
    // Baud rate function: must wait until all the data written so far has
    // been transmitted, then change the serial port baud rate.
    typedef void (*SetBaudRateFn)(void* ctx, uint32_t rate);

    Link(OutputFn output, void* ctx, const Timing& timing = Timing());

    // Enables the baud rate negotiation. base is the rate of the serial port
    // at the beginning of every session, rates is the mask of the faster
    // rates that the serial port supports (see baudRateMask()). It must be
    // called before begin(), and the serial port must be at the base rate
    // the first time it is called. Changes take effect at the next begin().
    void setBaudRates(SetBaudRateFn fn, uint32_t base, uint16_t rates);

    Link(const Link&) = delete;
    Link& operator=(const Link&) = delete;

    // Starts a new session (dropping the current one, if any). The nonce
    // identifies the session and should be random: a peer reboot is detected
    // by a change of nonce.
    void begin(uint32_t nonce, uint32_t now);

    // Terminates the session, notifying the peer if established.
    void close();

    // Processes bytes received from the serial port.
    void onRx(const uint8_t* data, size_t len, uint32_t now);

    // Runs the timers and sends the pending frames. Returns the number of
    // milliseconds after which poll() must be called again (if nothing else
    // happens in the meantime).
    uint32_t poll(uint32_t now);

    // Queues up to len bytes for transmission and returns how many have been
    // accepted (0 if not established or if the peer window is full).
    size_t write(const uint8_t* data, size_t len);

    // Number of bytes that write() would accept now.
    size_t writable() const;

    // Reads up to len received bytes, returns the number of bytes read.
    size_t read(uint8_t* buf, size_t len);

    // Number of received bytes that can be read.
    size_t available() const { return rxCount; }

    State state() const { return st; }
    Error error() const { return err; }
    const Stats& stats() const { return stat; }

    // Negotiated parameters (valid when established).
    uint16_t maxPayload() const { return negMaxPayload; }
    uint8_t sendWindow() const { return negWindow; }

    // Current baud rate of the serial port.
    uint32_t baudRate() const { return curBaud; }

private:
    static constexpr size_t RX_CAP = SERIALLINK_WINDOW * SERIALLINK_MAX_PAYLOAD;
    // Largest payload received or sent: control frames may be larger than
    // the max payload.
    static constexpr size_t FRAME_PAYLOAD_CAP =
        SERIALLINK_MAX_PAYLOAD > CONTROL_PAYLOAD_LIMIT ? SERIALLINK_MAX_PAYLOAD : CONTROL_PAYLOAD_LIMIT;
    static constexpr size_t MAX_ENCODED = cobs::maxEncodedLen(rawFrameSize(FRAME_PAYLOAD_CAP));
    static constexpr uint8_t MAX_BAUD_FAILURES = 5;

    enum class BaudPhase : uint8_t {
        Base, // at the base rate
        Fast, // at the negotiated rate (or switching to it)
    };

    struct Slot {
        uint16_t len;
        uint8_t data[SERIALLINK_MAX_PAYLOAD];
    };

    void terminate(Error e, bool sendRst);
    void establish(uint32_t now);
    void receive(uint32_t now);
    void handleFrame(const Frame& f, uint32_t now);
    void handleEstablished(const Frame& f, uint32_t now);
    void retransmit();
    void sendNak(uint32_t now);
    void flush(uint32_t now);
    void emit(FrameType type, uint8_t seq, const uint8_t* payload, size_t len, uint32_t now);
    SynParams localParams() const;
    void setBaud(uint32_t rate);
    void onValidFrameBaud(uint32_t now);
    void baudFallback(uint32_t now);
    uint32_t baudSilenceDeadline() const;
    uint32_t keepAliveInterval(uint32_t now) const;
    uint8_t recvCredit() const;
    uint8_t sendLimit() const;

    OutputFn output;
    void* outputCtx;
    Timing timing;

    State st = State::Idle;
    Error err = Error::None;
    Stats stat;

    uint32_t nonce = 0;
    SynParams peer{};
    bool peerKnown = false;  // we received (and answered) a SYN from the current peer
    bool peerActive = false; // the peer sent DATA/ACK/NAK in this session
    uint16_t negMaxPayload = SERIALLINK_MAX_PAYLOAD;
    uint8_t negWindow = SERIALLINK_WINDOW;
    uint32_t synDeadline = 0;

    // pending control frames, sent by flush()
    bool synPending = false;
    bool synAckPending = false;
    bool rstPending = false;
    bool nakPending = false;
    bool ackNeeded = false;

    // transmitter: unacknowledged DATA frames, slots[txBase] is the oldest
    Slot slots[SERIALLINK_WINDOW];
    uint8_t txBase = 0;
    uint8_t txCount = 0;
    uint8_t txSent = 0; // frames of the window already sent (from the oldest)
    uint8_t nextSeq = 0;
    uint8_t peerWnd = 0;
    uint8_t retries = 0;
    uint32_t rtoStart = 0;
    uint32_t lastTx = 0;
    uint32_t lastRst = 0;
    bool rstSent = false;

    // baud rate negotiation
    SetBaudRateFn setBaudFn = nullptr;
    uint32_t baseBaud = 0;
    uint16_t localRates = 0;
    uint32_t curBaud = 0;
    uint32_t baudTarget = 0; // negotiated rate, 0 if none
    BaudPhase baudPhase = BaudPhase::Base;
    bool baudSwitchPending = false; // send the answer and switch, from flush()
    bool baudProven = false;        // Fast: the peer has been heard at the negotiated rate
    uint32_t baudSwitchedAt = 0;    // Fast: when the port switched to the negotiated rate
    uint8_t baudFailures = 0;       // consecutive failed switches

    // receiver
    uint8_t rxBuf[RX_CAP];
    size_t rxHead = 0;
    size_t rxCount = 0;
    uint8_t expected = 0;
    uint8_t ackPending = 0;
    uint32_t ackDue = 0;
    bool nakSent = false; // a NAK has been sent and no DATA frame accepted since
    uint32_t nakAt = 0;   // when the last NAK has been sent
    uint8_t lastAdvWnd = 0;
    uint32_t lastRx = 0;

    // frame buffers
    uint8_t acc[MAX_ENCODED];
    size_t accLen = 0;
    bool discarding = false;
    uint8_t rxRaw[rawFrameSize(FRAME_PAYLOAD_CAP)];
    uint8_t txRaw[rawFrameSize(FRAME_PAYLOAD_CAP)];
    uint8_t txEnc[1 + maxEncodedFrameSize(FRAME_PAYLOAD_CAP)]; // with room for a leading delimiter
};

} // namespace seriallink

#endif // SERIALLINK_LINK_H
