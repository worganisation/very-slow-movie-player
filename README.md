# Very Slow Movie Player

## Hardware

This project uses a **Waveshare 7.5-inch e-Paper HAT V2 kit**, with an
**800 × 480 monochrome panel** and the **e-Paper Driver HAT** for Raspberry Pi.
The November 2020 purchase is recorded in the
[hardware reference](docs/hardware/waveshare-7in5-v2.md), alongside local copies
of the manufacturer manuals, panel specification, and HAT schematic. Start
there for hardware discussions; the adapter's physical PCB revision has not
been inspected.

The host is a Raspberry Pi 4 Model B Rev 1.4, confirmed over SSH. The
[Pi and lighting notes](docs/hardware/pi-and-lighting.md) record this inspection
and the proposed power budget and PWM dimmer for the purchased 5 V COB strip.

## Media source

`VSMP_SOURCE` selects `local` (the default) or `immich`. Both the service and
playlist command load the repository's private `.env` and validate **all**
settings before playback, downloads, or GPIO startup. Populate the complete
configuration before deploying, including a valid local video path, Immich
credentials, and playlist ID even when their source is not selected:

```dotenv
VSMP_SOURCE=local
VSMP_VIDEO_PATH=/home/worgarside/movies/example.mp4
IMMICH_URL=https://immich.example.com
IMMICH_API_KEY=replace-with-private-api-key
IMMICH_ALBUM_ID=00000000-0000-0000-0000-000000000000
YT_PLAYLIST_ID=replace-with-public-playlist-id
MQTT_HOST=homeassistant.local
MQTT_PORT=1883
MQTT_TLS=false
MQTT_TOPIC_PREFIX=vsmp
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_DEVICE_ID=vsmp_pi
MQTT_DEVICE_NAME=Very Slow Movie Player
VSMP_IMAGE_GAMMA=1.7
VSMP_VIDEO_FRAME_DELAY_SECONDS=180
VSMP_PHOTO_FRAME_DELAY_SECONDS=300
VSMP_VIDEO_FRAME_ADVANCE=12
IMMICH_MEDIA_TYPE=both
VSMP_PLAYBACK_ENABLED=true
ALWAYS_RESTART_VIDEOS=false
VSMP_ALLOW_MOCK_HARDWARE=false
```

The first seven values are required except `VSMP_SOURCE`, which defaults to
`local`. `VSMP_VIDEO_PATH` must be an existing absolute file, `IMMICH_URL` an
HTTP(S) server URL without credentials or query parameters, and
`IMMICH_ALBUM_ID` a UUID. The API key, playlist ID, and bare `MQTT_HOST` must be
nonblank. The remaining values are optional and show their defaults. Invalid values fail
startup without printing the API key. Update the Pi's existing `.env` before
deploying this version; the deployment health check will reject a service
that cannot start. Environment variables supplied by systemd override the
same values in `.env`.

### Local video

Set `VSMP_VIDEO_PATH` in the Pi's private `.env` to the absolute path of a
local video, then start `vsmp.service`. VSMP waits three minutes after each
displayed video frame by default and records its position in
`very_slow_movie_player/.media/` so it can resume after a restart. The media
directory is created on first run. The video loops after its final frame when
systemd restarts the service.

`VSMP_VIDEO_FRAME_DELAY_SECONDS` sets the wait after each video frame for both
local videos and Immich videos. It defaults to 180 seconds. Still images use
`VSMP_PHOTO_FRAME_DELAY_SECONDS` (default 300 seconds). Both accept finite values
from 180 to 86400 seconds. `VSMP_VIDEO_FRAME_ADVANCE` defaults to 12 source
frames per update. The startup clear is a separate panel operation; the
playback scheduler enforces at least 180 seconds between completed frame
refreshes, including button requests.

Progress is saved per video in `.media/state.sqlite3`. On first startup, VSMP
imports an existing `.media/progress_log.json` and `.media/ha_controls.json` in
one transaction. The original JSON files remain untouched as recovery copies.
Invalid JSON or invalid values stop startup and roll back the import; repair the
file and restart. Once the database has schema version 1, the JSON files are
ignored, so stale copies cannot overwrite newer playback or control state.

Set `VSMP_SOURCE=local` in the complete configuration above.

### Immich album

Create an Immich API key with album read, asset read, asset view, and asset download access,
then set `VSMP_SOURCE=immich` in the complete configuration above.

`IMMICH_URL` is the server URL with or without `/api`. Use the album's UUID,
not its name. VSMP pages through the album with Immich's metadata search API,
downloads preview images and original videos into `.media/immich/`, then
displays each image for five minutes and videos at the usual frame interval.
Downloads use asset IDs as filenames and are cached across restarts. An empty
album leaves the current display undisturbed; VSMP checks again after five
minutes. Original videos remain cached, so size the Pi's `.media` storage for
the album or prune `.media/immich/` when needed.

