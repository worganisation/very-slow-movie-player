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
from typing import TYPE_CHECKING, Final, Protocol, cast

from .epdconfig import RaspberryPi

LOGGER = getLogger(__name__)

if TYPE_CHECKING:
    from PIL.Image import Image


class PixelReader(Protocol):
    """Read one monochrome pixel from a Pillow image."""

    def __getitem__(self, _position: tuple[int, int]) -> int:
        """Return a pixel value."""
        ...


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

    def getbuffer(self, image: Image) -> list[int]:
        """Get the image buffer."""
        buf = [0xFF] * (int(self.WIDTH / 8) * self.HEIGHT)
        image_monocolor = image.convert("1")
        imwidth, imheight = image_monocolor.size
        pixels = cast("PixelReader", image_monocolor.load())  # pyright: ignore[reportUnknownMemberType]
        if imwidth == self.WIDTH and imheight == self.HEIGHT:
            LOGGER.debug("Vertical")
            for y in range(imheight):
                for x in range(imwidth):
                    # Set the bits for the column of pixels at the current position.
                    if pixels[x, y] == 0:
                        buf[int((x + y * self.WIDTH) / 8)] &= ~(0x80 >> (x % 8))
        elif imwidth == self.HEIGHT and imheight == self.WIDTH:
            LOGGER.debug("Horizontal")
            for y in range(imheight):
                for x in range(imwidth):
                    new_x = y
                    new_y = self.HEIGHT - x - 1
                    if pixels[x, y] == 0:
                        buf[int((new_x + new_y * self.WIDTH) / 8)] &= ~(0x80 >> (y % 8))
        return buf

    def display(self, image: list[int]) -> None:
        """Display the image."""
        self.send_command(0x13)
        for i in range(int(self.WIDTH * self.HEIGHT / 8)):
            self.send_data(~image[i])

        self.send_command(0x12)
        self.pi.delay_ms(100)
        self.read_busy()

    def clear(self) -> None:
        """Clear the display."""
        self.send_command(0x10)
        for _ in range(int(self.WIDTH * self.HEIGHT / 8)):
            self.send_data(0x00)

        self.send_command(0x13)
        for _ in range(int(self.WIDTH * self.HEIGHT / 8)):
            self.send_data(0x00)

        self.send_command(0x12)
        self.pi.delay_ms(100)
        self.read_busy()

    def sleep(self) -> None:
        """Enter deep sleep mode."""
        self.send_command(0x02)  # POWER_OFF
        self.read_busy()

        self.send_command(0x07)  # DEEP_SLEEP
        self.send_data(0xA5)
