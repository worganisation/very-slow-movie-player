set dotenv-load

# List available recipes
default:
    @just --list

# Remove the local virtual environment
clean:
    rm -rf .venv

create:
    just clean
    just install-all

# Install locked runtime dependencies into .venv
sync:
    uv sync --frozen --no-dev

# Install all locked dependencies for local development
sync-dev:
    uv sync --frozen --all-groups

# Check source types with the locked development environment
typecheck:
    uv run --frozen basedpyright

install-python: sync

# Install the systemd unit without starting the service
install-service:
    sudo cp service/vsmp.service /etc/systemd/system/
    sudo systemctl daemon-reload

install-all: install-python install-service

disable:
    sudo systemctl disable vsmp.service

enable:
    sudo systemctl enable vsmp.service

restart:
    sudo systemctl restart vsmp.service

run:
    .venv/bin/python very_slow_movie_player/main.py

start:
    sudo systemctl start vsmp.service

stop:
    sudo systemctl stop vsmp.service

tail:
    clear && sudo journalctl -u vsmp.service -f -n 50

# Update the checkout only when it can fast-forward cleanly
update:
    git pull --ff-only
    just install-all

# Deploy an existing stable release tag to this Pi
deploy tag:
    #!/usr/bin/env bash
    set -euo pipefail
    tag={{ quote(tag) }}
    if [[ ! "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Expected a stable semantic release tag" >&2
        exit 1
    fi
    if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
        echo "Working tree is dirty; preserve local changes before deploying" >&2
        exit 1
    fi
    git fetch --prune --tags origin
    target="$(git rev-parse --verify "refs/tags/${tag}^{commit}")"
    if ! git merge-base --is-ancestor "$target" origin/main; then
        echo "Release tag is not on origin/main" >&2
        exit 1
    fi
    git switch --detach "$target"
    uv sync --locked --no-dev
    sudo -n systemctl restart vsmp.service
    # Type=simple can report active before application startup has finished.
    # Require one uninterrupted process for longer than RestartSec (5s).
    pid="$(systemctl show --value --property=MainPID vsmp.service)"
    restarts="$(systemctl show --value --property=NRestarts vsmp.service)"
    if [[ ! "$pid" =~ ^[1-9][0-9]*$ || ! "$restarts" =~ ^[0-9]+$ ]]; then
        echo "VSMP did not start with a valid process" >&2
        exit 1
    fi
    for ((attempt = 0; attempt < 20; attempt++)); do
        sleep 1
        if ! sudo -n systemctl is-active --quiet vsmp.service || \
            [[ "$(systemctl show --value --property=MainPID vsmp.service)" != "$pid" ]] || \
            [[ "$(systemctl show --value --property=NRestarts vsmp.service)" != "$restarts" ]]; then
            echo "VSMP exited or restarted during the deployment health check" >&2
            exit 1
        fi
    done