The integration follows Immich's [OpenAPI specification](https://github.com/immich-app/immich/blob/main/open-api/immich-openapi-specs.json)
for metadata search, image previews, and original downloads. Older servers use
the `albumIds` search field; when that field is rejected, VSMP uses the newer
structured album filter and cursor.

`VSMP_IMAGE_GAMMA` controls midtone darkening before the frame is dithered for
the monochrome panel. Its default is `1.7`; set it to `1.0` for the original
brightness, or increase it for a darker image. The accepted range is 0.1 to 10.

Playback requires the Raspberry Pi GPIO and SPI Python modules. If either is
missing, startup fails so the service does not report a working display that
is only a software mock. For local development without a panel, set
`VSMP_ALLOW_MOCK_HARDWARE=true` explicitly. On service stop or playback
failure, VSMP attempts to sleep the display and release its GPIO and SPI
resources before exiting.
After each completed clear or frame refresh, the driver powers off the panel
booster and VCOM during the long playback pause, then powers on for the next
refresh. This keeps the displayed image and controller registers without
holding drive voltage throughout the pause. Command-order checks are
hardware-free; verify image quality and power behavior on the physical panel
before relying on this change in a deployed service.
The verified purchase model, official manuals and driver references, current
software pinout, and physical-validation limits are recorded in the
[Waveshare hardware reference](docs/hardware/waveshare-7in5-v2.md). Waveshare
recommends at least 180 seconds between refreshes; VSMP's video-frame dwell
now defaults to 180 seconds. Confirm the actual panel revision and long-term
display quality before enabling revision-specific fast/partial modes.

Frame packing and SPI block writes reduce Python and GPIO transfer overhead.
The panel still uses its normal full refresh, including its visible flash.

### Home Assistant controls

Set `MQTT_HOST` to the Home Assistant broker. VSMP maintains one MQTT connection
throughout playback, reconnects and republishes discovery/state after broker
outages or Home Assistant birth, and keeps playing when MQTT is unavailable.
Discovery uses the stable device ID `vsmp_pi` by default. The existing
**Displayed frame** image keeps its identity and retained PNG topic; it remains
visible even when the service disconnects. Control and status entities use an
availability topic and become unavailable when the service disconnects.

The device exposes Source, Playback enabled, Video refresh interval, Photo
refresh interval, Video frame advance, Image gamma, Local video, Immich album,
Immich media type, and Always restart videos. The album selector shows names
from Immich and disambiguates duplicates with IDs; the selected value is stored
as an immutable album UUID. `IMMICH_MEDIA_TYPE` defaults to `both` and accepts
`photos` or `videos`. The controls act without a service restart. Pausing leaves
the current physical image in place. Source changes wake a playback wait but
cannot interrupt an active panel refresh. An edited gamma takes effect on the
next frame; use **Redisplay current frame** to show it sooner. **Next** advances
one video step or skips the displayed Immich asset, and **Restart current video**
starts that video's frame position at zero. Both refresh interval controls have
a three-minute minimum, which also applies to manual requests. Playback status,
the displayed media filename without its extension, one-based current video
frame, total video frame count, last successful refresh, next scheduled refresh,
and last error are reported as sensors. The frame sensors are unknown while a
photo is displayed.
Configured source and current displayed media are separate while a source
change is pending.

All commands use `vsmp/vsmp_pi/command/...`; confirmed states use
`vsmp/vsmp_pi/state/...`. Discovery and state are retained; commands are not.
Retained command replays are ignored. Invalid commands leave the last valid
setting in place and update **Last error**. The Local video command must specify
an absolute readable path containing a playable video stream. Immich album
options require the API key's album read permission in addition to the asset
permissions listed above. Broker outages do not stop playback. The most recent
successfully displayed frame is republished when MQTT reconnects.

The service eagerly validates the entire `.env` configuration, including
inactive sources. Home Assistant changes persist only explicit non-secret
overrides in `very_slow_movie_player/.media/state.sqlite3` on the Pi. Systemd
environment overrides `.env`; database overrides take precedence only for their
named settings. To restore an environment value, stop the service and delete its
row from `control_overrides` using `sqlite3`, or delete all rows to restore all
environment defaults. Back up the database before editing it. A malformed or
invalid override stops startup for repair. Keep the database and `.env` private;
never place the Immich API key, broker password, or other secrets in either
override storage or the legacy JSON files. SQLite `PRAGMA user_version` records
the schema; an unknown version stops startup. To recover from database damage,
stop the service, preserve the database for diagnosis, then restore a backup.
The preserved JSON copies can seed a fresh database, but reflect only their
original import-time state.

