# VSMP Raspberry Pi and proposed frame lighting

## Confirmed host

A read-only SSH inspection of the configured `vsmppi` alias on **3 October
2026** returned:

| Item | Observation |
| --- | --- |
| Board | Raspberry Pi 4 Model B Rev 1.4 |
| Revision code | `b03114` |
| Available physical memory reported by Linux | `MemTotal: 1888908 kB` |
| Operating system | Debian GNU/Linux 13 (trixie) |
| Kernel | `6.18.50+rpt-rpi-v8` |
| Firmware power/throttling status | `vcgencmd get_throttled`: `throttled=0x0` |
| External USB devices | None enumerated; only the root hubs and onboard VIA hub |
| Candidate PWM pins | GPIO12, GPIO13, GPIO18, GPIO19 all reported input, pull-down, low |

These are dated observations. Zero throttling flags do not establish spare
power-supply capacity. The adapter make, output rating, cable losses, and actual
current draw were not measured or identified through SSH. Do not publish the
Pi's serial number, credentials, or private environment files here.

## Purchased COB LED strip

The Amazon item open in Firefox on **3 October 2026** identified the purchased
variant as **TOPAI 5 V COB LED strip**, warm white **3000 K**, **5 mm** wide,
**400 LEDs/m**, **5 m** roll, IP20, with cut points every **10 mm**.
Public product identifier: [ASIN B0CWV7ZTS8](https://www.amazon.co.uk/dp/B0CWV7ZTS8).
The selected variant was warm white, 5 V, non-waterproof.

The seller's description lists both **8 W/m** and **20 W/5 m**. These are
inconsistent: 20 W/5 m is 4 W/m. Until the actual cut section is measured,
use the higher 8 W/m figure when estimating the load:

| Cut length | Estimated power at 8 W/m | Current at 5 V |
| --- | --- | --- |
| 10 cm | 0.8 W | 0.16 A |
| 20 cm | 1.6 W | 0.32 A |
| 30 cm | 2.4 W | 0.48 A |

These are calculations from seller ratings, not measured values or guaranteed
maximum current. A short section can also draw more current per metre than a
full roll because it has less voltage drop.

## Power and dimming proposal

A plain two-wire 5 V strip can share the Pi's 5 V header rail, provided the
power supply, wiring, and connectors have enough capacity for the Pi, display,
and LEDs at full brightness. The 5 V rail is available on physical pins 2 and
4. GPIO signal pins operate at 3.3 V and must not carry the LED load.
See [Raspberry Pi's GPIO and power documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html).

Use a low-side N-channel MOSFET dimmer whose input is explicitly compatible
with 3.3 V logic and whose power stage accepts a 5 V load. For a discrete
MOSFET, choose one with on-resistance specified at a gate voltage no higher
than 3.3 V; the gate-threshold voltage alone is not a suitability rating.
GPIO PWM controls brightness; the MOSFET carries the strip current. See
[Adafruit's analog LED-strip PWM guide](https://learn.adafruit.com/rgb-led-strips/usage)
for the switching principle (its illustrated strips use a different supply
voltage).

Proposed connections for the two-wire strip:

| Connection | Destination |
| --- | --- |
| Pi 5 V, physical pin 2 or 4 | LED strip positive |
| LED strip negative | MOSFET drain / dimmer load negative |
| MOSFET source / dimmer power ground | Pi ground, e.g. physical pin 6 |
| GPIO12, physical pin 32 | Dimmer PWM input, or discrete gate through a series resistor |
| Discrete MOSFET gate | Pull-down resistor to source, keeping lighting off before PWM starts |

GPIO12 is a candidate hardware-PWM output outside VSMP's current display
assignments: GPIO8, GPIO10, GPIO11, GPIO17, GPIO24, and GPIO25. Confirm overlay,
audio, and other service usage before configuring PWM. Prefer it over GPIO18,
which newer Waveshare HATs assign to PWR. A header breakout or splitter may be
needed for physical access while the HAT is installed.

The dimmer is **proposed, not installed or tested**. Verify the strip's actual
current, supply label, rail voltage, and undervoltage flags with LEDs at full
brightness during Pi load and display refresh before treating this as a
validated shared-power arrangement. If there is insufficient headroom, power
the LEDs from a separate 5 V supply and join grounds; do not join the two
supplies' positive rails.
