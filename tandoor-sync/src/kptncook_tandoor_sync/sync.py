from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .locking import locked_state
from .source import FavoriteRecipe, KptnCookSource, SourceError
from .state import StateStore
from .tandoor import TandoorClient, TandoorError

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    favorites: int = 0
    imported: int = 0
    already_handled: int = 0
    uncertain: int = 0
    image_warnings: int = 0
    failed: int = 0


def _cover_bytes(exporter: Any, recipe: object) -> bytes | None:
    try:
        return exporter.get_cover_image_bytes(recipe)
    except Exception as exc:
        logger.warning("Cover download skipped (%s)", type(exc).__name__)
        return None


def sync_once(
    settings: Settings,
    source: KptnCookSource | None = None,
    destination: TandoorClient | None = None,
    exporter: Any | None = None,
) -> RunSummary:
    store = StateStore(settings.state_path, settings.kptncook_account_id, settings.destination_identity)
    summary = RunSummary()
    with locked_state(settings.state_path):
        store.initialize()
        own_source = source is None
        own_destination = destination is None
        source = source or KptnCookSource(settings)
        destination = destination or TandoorClient(settings)
        try:
            favorites = source.fetch_all()
            summary.favorites = len(favorites)
            if exporter is None:
                try:
                    from kptncook.tandoor import TandoorExporter
                    exporter = TandoorExporter()
                except ImportError as exc:
                    raise SourceError("Installed kptncook release lacks its Tandoor converter") from exc
            for favorite in favorites:
                status = store.get_status(favorite.oid)
                if status is not None:
                    summary.already_handled += 1
                    if status in {"uncertain", "in_flight"}:
                        summary.uncertain += 1
                        logger.error("Source recipe %s is blocked for manual reconciliation", favorite.oid)
                    continue
                try:
                    payload = destination.build_payload(exporter, favorite.recipe, settings.tandoor_space_id)
                    destination.ensure_no_secrets(
                        payload,
                        (settings.kptncook_api_key, settings.kptncook_access_token),
                    )
                except Exception as exc:
                    summary.failed += 1
                    detail = str(exc) if isinstance(exc, TandoorError) else type(exc).__name__
                    logger.error("Source recipe %s: conversion failed (%s)", favorite.oid, detail)
                    continue
                image = _cover_bytes(exporter, favorite.recipe)
                store.begin_create(favorite.oid)
                try:
                    destination_id = destination.create_recipe(payload)
                except Exception as exc:
                    try:
                        store.mark_uncertain(favorite.oid)
                    except Exception:
                        logger.exception("Could not persist uncertain state for source recipe %s", favorite.oid)
                    summary.uncertain += 1
                    detail = str(exc) if isinstance(exc, TandoorError) else type(exc).__name__
                    logger.error("Source recipe %s: %s", favorite.oid, detail)
                    continue
                # Persist the tombstone before the nonessential image request.
                store.mark_imported(favorite.oid, destination_id)
                summary.imported += 1
                if image is not None:
                    try:
                        destination.upload_cover(destination_id, image)
                    except Exception as exc:
                        summary.image_warnings += 1
                        detail = str(exc) if isinstance(exc, TandoorError) else type(exc).__name__
                        logger.warning("Source recipe %s: %s", favorite.oid, detail)
        finally:
            if own_source:
                source.close()
            if own_destination:
                destination.close()
    logger.info(
        "Sync complete: favorites=%d imported=%d already_handled=%d uncertain=%d image_warnings=%d failed=%d",
        summary.favorites,
        summary.imported,
        summary.already_handled,
        summary.uncertain,
        summary.image_warnings,
        summary.failed,
    )
    return summary

