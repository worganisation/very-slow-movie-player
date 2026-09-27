"""EPD class.

* | File        :	  epd7in5.py
* | Author      :   Waveshare team
* | Function    :   Electronic paper driver
* | Info        :
*----------------
* | This version:   V4.0
* | Date        :   2019-06-20
# | Info        :   python demo
-----------------------------------------------------------------------------
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to  whom the Software is
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

from logging import getLogger
from time import monotonic, sleep
from typing import TYPE_CHECKING, Final

from PIL.Image import Transpose

from .epdconfig import RaspberryPi

LOGGER = getLogger(__name__)

if TYPE_CHECKING:
    from PIL.Image import Image


class EPaperDisplay:
    """Electronic paper driver class."""

    WIDTH: Final = 800
    HEIGHT: Final = 480
    BUSY_TIMEOUT_SECONDS: Final = 30

    def __init__(self) -> None:
        self.pi: RaspberryPi = RaspberryPi()

        self.reset_pin: int = self.pi.RST_PIN
        self.dc_pin: int = self.pi.DC_PIN
        self.busy_pin: int = self.pi.BUSY_PIN
        self.cs_pin: int = self.pi.CS_PIN

    # Hardware reset
    def reset(self) -> None:
        """Reset the display."""
        self.pi.digital_write(self.reset_pin, value=True)
        self.pi.delay_ms(200)
        self.pi.digital_write(self.reset_pin, value=False)
        self.pi.delay_ms(2)
        self.pi.digital_write(self.reset_pin, value=True)
        self.pi.delay_ms(200)

    def send_command(self, command: int) -> None:
        """Send command to the display."""
        self.pi.digital_write(self.dc_pin, value=False)
        self.pi.digital_write(self.cs_pin, value=False)
        self.pi.spi_writebyte([command])
        self.pi.digital_write(self.cs_pin, value=True)

    def send_data(self, data: int) -> None:
        """Send data to the display."""
        self.pi.digital_write(self.dc_pin, value=True)
        self.pi.digital_write(self.cs_pin, value=False)
        self.pi.spi_writebyte([data])
        self.pi.digital_write(self.cs_pin, value=True)

    def send_data_block(self, data: bytes) -> None:
        """Transfer a contiguous payload with fixed GPIO and one bulk SPI call."""
        self.pi.digital_write(self.dc_pin, value=True)
        self.pi.digital_write(self.cs_pin, value=False)
        try:
            self.pi.spi_writebytes2(data)
        finally:
            self.pi.digital_write(self.cs_pin, value=True)

    def read_busy(self) -> None:
        """Read the busy signal."""
        LOGGER.debug("e-Paper busy")

        deadline = monotonic() + self.BUSY_TIMEOUT_SECONDS
        self.send_command(0x71)
        busy = self.pi.digital_read(self.busy_pin)
        while not busy:
            if monotonic() >= deadline:
                raise TimeoutError(
                    f"E-paper display stayed busy for {self.BUSY_TIMEOUT_SECONDS} seconds",
                )
            sleep(0.01)
            self.send_command(0x71)
            busy = self.pi.digital_read(self.busy_pin)

        self.pi.delay_ms(200)

    def init(self) -> int:
        """Initialize the display."""
        _ = self.pi.module_init()
        # EPD hardware init start
        self.reset()

        self.send_command(0x01)  # POWER SETTING
        self.send_data(0x07)
        self.send_data(0x07)  # VGH=20V,VGL=-20V
        self.send_data(0x3F)  # VDH=15V
        self.send_data(0x3F)  # VDL=-15V

        self.send_command(0x04)  # POWER ON
        self.pi.delay_ms(100)
        self.read_busy()

        self.send_command(0x00)  # PANEL SETTING
        self.send_data(0x1F)  # KW-3f   KWR-2F	BWROTP 0f	BWOTP 1f

        self.send_command(0x61)  # tres
        self.send_data(0x03)  # source 800
        self.send_data(0x20)
        self.send_data(0x01)  # gate 480
        self.send_data(0xE0)

        self.send_command(0x15)
        self.send_data(0x00)

        self.send_command(0x50)  # VCOM AND DATA INTERVAL SETTING
        self.send_data(0x10)
        self.send_data(0x07)

        self.send_command(0x60)  # TCON SETTING
        self.send_data(0x22)

        # EPD hardware init end
        return 0

    def getbuffer(self, image: Image) -> bytes:
        """Pack Pillow's monochrome pixels in the panel's existing orientation."""
        if image.size not in {
            (self.WIDTH, self.HEIGHT),
            (self.HEIGHT, self.WIDTH),
        }:
            raise ValueError("Image must be 800x480 or 480x800 pixels")
        with image.convert("1") as monochrome:
            if image.size == (self.WIDTH, self.HEIGHT):
                return monochrome.tobytes()
            with monochrome.transpose(Transpose.ROTATE_90) as rotated:
                return rotated.tobytes()

    def display(self, image: bytes) -> None:
        """Write complementary OLD/NEW RAM planes and refresh the display."""
        frame_bytes = self.WIDTH * self.HEIGHT // 8
        if len(image) != frame_bytes:
            raise ValueError(f"Display buffer must contain {frame_bytes} bytes")
        # getbuffer returns Pillow's bits (1=white). The panel's NEW plane
        # uses inverted bits, while the OLD plane needs the complement.
        self.send_command(0x10)
        self.send_data_block(image)

        self.send_command(0x13)
        self.send_data_block(bytes(value ^ 0xFF for value in image))

        self.send_command(0x12)
        self.pi.delay_ms(100)
        self.read_busy()

    def clear(self) -> None:
        """Clear the display."""
        frame_bytes = self.WIDTH * self.HEIGHT // 8
        self.send_command(0x10)
        self.send_data_block(bytes([0xFF]) * frame_bytes)

        self.send_command(0x13)
        self.send_data_block(bytes(frame_bytes))

        self.send_command(0x12)
        self.pi.delay_ms(100)
        self.read_busy()

    def sleep(self) -> None:
        """Enter deep sleep mode."""
        self.send_command(0x02)  # POWER_OFF
        self.read_busy()

        self.send_command(0x07)  # DEEP_SLEEP
        self.send_data(0xA5)
