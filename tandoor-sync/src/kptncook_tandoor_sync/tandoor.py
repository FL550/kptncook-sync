from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from .config import Settings

logger = logging.getLogger(__name__)
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")


class TandoorError(RuntimeError):
    pass


class TandoorClient:
    def __init__(self, settings: Settings):
        import httpx

        self.settings = settings
        self.httpx = httpx
        self.client = httpx.Client(
            base_url=settings.tandoor_url + "/",
            headers={
                "Authorization": f"Bearer {settings.tandoor_token}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(settings.request_timeout_seconds, connect=10),
            follow_redirects=False,
        )

    @staticmethod
    def build_payload(exporter: Any, recipe: object, space_id: int) -> dict[str, Any]:
        try:
            payload = dict(exporter.get_recipe_payload(recipe))
        except Exception as exc:
            raise TandoorError(f"Recipe conversion failed ({type(exc).__name__})") from None
        # The ZIP importer accepts a top-level ingredient list; the API does not.
        # API ingredients live on steps, so retaining both forms would duplicate them.
        grouped_ingredients = payload.pop("ingredients", [])
        if not isinstance(grouped_ingredients, list):
            raise TandoorError("Converted recipe contains an invalid ingredient list")
        _annotate_groups(payload.get("steps"), grouped_ingredients)
        payload["cookbook"] = space_id
        payload = _drop_none(payload)
        if not isinstance(payload.get("name"), str) or not payload["name"].strip():
            raise TandoorError("Converted recipe has no title")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise TandoorError("Converted recipe has no instructions")
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("instruction"), str):
                raise TandoorError("Converted recipe contains an invalid instruction")
            ingredients = step.get("ingredients", [])
            if not isinstance(ingredients, list):
                raise TandoorError("Converted recipe contains invalid step ingredients")
            for ingredient in ingredients:
                if not isinstance(ingredient, dict) or not isinstance(ingredient.get("food"), dict):
                    raise TandoorError("Converted recipe contains an invalid ingredient")
        return payload

    @staticmethod
    def ensure_no_secrets(payload: dict[str, Any], secrets: tuple[str, ...]) -> None:
        for value in _strings(payload):
            if any(secret and secret in value for secret in secrets):
                raise TandoorError("Converted recipe contains a configured credential")
            for match in re.findall(r"https?://[^\s\"'<>]+", value, re.IGNORECASE):
                parsed = urlsplit(match.rstrip(".,);]"))
                if parsed.username or parsed.password or parsed.query:
                    raise TandoorError("Converted recipe contains a credential-bearing URL")

    def create_recipe(self, payload: dict[str, Any]) -> int:
        try:
            response = self.client.post("api/recipe/", json=payload)
        except Exception:
            raise TandoorError("Recipe create request failed; remote outcome is uncertain") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise TandoorError(
                f"Recipe create returned HTTP {response.status_code}; remote outcome is uncertain"
            )
        try:
            result = response.json()
            recipe_id = result.get("id") if isinstance(result, dict) else None
            if isinstance(recipe_id, int) and not isinstance(recipe_id, bool):
                if recipe_id > 0:
                    return recipe_id
            elif isinstance(recipe_id, str) and re.fullmatch(r"[1-9][0-9]*", recipe_id):
                return int(recipe_id)
        except (ValueError, TypeError):
            pass
        raise TandoorError("Recipe create returned no validated ID; remote outcome is uncertain") from None

    def upload_cover(self, recipe_id: int, image: bytes) -> None:
        if not _SAFE_SEGMENT.fullmatch(str(recipe_id)):
            raise ValueError("Invalid destination recipe ID")
        try:
            response = self.client.post(
                f"api/recipe/{recipe_id}/image/",
                files={"image": ("cover.jpg", image, "image/jpeg")},
            )
        except Exception:
            raise TandoorError("Cover upload failed") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise TandoorError(f"Cover upload returned HTTP {response.status_code}")

    def close(self) -> None:
        self.client.close()


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(child) for key, child in value.items() if child is not None}
    if isinstance(value, list):
        return [_drop_none(child) for child in value if child is not None]
    return value


def _strings(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def _annotate_groups(steps: Any, entries: list[object]) -> None:
    if not isinstance(steps, list):
        return
    active_group: str | None = None
    names_to_groups: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise TandoorError("Converted recipe contains an invalid ingredient entry")
        if entry.get("is_header"):
            label = entry.get("note")
            active_group = label.strip() if isinstance(label, str) and label.strip() else None
            continue
        food = entry.get("food")
        name = food.get("name") if isinstance(food, dict) else entry.get("name")
        if active_group and isinstance(name, str) and name.strip():
            names_to_groups.setdefault(name.strip().casefold(), set()).add(active_group)
    for step in steps:
        if not isinstance(step, dict):
            continue
        for ingredient in step.get("ingredients", []):
            if not isinstance(ingredient, dict):
                continue
            food = ingredient.get("food")
            name = food.get("name") if isinstance(food, dict) else None
            groups = names_to_groups.get(name.strip().casefold(), set()) if isinstance(name, str) else set()
            # Only annotate an unambiguous match; never guess across same-name groups.
            if len(groups) == 1 and not ingredient.get("note"):
                ingredient["note"] = next(iter(groups))

