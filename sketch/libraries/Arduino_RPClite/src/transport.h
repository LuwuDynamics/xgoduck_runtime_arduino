/*
    This file is part of the Arduino_RPClite library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#ifndef RPCLITE_TRANSPORT_H
#define RPCLITE_TRANSPORT_H

class ITransport {
// Transport abstraction interface

public:
virtual ~ITransport() = default;

virtual size_t write(const uint8_t* data, size_t size) = 0;
    virtual size_t read(uint8_t* buffer, size_t size) = 0;
    virtual size_t read_byte(uint8_t& r) = 0;
    virtual bool available() = 0;
    virtual void flush() = 0;
};

#endif //RPCLITE_TRANSPORT_H
