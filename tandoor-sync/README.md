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

1. Copy `.env.example` to `.env` and set values privately. Do not commit `.env`.
2. Provide a KptnCook API key and a valid account access token. Set a stable
   `KPTNCOOK_ACCOUNT_ID` label for that account; do not put a token in the
   label. Set `TANDOOR_TOKEN` to a scoped Bearer token with permission to
   create recipes and upload recipe images in the target cookbook. The service
   also needs the Tandoor base URL and numeric cookbook/space ID.
3. Before enabling sync, verify that the resolved KptnCook client and your
   account's API response include all favorites, including pagination and
   maximum-result behavior. Only then set `KPTNCOOK_FAVORITES_COMPLETE=true`.
   It defaults to false and prevents any source request or destination write
   until completeness has been confirmed; unresolved favorites also stop the
   run before writes.
4. Use HTTPS for both services except local development. Restrict token scopes
   and access to `.env`; credentials are never written to the state database.
5. Ensure the named volume is writable by container UID/GID 10001 and is on a
   local filesystem supporting SQLite locking and durability. Keep a single
   running instance for each state volume.
6. From this directory, build and start with:

   ```sh
   docker compose build --pull --no-cache sync
   docker compose up -d
   docker compose logs -f sync
   ```

   `--no-cache` is required when you want a rebuild to check PyPI rather than
   reuse a cached package-install layer. An already-built image never updates
   itself. Retain the prior image for rollback.

The service executes a run immediately, then waits the configured
`SYNC_INTERVAL_SECONDS` (default 86400) after completion. Runs do not overlap.
`HTTP_TIMEOUT_SECONDS` bounds network requests (1–120 seconds). The Compose
container runs non-root, drops Linux capabilities, and restarts unless stopped.

## History and recovery

`sync-state` contains `history.sqlite3`; back it up and restore it coherently
with the Tandoor backup. Stop the service and use a consistent SQLite backup
procedure (or copy the volume while the service is stopped). Losing or rolling
back this volume can reimport recipes and cause duplicates. The database binds
history to the configured source account label, Tandoor base URL, and cookbook
ID; a mismatch is an error, not a new implicit history.

Before each create, the service persists an `in_flight` marker. On a validated
create response, it persists an `imported` tombstone before attempting the
optional image upload. An interrupted or ambiguous create remains `uncertain`
and is never retried automatically: exactly-once creation cannot be guaranteed
across HTTP and SQLite. A failed image upload is logged and is not retried.

Administrative commands do not initiate a sync:

```sh
docker compose run --rm sync list-uncertain
docker compose run --rm sync mark-imported SOURCE_OID DESTINATION_ID
docker compose run --rm sync clear-for-retry SOURCE_OID --confirm-no-remote-recipe
```

Inspect Tandoor manually before resolving an uncertain write. Mark it imported
only with a verified destination ID. Clear it only after confirming no recipe
was created; this can cause a duplicate if that assessment is wrong. A search
result or matching title alone does not establish whether the write succeeded.
Changing/removing favorites does not clear history; clearing the whole state
volume resets all history and carries the same duplication risk.

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

Use the source API and client only in accordance with their terms and for
personal use where permitted. Do not expose logs publicly: logs contain source
recipe IDs and operational status, but not tokens, request URLs, or recipe
bodies.
