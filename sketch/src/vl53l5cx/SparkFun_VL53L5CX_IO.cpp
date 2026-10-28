/*
  This is a library written for the ST VL53L5CX Time-of-flight sensor
  SparkFun sells these at its website:
  https://www.sparkfun.com/products/18642

  Do you like this library? Help support open source hardware. Buy a board!

  Written by Ricardo Ramos  @ SparkFun Electronics, October 22nd, 2021
  This file implements the VL53L5CX I2C driver class.

  This library uses ST's VL53L5CX driver and parts of Simon Levy's VL53L5CX
  Arduino library available at https://github.com/simondlevy/VL53L5/tree/main/src/st

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
  GNU General Public License for more details.
  You should have received a copy of the GNU General Public License
  along with this program. If not, see <http://www.gnu.org/licenses/>.
*/

#include "SparkFun_VL53L5CX_IO.h"
#include "SparkFun_VL53L5CX_Library_Constants.h"

bool SparkFun_VL53L5CX_IO::begin(byte address, TwoWire &wirePort)
{
    _address = address;
    _i2cPort = &wirePort;
    return isConnected();
}

bool SparkFun_VL53L5CX_IO::isConnected()
{
    _i2cPort->beginTransmission(_address);
    if (_i2cPort->endTransmission() != 0)
        return (false);
    return (true);
}

void SparkFun_VL53L5CX_IO::setAddress(uint8_t newAddress)
{
    _address = newAddress;
}

// Must be able to write 32,768 bytes at a time
uint8_t SparkFun_VL53L5CX_IO::writeMultipleBytes(uint16_t registerAddress, uint8_t *buffer, uint16_t bufferSize)
{
    // Chunk I2C transactions into limit of 32 bytes (or wireMaxPacketSize)
    uint8_t i2cError = 0;
    uint32_t startSpot = 0;
    uint32_t bytesToSend = bufferSize;
    while (bytesToSend > 0)
    {
        uint32_t len = bytesToSend;
        if (len > (wireMaxPacketSize - 2)) // Allow 2 byte for register address
            len = (wireMaxPacketSize - 2);

        _i2cPort->beginTransmission((uint8_t)_address);
        _i2cPort->write(highByte(registerAddress));
        _i2cPort->write(lowByte(registerAddress));

        // TODO write a subsection of the buffer rather than byte wise
        for (uint16_t x = 0; x < len; x++)
            _i2cPort->write(buffer[startSpot + x]); // Write a portion of the payload to the bus

        i2cError = _i2cPort->endTransmission(); // Release bus because we are writing the address each time
        if (i2cError != 0)
            return (i2cError); // Sensor did not ACK

        startSpot += len; // Move the pointer forward
        bytesToSend -= len;
        registerAddress += len; // Move register address forward
    }
    return (i2cError);
}

uint8_t SparkFun_VL53L5CX_IO::readMultipleBytes(uint16_t registerAddress, uint8_t *buffer, uint16_t bufferSize)
{
    uint16_t offset = 0;
    while (offset < bufferSize)
    {
        const uint16_t remaining = bufferSize - offset;
        const uint8_t count = remaining > wireMaxPacketSize ? wireMaxPacketSize : remaining;
        const uint16_t reg = registerAddress + offset;
        // Re-address every chunk; check the exact length before consuming it.
        _i2cPort->beginTransmission(_address);
        _i2cPort->write(highByte(reg));
        _i2cPort->write(lowByte(reg));
        const uint8_t error = _i2cPort->endTransmission(false);
        if (error) return error;
        const size_t received = _i2cPort->requestFrom((uint8_t)_address, (size_t)count);
        if (received != count || _i2cPort->available() != count) return 1;
        for (uint8_t i = 0; i < count; ++i) buffer[offset + i] = _i2cPort->read();
        offset += count;
    }
    return 0;
}

uint8_t SparkFun_VL53L5CX_IO::readSingleByte(uint16_t registerAddress)
{
    _i2cPort->beginTransmission(_address);
    _i2cPort->write(highByte(registerAddress));
    _i2cPort->write(lowByte(registerAddress));
    _i2cPort->endTransmission();
    _i2cPort->requestFrom(_address, 1U);
    return _i2cPort->read();
}

uint8_t SparkFun_VL53L5CX_IO::writeSingleByte(uint16_t registerAddress, uint8_t const value)
{
    _i2cPort->beginTransmission(_address);
    _i2cPort->write(highByte(registerAddress));
    _i2cPort->write(lowByte(registerAddress));
    _i2cPort->write(value);
    return _i2cPort->endTransmission();
}