For authenticated MQTT, set both `MQTT_USERNAME` and `MQTT_PASSWORD`; for TLS,
set `MQTT_TLS=true` and the broker port. The system CA store verifies the broker
certificate. Broker readers can access the retained frame image. Changing the
MQTT device ID or prefixes creates new discovery topics; remove obsolete
retained configs manually after a rename. No deployment or physical panel
validation is implied by these controls.

## YouTube playlist downloads

Set `YT_PLAYLIST_ID` to a public playlist ID and run
`uv run python very_slow_movie_player/youtube.py`. The downloader creates
`very_slow_movie_player/.media/`, fetches every page of the playlist, and saves
videos as MP4 files with their YouTube IDs in the filenames. Its
`.youtube-download-archive.txt` records successfully downloaded IDs so later
runs skip them. Unavailable videos are skipped while the rest of the playlist
continues; the command exits nonzero if any download fails. `YT_API_KEY` is no
longer needed.

YouTube downloads also require system `ffmpeg` and `ffprobe`, plus a JavaScript
runtime for yt-dlp's bundled `yt-dlp-ejs` challenge solver. Install
[Deno 2.3+](https://github.com/yt-dlp/yt-dlp/wiki/EJS) on the Pi (enabled by
yt-dlp by default). See the linked guide if using another supported runtime.
Existing videos from the old downloader are not in the archive, so the first
run may download those videos again.

## Tooling

Requires Python 3.12, [uv](https://docs.astral.sh/uv/),
[prek](https://prek.j178.dev/), and [just](https://just.systems/).
Install native [`ffmpeg` and `ffprobe`](ffmpeg/README.md) for video playback.

```bash
just sync-dev            # install locked dependencies into .venv
just typecheck           # check source types with basedpyright
prek install             # install Git hooks
prek run --all-files     # run repository checks
```

The systemd unit still launches `.venv/bin/python`. On the Pi, `just sync`
installs only locked runtime dependencies; `just install-service` installs the
existing unit, and `just install-all` does both. These recipes do not start the
service. `just --list` shows the remaining service commands.

If the display's busy signal stays active for more than 30 seconds, playback
raises an error so systemd can restart the service. Inspect the service journal
and the display wiring if the error recurs.

The old Pipenv dependency updater and its cron installer have been removed.
If they were installed on a Pi, check the `worgarside` user's `crontab -l` and
remove the entry for `utilities/dep_updater/dep_updater.sh` with `crontab -e`.
The release deployment workflow installs locked dependencies with uv.

## Release deployment

The **Semantic Release** workflow is started manually on `main`. When it
publishes a stable GitHub release, **Deploy VSMP Release** joins Tailscale and
deploys that exact tag to VSMPPi. The same deployment workflow can redeploy a
published tag through **Actions → Deploy VSMP Release → Run workflow**. A
prerelease, draft, or tag without a published release is not deployed.

The Pi checkout is `/home/worgarside/very-slow-movie-player`. `just deploy
<tag>` refuses local changes, checks that the tag is on `origin/main`, installs
locked runtime dependencies with uv, then restarts `vsmp.service`. Deployment
reports success only if the same service process stays active for 20 seconds
without a systemd restart; this catches startup crashes but does not prove that
frames are rendering on the display.
The systemd unit in `service/vsmp.service` uses the Pi's `worgarside` account.
The Pi needs Git, uv, just, Tailscale, SSH, SPI, and a populated private `.env`
before its first deployment. Allow the deploy account to run only
`systemctl restart vsmp.service` and `systemctl is-active vsmp.service` without
an interactive sudo password.

Configure a `production-deploy` GitHub environment with these values:

| Kind | Name | Purpose |
| --- | --- | --- |
| Variable | `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client tagged `tag:ci` |
| Secret | `TS_OAUTH_SECRET` | Matching OAuth secret |
| Variable | `VSMP_TAILSCALE_HOSTNAME` | Pi's Tailscale name, e.g. `vsmppi` |
| Variable | `DEPLOY_SSH_USER` | Pi SSH account, `worgarside` |
| Secret | `DEPLOY_SSH_PRIVATE_KEY` | VSMP-specific deploy key |
| Secret | `VSMP_SSH_KNOWN_HOSTS` | Verified `vsmppi ssh-ed25519 ...` host key |

Tailscale policy must allow `tag:ci` to reach the Pi's SSH port. Install the
deploy public key in the Pi user's `authorized_keys`, and verify the Pi's SSH
host key out of band before storing it in `VSMP_SSH_KNOWN_HOSTS`.
