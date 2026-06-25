#!/bin/sh
# Entry point that reconciles bind-mount ownership before dropping privileges.
#
# When ./cookies, ./analytics and ./logs are bind-mounted from the host, they
# arrive owned by the host UID (often root), which the unprivileged "miner" user
# cannot write to. Running as root first, we ensure the directories exist and
# are owned by miner, then re-exec the actual command as miner via gosu.
set -e

for dir in /app/cookies /app/analytics /app/logs; do
    mkdir -p "$dir"
    # Best effort: on some hosts (e.g. read-only mounts) chown may fail; the
    # app still degrades gracefully thanks to its logging fallback.
    chown -R miner:miner "$dir" 2>/dev/null || true
done

# If we are root, drop to the unprivileged user; otherwise run as-is (e.g. when
# the container is already started with `user:` in compose).
if [ "$(id -u)" = "0" ]; then
    exec gosu miner "$@"
fi

exec "$@"
