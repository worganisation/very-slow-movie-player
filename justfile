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
