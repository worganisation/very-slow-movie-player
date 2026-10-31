# VSMP Raspberry Pi and proposed frame lighting

## Confirmed host

VSMPPi uses a Raspberry Pi 4 Model B Rev 1.4, revision code `b03114`, with 2 GB RAM.

Before adding lighting, inspect the power adapter rating, wiring, connectors and actual load. Check undervoltage and throttling flags during Pi load and display refresh; zero flags alone do not establish spare supply capacity. Check current GPIO, overlay and service usage before assigning a PWM pin. Keep the Pi's serial number, credentials and private environment files out of this reference.

## Purchased COB LED strip

The purchased strip is a **TOPAI 5 V COB LED strip**, warm white **3000 K**, **5 mm** wide, **400 LEDs/m**, **5 m** roll, IP20, with cut points every **10 mm**. Public product identifier: [ASIN B0CWV7ZTS8](https://www.amazon.co.uk/dp/B0CWV7ZTS8). The selected variant is warm white, 5 V, non-waterproof.

The seller's description lists both **8 W/m** and **20 W/5 m**. These are inconsistent: 20 W/5 m is 4 W/m. Until the actual cut section is measured, use the higher 8 W/m figure when estimating the load:

| Cut length | Estimated power at 8 W/m | Current at 5 V |
| --- | --- | --- |
| 10 cm | 0.8 W | 0.16 A |
| 20 cm | 1.6 W | 0.32 A |
| 30 cm | 2.4 W | 0.48 A |

These are calculations from seller ratings, not measured values or guaranteed maximum current. A short section can also draw more current per metre than a full roll because it has less voltage drop.

## Purchased MOSFET driver references

The purchased dimmer board is the **Adafruit MOSFET Driver for Motors, Solenoids and LEDs, STEMMA JST PH 2 mm**, [product 5648](https://www.adafruit.com/product/5648). The connector is three-pin JST PH, not four-pin STEMMA QT. Purchase does not establish installation or physical validation of the lighting arrangement below.

The [official guide](https://learn.adafruit.com/adafruit-mosfet-driver) covers pinouts, terminal blocks, Raspberry Pi PWM control and electrical limits. Its [downloads page](https://learn.adafruit.com/adafruit-mosfet-driver/downloads) supplies the schematic, fabrication print and links to CAD resources. Local manufacturer references are preserved unchanged; PDF text extracts normalize trailing whitespace for searching. Copyright and attribution remain with Adafruit Industries and Alpha & Omega Semiconductor, respectively.

| Local reference | Manufacturer source and scope |
| --- | --- |
| [User guide PDF](vendor/adafruit-mosfet-driver-guide.pdf) / [text](vendor/adafruit-mosfet-driver-guide.txt) | [Adafruit guide download](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-mosfet-driver.pdf); pinouts, wiring, PWM examples and drawings. |
| [Schematic PNG](vendor/adafruit-mosfet-driver-schematic.png) | [Adafruit schematic](https://cdn-learn.adafruit.com/assets/assets/000/117/028/large1024/adafruit_products_schem.png); board circuit and component values. |
| [Dimensioned fabrication print PNG](vendor/adafruit-mosfet-driver-fab-print.png) | [Adafruit fabrication print](https://cdn-learn.adafruit.com/assets/assets/000/117/029/large1024/adafruit_products_fab.png); dimensions are in inches, including the 1.00 × 0.70 inch board outline and 0.50 inch mounting-hole centre spacing. |
| [EagleCAD board](vendor/adafruit-mosfet-driver.brd) / [schematic](vendor/adafruit-mosfet-driver.sch) / [license](vendor/adafruit-mosfet-driver-license.txt) | [Adafruit PCB source at commit `ea11049753a79330cc2651df233909c0191a5af4`](https://github.com/adafruit/Adafruit-MOSFET-Driver-STEMMA-PCB/tree/ea11049753a79330cc2651df233909c0191a5af4); original board and schematic files, under the supplied CC BY-SA 3.0 license. |
| [AO3406 datasheet PDF](vendor/ao3406-datasheet.pdf) / [text](vendor/ao3406-datasheet.txt) | [Manufacturer datasheet hosted by Adafruit](https://cdn-shop.adafruit.com/product-files/5648/5648_ds_AO3406.pdf); transistor electrical characteristics and package dimensions. Component ratings are not whole-board current ratings. |

For enclosure modelling, Adafruit also provides [3D CAD models for product 5648](https://github.com/adafruit/Adafruit_CAD_Parts/tree/main/5648%20MOSFET%20Driver). Use the drawings and CAD for geometry and check the actual board, connector clearance and cable bend space before finalising a mount. [SHA256SUMS](vendor/SHA256SUMS) identifies the local reference files alongside the display documentation.

## Power and dimming proposal

A plain two-wire 5 V strip can share the Pi's 5 V header rail, provided the power supply, wiring, and connectors have enough capacity for the Pi, display, and LEDs at full brightness. The 5 V rail is available on physical pins 2 and 4. GPIO signal pins operate at 3.3 V and must not carry the LED load. See [Raspberry Pi's GPIO and power documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html).

Use a low-side N-channel MOSFET dimmer whose input is explicitly compatible with 3.3 V logic and whose power stage accepts a 5 V load. For a discrete MOSFET, choose one with on-resistance specified at a gate voltage no higher than 3.3 V; the gate-threshold voltage alone is not a suitability rating. GPIO PWM controls brightness; the MOSFET carries the strip current. See [Adafruit's analog LED-strip PWM guide](https://learn.adafruit.com/rgb-led-strips/usage) for the switching principle (its illustrated strips use a different supply voltage).

Proposed connections for the two-wire strip:

| Connection | Destination |
| --- | --- |
| Pi 5 V, physical pin 2 or 4 | LED strip positive |
| LED strip negative | MOSFET drain / dimmer load negative |
| MOSFET source / dimmer power ground | Pi ground, e.g. physical pin 6 |
| GPIO12, physical pin 32 | Dimmer PWM input, or discrete gate through a series resistor |
| Discrete MOSFET gate | Pull-down resistor to source, keeping lighting off before PWM starts |

GPIO12 is a candidate hardware-PWM output outside VSMP's current display assignments: GPIO8, GPIO10, GPIO11, GPIO17, GPIO24, and GPIO25. Confirm overlay, audio, and other service usage before configuring PWM. Prefer it over GPIO18, which newer Waveshare HATs assign to PWR. A header breakout or splitter may be needed for physical access while the HAT is installed.

The dimmer is **proposed, not installed or tested**. Verify the strip's actual current, supply label, rail voltage, and undervoltage flags with LEDs at full brightness during Pi load and display refresh before treating this as a validated shared-power arrangement. If there is insufficient headroom, power the LEDs from a separate 5 V supply and join grounds; do not join the two supplies' positive rails.
