/*
    This file is part of the Arduino_RouterBridge library.

    Copyright (C) Arduino s.r.l. and/or its affiliated companies

    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.

*/

#include "Arduino_RouterBridge.h"

#ifndef ARDUINO_ROUTER_SERIAL
#define ARDUINO_ROUTER_SERIAL Serial1
#endif

BridgeClass Bridge(ARDUINO_ROUTER_SERIAL);
BridgeMonitor<> Monitor(Bridge);

#ifdef ARDUINO_ROUTERBRIDGE_PROVIDES_SERIAL
// Alias the 'Serial' object to the above 'Monitor' instance
extern BridgeMonitor<> Serial [[gnu::alias("Monitor")]];
#endif
