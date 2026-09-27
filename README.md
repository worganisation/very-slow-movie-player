# Very Slow Movie Player

## Media source

`VSMP_SOURCE` selects `local` (the default) or `immich`. Local playback uses
`VSMP_VIDEO_PATH` exactly as before. Keep the source settings in the Pi's
private `.env`.

### Local video

Set `VSMP_VIDEO_PATH` in the Pi's private `.env` to the absolute path of a
local video, then start `vsmp.service`. VSMP displays one frame every two
minutes and records its position in `very_slow_movie_player/.media/` so it can
resume after a restart. The media directory is created on first run. The
video loops after its final frame when systemd restarts the service.

Progress is saved by replacing the log atomically. If the JSON is damaged,
VSMP preserves it beside the log as `progress_log.json.corrupt-*`, writes a
warning to the service journal, and starts the video from the beginning.

For example:

```dotenv
VSMP_VIDEO_PATH=/home/worgarside/movies/example.mp4
```

### Immich album

Create an Immich API key with album read, asset read, asset view, and asset download access,
then set:

```dotenv
VSMP_SOURCE=immich
IMMICH_URL=https://immich.example.com
IMMICH_API_KEY=your-private-api-key
IMMICH_ALBUM_ID=00000000-0000-0000-0000-000000000000
```

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
brightness, or increase it for a darker image.

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

Playback requires the Raspberry Pi GPIO and SPI Python modules. If either is
missing, startup fails so the service does not report a working display that
is only a software mock. For local development without a panel, set
`VSMP_ALLOW_MOCK_HARDWARE=true` explicitly.
On service stop or playback failure, VSMP attempts to sleep the display and
release its GPIO and SPI resources before exiting.

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
