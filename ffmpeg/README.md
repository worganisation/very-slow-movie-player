# FFmpeg runtime

VSMP uses the Python `ffmpeg-python` package to call the native `ffmpeg` and
`ffprobe` executables. Install both on the machine running VSMP and make sure
they are on its `PATH`. The Python package does not install those executables.

On Raspberry Pi OS:

```sh
sudo apt update
sudo apt install ffmpeg
```

On macOS with Homebrew:

```sh
brew install ffmpeg
```

Confirm both programs are available in the environment that runs VSMP:

```sh
command -v ffmpeg
command -v ffprobe
ffmpeg -version
ffprobe -version
```

The Pi's `vsmp.service` runs under `worgarside`. Check the service journal if
playback reports that either executable is missing. `just sync` installs the
locked Python dependencies; the native FFmpeg package is installed separately.
