from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FavoriteRecipe:
    oid: str
    recipe: Any


def _field(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _recipe_from_result(item: object) -> object:
    data = _field(item, "data")
    if data is not None:
        return data
    return item


def _recipe_identity(recipe: object) -> tuple[str | None, str | None]:
    recipe_id = _field(recipe, "id")
    oid = _field(recipe_id, "oid")
    if oid is None and isinstance(recipe_id, dict):
        oid = recipe_id.get("oid", recipe_id.get("_id"))
    uid = _field(recipe, "uid")
    oid = str(oid).strip() if oid is not None else None
    uid = str(uid).strip() if uid is not None else None
    return (oid or None, uid or None)


def _summary_aliases(item: object) -> set[str]:
    nested = _field(item, "data")
    if nested is not None:
        item = nested
    result: set[str] = set()

    def collect(value: object, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(value, str):
            if value.strip():
                result.add(value.strip())
            return
        for key in (
            "uid", "identifier", "oid", "_id", "id", "$oid",
            "recipe", "recipeSummary", "recipe_summary", "summary",
        ):
            child = _field(value, key)
            if child is not None:
                collect(child, depth + 1)

    collect(item)
    return result


def _extract_favorite_items(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    raise SourceError("KptnCook favorites response was not a complete list")


def _favorites_method(client_type: type) -> str | None:
    for name in ("list_favorites", "get_favorites"):
        if callable(getattr(client_type, name, None)):
            return name
    return None


class KptnCookSource:
    def __init__(self, settings: Settings):
        try:
            import httpx
            from kptncook.api import KptnCookClient
            from kptncook.models import Recipe
            from kptncook.tandoor import TandoorExporter
        except ImportError as exc:
            raise SourceError("The installed kptncook package lacks required modules") from exc
        favorite_method = _favorites_method(KptnCookClient)
        if favorite_method is None:
            raise SourceError(
                "Installed kptncook release is incompatible; missing favorites-list interface"
            )
        if not callable(getattr(KptnCookClient, "resolve_recipe_summaries", None)):
            raise SourceError(
                "Installed kptncook release is incompatible; missing client interface: "
                "resolve_recipe_summaries"
            )
        exporter_methods = ("get_recipe_payload", "get_cover_image_bytes")
        missing_exporter = [
            name for name in exporter_methods if not callable(getattr(TandoorExporter, name, None))
        ]
        if missing_exporter:
            raise SourceError(
                "Installed kptncook release is incompatible; missing converter interface(s): "
                + ", ".join(missing_exporter)
            )

        request_timeout = httpx.Timeout(settings.request_timeout_seconds)

        class BoundedHttpClient(httpx.Client):
            def request(self, *args: Any, **kwargs: Any) -> Any:
                # The upstream resolver currently sets its own longer timeout.
                kwargs["timeout"] = request_timeout
                return super().request(*args, **kwargs)

        self.recipe_model = Recipe
        self.complete_inventory = settings.kptncook_favorites_complete
        self._client = BoundedHttpClient(
            timeout=request_timeout,
            follow_redirects=False,
        )
        self._favorite_method = favorite_method
        self.client = KptnCookClient(
            base_url=settings.kptncook_api_url,
            api_key=settings.kptncook_api_key,
            access_token=settings.kptncook_access_token,
            client=self._client,
        )

    def fetch_all(self) -> list[FavoriteRecipe]:
        if not self.complete_inventory:
            raise SourceError(
                "KptnCook inventory completeness has not been confirmed; verify "
                "pagination and response limits, then set KPTNCOOK_FAVORITES_COMPLETE=true"
            )
        try:
            raw = getattr(self.client, self._favorite_method)()
            summaries = _extract_favorite_items(raw)
            if not summaries:
                return []
            resolved = self.client.resolve_recipe_summaries(summaries)
        except SourceError:
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                if status in {401, 403}:
                    raise SourceError(
                        f"KptnCook authentication or permission failed (HTTP {status})"
                    ) from None
                raise SourceError(f"KptnCook request failed with HTTP {status}") from None
            if type(exc).__module__.startswith("httpx"):
                raise SourceError("KptnCook request failed or timed out") from None
            raise SourceError(f"KptnCook favorites could not be resolved ({type(exc).__name__})") from None
        if not isinstance(resolved, Iterable) or isinstance(resolved, (str, bytes, dict)):
            raise SourceError("KptnCook recipe resolution returned an invalid response")
        resolved_items = list(resolved)
        by_alias: dict[str, FavoriteRecipe] = {}
        conflicting_aliases: set[str] = set()
        for item in resolved_items:
            raw_recipe = _recipe_from_result(item)
            try:
                recipe = self.recipe_model.model_validate(raw_recipe)
            except Exception:
                continue
            oid, uid = _recipe_identity(recipe)
            if not oid:
                continue
            favorite = FavoriteRecipe(oid, recipe)
            by_alias[oid] = favorite
            if uid:
                previous = by_alias.get(uid)
                if previous is not None and previous.oid != oid:
                    conflicting_aliases.add(uid)
                by_alias[uid] = favorite
        found: dict[str, FavoriteRecipe] = {}
        unresolved: list[int] = []
        for position, summary in enumerate(summaries, start=1):
            aliases = _summary_aliases(summary)
            matches = {by_alias[a].oid: by_alias[a] for a in aliases if a in by_alias}
            if (
                not aliases
                or len(matches) != 1
                or any(alias in conflicting_aliases for alias in aliases)
            ):
                unresolved.append(position)
                continue
            match = next(iter(matches.values()))
            found[match.oid] = match
        if unresolved:
            raise SourceError(
                f"KptnCook returned unresolved or partial favorites at positions "
                f"{', '.join(map(str, unresolved))}; refusing an incomplete inventory"
            )
        return list(found.values())

    def close(self) -> None:
        self._client.close()

