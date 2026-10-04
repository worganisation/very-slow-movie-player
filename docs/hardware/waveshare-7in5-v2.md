# VSMP e-paper hardware reference

For the confirmed Raspberry Pi model and the purchased COB strip's proposed
power/dimming arrangement, see [Pi and lighting notes](pi-and-lighting.md).

The Amazon order placed **16 November 2020** identifies a **Waveshare 7.5-inch
e-Paper Display HAT Module V2 Kit**, monochrome, **800 × 480**, SPI, public
[ASIN B075R4QY3L](https://www.amazon.co.uk/dp/B075R4QY3L). The current
ordered-item link is useful for identifying the purchase, but its title may
have changed since 2020. The physical panel's back label, PCB revision, and
controller markings have **not** been inspected, so the exact hardware revision
remains unconfirmed. No private order details are stored here.

The ordered kit includes the panel and the Raspberry Pi **Waveshare e-Paper
Driver HAT** adapter. **V2 describes the display family, not a verified HAT
PCB revision.** The order was checked again in Firefox on **3 October 2026**:
the ordered-item title still identifies the V2 kit and the same ASIN. The
public listing can change; use this purchase record and the repository's
`epd7in5_v2.py` driver together when identifying the installed display family.

[Waveshare's 7.5-inch e-Paper HAT manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_Manual)
distinguishes the 640 × 384 V1 from the 800 × 480 monochrome V2. It recommends
the `7.5V2_old` program for V2 units sold **before September 2023**, and the
`7.5V2` program for later units. The 2020 purchase date points to the older
family, subject to checking the panel itself. VSMP's driver is vendor-derived
and locally modified; it is not a verbatim copy of either current program.

## Manufacturer reference index

### Local copies for future hardware discussions

The following manufacturer files were retrieved on **3 October 2026**. The
PDFs are stored unchanged, with searchable text extracts beside them. The
manual text snapshot preserves the wiki article's text and links, including
its version-selection guidance; images are omitted. Use the PDFs or live wiki
for illustrations. Manufacturer copyright and attribution remain with
the respective manufacturers; these files are reference material, not
project-authored manuals. The additional mechanical drawings below were
retrieved on **4 October 2026**, unchanged from their manufacturer URLs.

| Local reference | Source and scope |
| --- | --- |
| [7.5-inch HAT manual, text snapshot](vendor/waveshare-7in5-hat-manual.txt) | [Waveshare manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_Manual), wiki revision `110993`; includes the pre/post-September 2023 V2 distinction. |
| [V2 panel specification, PDF](vendor/waveshare-7in5-v2-specification.pdf) / [text](vendor/waveshare-7in5-v2-specification.txt) | [Manufacturer download](https://files.waveshare.com/upload/6/60/7.5inch_e-Paper_V2_Specification.pdf), revision 2.0, 28 June 2019, 52 pages. |
| [e-Paper Driver HAT user manual, PDF](vendor/waveshare-e-paper-driver-hat-manual.pdf) / [text](vendor/waveshare-e-paper-driver-hat-manual.txt) | [Manufacturer download](https://files.waveshare.com/upload/8/8e/E-paper-driver-hat-user-manual.pdf), 14 pages; covers the adapter family and several display sizes. |
| [Driver HAT V2.2 schematic, PDF](vendor/waveshare-e-paper-driver-hat-v2.2-schematic.pdf) / [text](vendor/waveshare-e-paper-driver-hat-v2.2-schematic.txt) | [Manufacturer download](https://files.waveshare.com/upload/8/87/E-Paper-Driver-HAT-Schematic.pdf), one sheet marked **V2.2**. This identifies the document, not the installed PCB. |
| [Raspberry Pi 4B mechanical drawing, PDF](vendor/raspberry-pi-4b-mechanical-drawing.pdf) / [text](vendor/raspberry-pi-4b-mechanical-drawing.txt) | [Official manufacturer download](https://pip-assets.raspberrypi.com/categories/545-raspberry-pi-4-model-b/documents/RP-008343-DS-1-raspberry-pi-4-mechanical-drawing.pdf), one sheet showing board outline, hole centres, connectors, and component heights. |
| [Driver HAT Rev2.3 mechanical drawing, JPEG](vendor/waveshare-e-paper-driver-hat-rev2.3-mechanical-drawing.jpg) | [Manufacturer image](https://www.waveshare.com/img/devkit/LCD/e-Paper-Driver-HAT/e-Paper-Driver-HAT-details-size.jpg), visibly marked **Rev2.3**; not a verified drawing of the installed 2020 board. |

### Mechanical drawings for frame design

Use the original files above for geometry; searchable text extracts do not
preserve the positioning of dimension labels.

- **Panel:** page **5** of the local V2 specification, section **1.4 Mechanical
  Drawing of EPD module**, includes the front/rear views, active area, thickness,
  and projecting ribbon cable. The outline is **170.2 ± 0.2 × 111.2 ± 0.2 mm**;
  the active area is **163.2 ± 0.1 × 97.92 ± 0.1 mm** and thickness is
  **1.18 ± 0.1 mm**. Follow the dimensioned active-area placement rather than
  assuming it is centred.
- **Pi:** the mechanical drawing shows an **85 × 56 mm** PCB outline and
  **58 × 49 mm** mounting-hole centre spacing. Its component heights and
  connector projections also matter for the frame's rear cavity.
- **HAT:** the downloaded **Rev2.3** drawing shows an outline of
  **65 × 32.2 mm**, plus the separate adapter board at **31.75 × 17.5 mm**.
  The saved HAT manual instead lists **65 × 30.2 mm**. These references disagree
  on the HAT height, and the image depicts a later revision than the purchased
  kit. Measure the actual board outline, mounting holes, and component height
  before finalising its pocket or mounting posts; do not treat the newer image
  as an exact mechanical drawing of the installed board.

[SHA256SUMS](vendor/SHA256SUMS) records every local reference file. Verify
the copies from the repository root with:

```sh
(cd docs/hardware/vendor && shasum -a 256 -c SHA256SUMS)
```

The downloaded panel specification and HAT schematic still match the hashes
recorded below on 27 September 2026. The HAT manual and schematic are general
adapter references; compare their revision-specific circuitry and connectors
against the physical board before applying them. The current Driver HAT wiki
also documents Rev2.3 with a separate PWR pin; this is not evidence that the
2020 kit contains that revision.

### Upstream references and historical drivers

| Reference | Use | Reproducible identity |
| --- | --- | --- |
| [7.5-inch e-Paper HAT manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_Manual) | Version selection, wiring, operation, and precautions | Waveshare wiki page; consult its revision history when guidance changes. |
| [7.5-inch e-Paper V2 specification (PDF)](https://files.waveshare.com/upload/6/60/7.5inch_e-Paper_V2_Specification.pdf) | Monochrome panel dimensions, electrical limits, command details, and operating conditions | SHA-256 `a68d062a4e94211ac790125ec3c9a7e3edf1d6a1fa3b61705a6ba78f10a15d59` |
| [e-Paper Driver HAT schematic (PDF)](https://files.waveshare.com/upload/8/87/E-Paper-Driver-HAT-Schematic.pdf) | HAT circuitry and interface reference; compare against the actual board revision | SHA-256 `53bc27a0840c8368399ac7bb174ee6ee5118d0ac7ccfa752a12e6cf9ee414365` |
| [Pre-split V2 Python driver, 21 August 2023](https://github.com/waveshareteam/e-Paper/blob/7621f7d4cf6650e92c5054539c7c434b40e5e108/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd7in5_V2.py) | Historical command-family comparison | Git commit `7621f7d4cf6650e92c5054539c7c434b40e5e108`; file SHA-256 `13a97ddbbc9f5defabed471dc327b93b0b28518ceb394a7d7e3e9f17c1cf351a` |
| [Waveshare's later `V2_old` Python driver](https://github.com/waveshareteam/e-Paper/blob/166c012cdeb3bee49f59780c7764daa076aea343/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd7in5_V2_old.py) | Manufacturer's older-panel driver after the version split | Git commit `166c012cdeb3bee49f59780c7764daa076aea343`; Git blob `280ab478fb6faa125d981f39b85c92c121e9dae3` |

The PDF hashes above were calculated from the manufacturer downloads on
**27 September 2026**. They identify the files retrieved then, not an
unchanging promise about those URLs. To retrieve and verify a PDF later:

```sh
curl -fL --output 7.5inch-e-paper-v2-specification.pdf \
  https://files.waveshare.com/upload/6/60/7.5inch_e-Paper_V2_Specification.pdf
shasum -a 256 7.5inch-e-paper-v2-specification.pdf
```

## Current VSMP wiring and refresh behavior

The repository's `epdconfig.py` uses Raspberry Pi **BCM** numbering: reset 17,
data/command 25, chip select 8 (SPI0 CE0), and busy 24. It opens SPI bus 0,
device 0 in mode 0. These are **software settings**, not a physical inspection
of the 2020 HAT. The current manual also describes a PWR pin; VSMP does not
control that GPIO, so do not infer its wiring from this document.

VSMP sends complementary OLD (`0x10`) and NEW (`0x13`) RAM planes before a
single full refresh (`0x12`). It powers the panel drive off after a completed
refresh and uses deep sleep on shutdown. Full refresh visibly flashes; the
manufacturer calls this normal. The manual recommends at least **180 seconds**
between refreshes. VSMP's configurable video-frame dwell defaults to **180
seconds** for both local and Immich videos; Immich still images remain at five
minutes. The startup clear is a separate refresh and is not paced by the video
setting, so the setting is not a global panel refresh-rate guarantee.

Before changing initialization, waveform voltages, fast/partial modes, or the
refresh interval, inspect the actual panel and HAT markings and test clear,
black/white polarity, image quality, busy timing, and power behavior on the
physical device. Software-only command checks cannot establish panel behavior.
