# VSMP Raspberry Pi and frame lighting

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

The purchased dimmer board is the **Adafruit MOSFET Driver for Motors, Solenoids and LEDs, STEMMA JST PH 2 mm**, [product 5648](https://www.adafruit.com/product/5648). The connector is three-pin JST PH, not four-pin STEMMA QT. The driver switches the frame strip from GPIO12 with 500 Hz software PWM.

The [official guide](https://learn.adafruit.com/adafruit-mosfet-driver) covers pinouts, terminal blocks, Raspberry Pi PWM control and electrical limits. Its [downloads page](https://learn.adafruit.com/adafruit-mosfet-driver/downloads) supplies the schematic, fabrication print and links to CAD resources. The original manufacturer references are preserved unchanged; PDF text extracts normalize trailing whitespace for searching. Copyright and attribution remain with Adafruit Industries and Alpha & Omega Semiconductor, respectively.

| Local reference | Manufacturer source and scope |
| --- | --- |
| [User guide PDF](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-guide.pdf) / [text](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-guide.txt) | [Adafruit guide download](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-mosfet-driver.pdf); pinouts, wiring, PWM examples and drawings. |
| [Schematic PNG](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-schematic.png) | [Adafruit schematic](https://cdn-learn.adafruit.com/assets/assets/000/117/028/large1024/adafruit_products_schem.png); board circuit and component values. |
| [Dimensioned fabrication print PNG](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-fab-print.png) | [Adafruit fabrication print](https://cdn-learn.adafruit.com/assets/assets/000/117/029/large1024/adafruit_products_fab.png); dimensions are in inches, including the 1.00 × 0.70 inch board outline and 0.50 inch mounting-hole centre spacing. |
| [EagleCAD board](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver.brd) / [schematic](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver.sch) / [license](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-license.txt) | [Adafruit PCB source at commit `ea11049753a79330cc2651df233909c0191a5af4`](https://github.com/adafruit/Adafruit-MOSFET-Driver-STEMMA-PCB/tree/ea11049753a79330cc2651df233909c0191a5af4); original board and schematic files, under the supplied CC BY-SA 3.0 license. |
| [AO3406 datasheet PDF](vendor/adafruit-mosfet-driver/ao3406-datasheet.pdf) / [text](vendor/adafruit-mosfet-driver/ao3406-datasheet.txt) | [Manufacturer datasheet hosted by Adafruit](https://cdn-shop.adafruit.com/product-files/5648/5648_ds_AO3406.pdf); transistor electrical characteristics and package dimensions. Component ratings are not whole-board current ratings. |

The [metric fabrication print](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-fab-print-metric.png) is a project-maintained, AI-edited derivative of Adafruit Industries' drawing, supplied under the [same CC BY-SA 3.0 license](vendor/adafruit-mosfet-driver/adafruit-mosfet-driver-license.txt). Its five dimension labels use 25.4 mm per inch: 0.10 → 2.54 mm, 0.50 → 12.70 mm, 0.70 → 17.78 mm, 0.91 → 23.11 mm (rounded from 23.114 mm), and 1.00 → 25.40 mm. The artwork is redrawn, so use the original fabrication print or EagleCAD board for geometry rather than measuring pixels in the derivative.

For enclosure modelling, Adafruit also provides [3D CAD models for product 5648](https://github.com/adafruit/Adafruit_CAD_Parts/tree/main/5648%20MOSFET%20Driver). Use the drawings and CAD for geometry and check the actual board, connector clearance and cable bend space before finalising a mount. [SHA256SUMS](vendor/SHA256SUMS) identifies the local reference files alongside the display documentation.

## Installed power and dimming

The frame uses **180 mm** of the purchased strip and the Pi's **5.1 V, 3 A** power adapter. At the higher seller rating of 8 W/m, the strip's estimated load is **1.44 W / 0.288 A at 5 V**; this is not a current measurement. The Pi and LEDs share the 5 V rail. GPIO12 supplies only the control signal; the MOSFET carries the strip current.

| Connection | Destination |
| --- | --- |
| Pi 5 V, physical pin 4 | Adafruit `V+` |
| Pi ground, physical pin 6 | Adafruit `GND` |
| GPIO12, physical pin 32 | Adafruit `In` |
| Adafruit output `+` | LED strip positive |
| Adafruit output `−` | LED strip negative |

The output `−` is switched by the MOSFET; connecting the strip negative directly to ground bypasses brightness control. The HAT connects off-header through its eight-wire interface: VCC to physical pin 1, GND to 9, DIN to 19, CLK to 23, CS to 24, DC to 22, RST to 11, and BUSY to 18. Leave the display ribbon and HAT switches configured for the panel.

The VSMP service owns GPIO12 and exposes **Frame light** through MQTT discovery. It starts off and shuts down off, remembering the last nonzero brightness for off/on toggles within a process. Do not run a second GPIO/PWM controller on the same pin. See [frame lighting](../../README.md#frame-lighting) for MQTT behavior.

Check supply voltage and undervoltage/throttling flags with the LEDs at full brightness during Pi load and display refresh. Zero flags do not establish spare supply capacity. If the combined load exceeds the supply or wiring capacity, use a separate 5 V LED supply with common ground; do not join the supplies' positive rails.
