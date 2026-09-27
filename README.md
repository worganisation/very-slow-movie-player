# Very Slow Movie Player

## Local playback

Set `VSMP_VIDEO_PATH` in the Pi's private `.env` to the absolute path of a
local video, then start `vsmp.service`. VSMP displays one frame every two
minutes and records its position in `very_slow_movie_player/.media/` so it can
resume after a restart. The media directory is created on first run. The
video loops after its final frame. The Google Photos album is no longer
consulted.

For example:

```dotenv
VSMP_VIDEO_PATH=/home/worgarside/movies/example.mp4
```

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

## Tooling

Requires Python 3.12, [uv](https://docs.astral.sh/uv/),
[prek](https://prek.j178.dev/), and [just](https://just.systems/).

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
