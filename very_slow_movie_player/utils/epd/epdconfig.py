"""Config for the EPD.

Adapted from https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd

* | File        :	  epdconfig.py
* | Author      :   Waveshare team
* | Function    :   Hardware underlying interface
* | Info        :
*----------------
* | This version:   V1.0
* | Date        :   2019-06-21
* | Info        :
******************************************************************************
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS OR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

from logging import debug
from time import sleep
from typing import Literal
from unittest.mock import MagicMock

from utils import const


class RaspberryPi:
    """Raspberry Pi configuration."""

    # Pin definition
    RST_PIN = 17
    DC_PIN = 25
    CS_PIN = 8
    BUSY_PIN = 24

    def __init__(self) -> None:
        try:
            from RPi import GPIO  # ruff: ignore[import-outside-top-level]
            from spidev import (
                SpiDev,  # type: ignore[import-not-found] # ruff: ignore[import-outside-top-level]
            )

            self.gpio = GPIO

            # SPI device, bus = 0, device = 0
            self.spi = SpiDev(0, 0)
        except ImportError:
            if const.HOSTNAME == "mtrxpi":
                raise

            self.gpio = MagicMock()
            self.spi = MagicMock()

    def digital_write(self, pin: int, *, value: bool) -> None:
        """Write the value to the pin."""
        self.gpio.output(pin, value)

    def digital_read(self, pin: int) -> bool:
        """Read the value of the pin."""
        return self.gpio.input(pin)

    @staticmethod
    def delay_ms(delay_time: float) -> None:
        """Delay in milliseconds."""
        sleep(delay_time / 1000)

    def spi_writebyte(self, data: list[int]) -> None:
        """Write byte to SPI (Serial Peripheral Interface)."""
        self.spi.writebytes(data)

    def module_init(self) -> Literal[0]:
        """Module initialization."""
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)  # ruff: ignore[boolean-positional-value-in-call]
        self.gpio.setup(self.RST_PIN, self.gpio.OUT)
        self.gpio.setup(self.DC_PIN, self.gpio.OUT)
        self.gpio.setup(self.CS_PIN, self.gpio.OUT)
        self.gpio.setup(self.BUSY_PIN, self.gpio.IN)
        self.spi.max_speed_hz = 4000000
        self.spi.mode = 0b00
        return 0

    def module_exit(self) -> None:
        """Module exit."""
        debug("spi end")
        self.spi.close()

        debug("close 5V, Module enters 0 power consumption ...")
        self.gpio.output(self.RST_PIN, 0)
        self.gpio.output(self.DC_PIN, 0)

        self.gpio.cleanup()
