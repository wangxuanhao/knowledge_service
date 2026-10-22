#!/usr/bin/env bash
# Invoked by migrate_docker_storage.py only after host-side stopped-writer checks.
set -euo pipefail
if ! awk '$5 == "/source" && $6 ~ /(^|,)ro(,|$)/ {ok=1} END {exit !ok}' /proc/self/mountinfo; then
    printf 'Refusing a source that is not a read-only mount\n' >&2
    exit 1
fi
if find /target -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    printf 'Refusing a nonempty target volume\n' >&2
    exit 1
fi
printf 'Capturing stopped source metadata\n'
source_counts=$(find /source -type f -printf '%s\n' | awk '{n++; b+=$1} END {printf "%d %.0f",n,b}')
printf 'Source files and bytes: %s\n' "$source_counts"
printf 'Archiving stopped read-only source\n'
tar --sort=name --numeric-owner --format=gnu -C /source -cf /tmp/storage-source.tar .
source_hash=$(sha256sum /tmp/storage-source.tar | cut -d ' ' -f 1)
printf 'Restoring Linux target\n'
tar --same-owner --same-permissions -C /target -xf /tmp/storage-source.tar
printf 'Verifying restored target\n'
target_counts=$(find /target -type f -printf '%s\n' | awk '{n++; b+=$1} END {printf "%d %.0f",n,b}')
target_hash=$(tar --sort=name --numeric-owner --format=gnu -C /target -cf - . | sha256sum | cut -d ' ' -f 1)
printf 'Target files and bytes: %s\n' "$target_counts"
printf 'Source archive SHA256: %s\nTarget archive SHA256: %s\n' "$source_hash" "$target_hash"
test "$source_counts" = "$target_counts"
test "$source_hash" = "$target_hash"
printf 'Verified byte-exact stopped-volume copy\n'
