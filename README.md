# Very Slow Movie Player

## Tooling

Requires Python 3.12, [uv](https://docs.astral.sh/uv/),
[prek](https://prek.j178.dev/), and [just](https://just.systems/).

```bash
just sync-dev            # install locked dependencies into .venv
prek install             # install Git hooks
prek run --all-files     # run repository checks
```

The systemd unit still launches `.venv/bin/python`. On the Pi, `just sync`
installs only locked runtime dependencies; `just install-service` installs the
existing unit, and `just install-all` does both. These recipes do not start the
service. `just --list` shows the remaining service commands.
