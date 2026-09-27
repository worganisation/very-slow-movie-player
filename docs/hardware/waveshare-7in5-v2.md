# VSMP e-paper hardware reference

The Amazon order placed **16 November 2020** identifies a **Waveshare 7.5-inch
e-Paper Display HAT Module V2 Kit**, monochrome, **800 × 480**, SPI, public
[ASIN B075R4QY3L](https://www.amazon.co.uk/dp/B075R4QY3L). The current
ordered-item link is useful for identifying the purchase, but its title may
have changed since 2020. The physical panel's back label, PCB revision, and
controller markings have **not** been inspected, so the exact hardware revision
remains unconfirmed. No private order details are stored here.

[Waveshare's 7.5-inch e-Paper HAT manual](https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_Manual)
distinguishes the 640 × 384 V1 from the 800 × 480 monochrome V2. It recommends
the `7.5V2_old` program for V2 units sold **before September 2023**, and the
`7.5V2` program for later units. The 2020 purchase date points to the older
family, subject to checking the panel itself. VSMP's driver is vendor-derived
and locally modified; it is not a verbatim copy of either current program.

## Manufacturer reference index

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
