#!/bin/sh
set -eu

version="$(
  curl -fsSL https://pypi.org/pypi/kptncook/json |
    python3 -c '
import json
import sys
from pip._vendor.packaging.version import InvalidVersion, Version

releases = json.load(sys.stdin)["releases"]
stable = []
for raw_version, files in releases.items():
    try:
        version = Version(raw_version)
    except InvalidVersion:
        continue
    if files and not version.is_prerelease and not version.is_devrelease:
        if not all(file.get("yanked", False) for file in files):
            stable.append(version)
if not stable:
    raise SystemExit("PyPI returned no non-yanked stable KptnCook release")
print(max(stable))
'
)"

export KPTNCOOK_VERSION="$version"
docker compose build --pull --no-cache sync
