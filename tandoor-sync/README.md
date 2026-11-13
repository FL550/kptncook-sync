# KptnCook favorites to Tandoor

This standalone service imports current KptnCook favorites into one Tandoor
cookbook. It runs once at startup and waits 24 hours after each completed run.
It only creates recipes: unfavoriting a recipe never deletes it, and an
imported recipe is never recreated or updated, even if it is later removed
from Tandoor. State starts empty; the first run can duplicate recipes already
in Tandoor.

The KptnCook mobile API is unofficial and may change. The service uses the
latest stable `kptncook` package from PyPI, without copying or modifying its
code. Each image build checks PyPI for the then-current stable release, and
the resolved version is recorded in `/usr/share/kptncook-version` and logged
at startup. It checks for required client interfaces at runtime and refuses
to run when they are unavailable. A published package change may require
updating the adapter; check image logs and run the test suite before deploying
an upgrade.

## Configure and run

1. Copy `.env.example` to `.env`, fill it in, and restrict it to your user:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

   Do not commit or share `.env`.
2. Set `KPTNCOOK_API_KEY` and `KPTNCOOK_ACCESS_TOKEN` for the KptnCook account
   whose favorites should be imported. If you use the companion CLI,
   `kptncook setup` initializes its configuration and
   `kptncook kptncook-access-token` fetches the account token without printing
   it. That CLI stores the token in `~/.kptncook/.env`; copy the required
   values into this service's `.env`. See the
   [companion CLI environment guide](../kptncook/README.md#environment). Set
   `KPTNCOOK_ACCOUNT_ID` to a stable, non-secret label for the same account.
3. Create a Tandoor API Bearer token for the destination instance. Grant only
   the permissions needed to create recipes and upload recipe images in the
   target cookbook. The exact scope names depend on the Tandoor version; check
   that instance's API settings/schema. Set `TANDOOR_URL` to the instance base
   URL and `TANDOOR_SPACE_ID` to the numeric ID of the intended cookbook/space,
   not a user ID. The opt-in integration test also requires read and delete
   permissions.
4. Leave `KPTNCOOK_FAVORITES_COMPLETE=false` until you have completed the
   inventory check below. It defaults to false and prevents any source request
   or destination write until completeness has been confirmed; unresolved
   favorites also stop the run before writes.
5. `KPTNCOOK_API_URL` defaults to `https://mobile.kptncook.com`.
   `KPTNCOOK_LANG` and `KPTNCOOK_STORE` select the source language and regional
   store; both default to `de`. Use HTTPS for both services except local
   development. Credentials are runtime configuration and are never written
   to the history database.
6. Compose pins history to `/data/history.sqlite3` in the persistent
   `sync-state` volume. `STATE_PATH` in `.env` is not used by this Compose
   deployment; change the Compose mount and path together if you intentionally
   relocate history. Keep one running service instance per state volume. The
   named volume must support SQLite locking/durability and be writable by
   container UID/GID 10001. Docker initializes a new named volume from the
   image's `/data` permissions; check ownership if using an external volume.
7. Building requires Docker Engine with Compose/Buildx, `curl`, and Python 3
   with `pip` on the host, plus access to PyPI and the Docker registry. From
   this directory, build before starting:

   ```sh
   sh build.sh
   ```

   `build.sh` reads the current stable release from PyPI, passes that exact
   version as a build argument, and builds without cache. The version is
   verified in the image, logged at startup, and recorded in the
   `org.opencontainers.image.version` label. An already-built image never
   updates itself. Retain the prior image for rollback. Run `sh build.sh`
   before `docker compose up -d` on a fresh install or upgrade; Compose's
   implicit build does not resolve the required KptnCook version argument.

### Verify the KptnCook inventory before the first sync

After building the image and filling in `.env`, run this read-only probe. It
uses the KptnCook client version in the image and the account credentials and
language/store settings from `.env`; it prints only the number of favorites
returned. It does not contact Tandoor or change sync history.

```sh
docker compose run --rm --entrypoint python sync -c '
from kptncook.api import KptnCookClient

client = KptnCookClient()
try:
    method = getattr(client, "list_favorites", None) or getattr(client, "get_favorites")
    favorites = method()
    print(f"Favorites returned by this image: {len(favorites)}")
except Exception as exc:
    print(f"Could not retrieve favorites ({type(exc).__name__}); check credentials and connectivity.")
    raise SystemExit(1) from None
finally:
    client.close()
'
```

Compare that number with an independently visible total for the same account
in the KptnCook app, after its favorites list has fully loaded. The current
client makes one favorites request and does not follow pagination; this probe
does not show raw pagination metadata. Because the API is undocumented, a
successful request or matching count alone cannot prove that the server did
not truncate the list. If you cannot independently confirm the complete total,
leave `KPTNCOOK_FAVORITES_COMPLETE=false` and do not start the sync. Once
confirmed, set it to `true` in `.env`, then start the scheduler:

```sh
docker compose up -d
docker compose logs -f sync
```

The normal mode is the scheduler: `docker compose up -d` starts the container,
which performs one sync immediately and then repeats automatically. The loop
lives inside the service; no host cron job or separate scheduler is needed.
After each completed run it waits `SYNC_INTERVAL_SECONDS` (default `86400`,
24 hours) before starting the next one, so runs never overlap. Running
`docker compose up` without `-d` keeps the same scheduler in the foreground.
The container uses `restart: unless-stopped`, so Docker restarts it after an
unexpected exit. If a sync run fails, the error is logged and the next attempt
still waits the full configured interval; there is no immediate retry/backoff.

There is no `--once` option or manual “sync now” command. The available CLI
commands are for reconciling uncertain writes only:

```sh
docker compose run --rm sync --help
docker compose run --rm sync list-uncertain
docker compose run --rm sync mark-imported SOURCE_OID DESTINATION_ID
docker compose run --rm sync clear-for-retry SOURCE_OID --confirm-no-remote-recipe
```

`--rm` is a Docker Compose option that removes the temporary admin container
when the command exits; it does not affect the service container. Admin
commands do not start a sync. `HTTP_TIMEOUT_SECONDS` bounds network requests
(1–120 seconds) for source and Tandoor API requests. Cover downloads use the
upstream downloader's separate 60-second total limit and may take longer than
this setting. The Compose container runs non-root and drops Linux capabilities.

## History and recovery

`sync-state` contains `history.sqlite3`; back it up and restore it coherently
with the Tandoor backup. For a simple consistent copy, stop the service and
copy the database from its container:

```sh
mkdir -p "$HOME/backups/kptncook-tandoor"
docker compose stop sync
docker compose cp sync:/data/history.sqlite3 "$HOME/backups/kptncook-tandoor/history.sqlite3"
chmod 600 "$HOME/backups/kptncook-tandoor/history.sqlite3"
docker compose start sync
```

Restore while the service is stopped. The ownership command is needed because
the container runs as UID/GID 10001:

```sh
docker compose stop sync
docker compose cp "$HOME/backups/kptncook-tandoor/history.sqlite3" sync:/data/history.sqlite3
docker compose run --rm --user 0 --entrypoint python sync -c \
  'import os; os.chown("/data/history.sqlite3", 10001, 10001); os.chmod("/data/history.sqlite3", 0o600)'
docker compose start sync
```

Coordinate the history backup/restore with the corresponding Tandoor backup.
Losing or rolling back this volume can reimport recipes and cause duplicates.
The database binds history to the configured source account label, Tandoor base
URL, and cookbook ID; a mismatch is an error, not a new implicit history.

Before each create, the service persists an `in_flight` marker. On a validated
create response, it persists an `imported` tombstone before attempting the
optional image upload. An interrupted or ambiguous create remains `uncertain`
and is never retried automatically: exactly-once creation cannot be guaranteed
across HTTP and SQLite. A failed image upload is logged and is not retried.

Inspect Tandoor manually before resolving an uncertain write. Mark it imported
only with a verified destination ID. Clear it only after confirming no recipe
was created; this can cause a duplicate if that assessment is wrong. A search
result or matching title alone does not establish whether the write succeeded.
Changing/removing favorites does not clear history; clearing the whole state
volume resets all history and carries the same duplication risk. To
intentionally reset it, stop the service and remove the Compose project volumes:

```sh
docker compose down -v
```

This removes the `sync-state` history volume (and any other volumes declared
for this Compose project). It does not remove Tandoor recipes; the next sync
can create duplicates.

## Limitations

The service relies on an unofficial source API and on the live Tandoor
instance's API behavior. The intended Tandoor compatibility baseline is 2.7.2;
verify recipe creation, nested step ingredients, keywords, cookbook assignment,
multipart image upload, token scopes, and deletion behavior against a
disposable instance before using a new Tandoor release. Automated tests use
mocked HTTP/state and never modify a real recipe library. The service cannot
prove completeness from a truncated response that carries no pagination
metadata; that is why the explicit completeness confirmation gates all reads
and writes by default. Malformed or unresolved favorites stop the run before
writes.

Run unit tests from this directory with `uv sync --extra test && uv run pytest`.
An opt-in API contract test creates a temporary recipe, uploads a JPEG, checks
nested ingredients and cookbook assignment, then deletes the recipe. Run it
only against a disposable Tandoor instance with a token scoped for create,
read, image upload, and delete:

```sh
TANDOOR_INTEGRATION_CONFIRM=I_ACCEPT_DISPOSABLE_TANDOOR_DATA \
TANDOOR_TEST_URL=https://disposable-tandoor.example \
TANDOOR_TEST_TOKEN=... \
TANDOOR_TEST_SPACE_ID=1 \
TANDOOR_TEST_IMAGE=/path/to/test.jpg \
uv run pytest -q tests/test_tandoor_integration.py
```

The integration test is skipped unless the explicit confirmation is set.
Never configure it with a real user's library.

Use the source API and client only in accordance with their terms and for
personal use where permitted. Do not expose logs publicly: logs contain source
recipe IDs and operational status, but not tokens, request URLs, or recipe
bodies.
