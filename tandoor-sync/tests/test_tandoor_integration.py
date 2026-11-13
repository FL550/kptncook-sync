from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from kptncook_tandoor_sync.config import Settings
from kptncook_tandoor_sync.tandoor import TandoorClient


CONFIRMATION = "I_ACCEPT_DISPOSABLE_TANDOOR_DATA"


def test_tandoor_create_nested_ingredients_image_and_delete() -> None:
    if os.environ.get("TANDOOR_INTEGRATION_CONFIRM") != CONFIRMATION:
        pytest.skip("set explicit disposable-instance confirmation to run")

    url = os.environ["TANDOOR_TEST_URL"]
    token = os.environ["TANDOOR_TEST_TOKEN"]
    space_id = int(os.environ["TANDOOR_TEST_SPACE_ID"])
    image_path = Path(os.environ["TANDOOR_TEST_IMAGE"])
    image = image_path.read_bytes()
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" and not (
        parsed_url.scheme == "http"
        and parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        pytest.fail("TANDOOR_TEST_URL must use HTTPS except for localhost")
    if (
        parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        pytest.fail("TANDOOR_TEST_URL must not contain credentials or query values")
    if not token or space_id < 1:
        pytest.fail(
            "TANDOOR_TEST_TOKEN and a positive TANDOOR_TEST_SPACE_ID are required"
        )
    if image_path.suffix.lower() not in {".jpg", ".jpeg"} or not image.startswith(
        b"\xff\xd8"
    ):
        pytest.fail("TANDOOR_TEST_IMAGE must point to a JPEG file")

    settings = Settings(
        kptncook_api_key="integration-test-key",
        kptncook_access_token="integration-test-token",
        kptncook_account_id="integration-test-account",
        kptncook_api_url="https://mobile.kptncook.com",
        kptncook_favorites_complete=False,
        kptncook_lang="en",
        kptncook_store="us",
        tandoor_url=url,
        tandoor_token=token,
        tandoor_space_id=space_id,
        interval_seconds=86400,
        state_path=Path("/tmp/kptncook-tandoor-integration.sqlite3"),
        request_timeout_seconds=20,
    )
    client = TandoorClient(settings)
    recipe_id: int | None = None
    try:
        name = f"Temporary KptnCook sync contract {uuid.uuid4()}"
        payload = {
            "name": name,
            "description": "Temporary integration test recipe; safe to delete.",
            "source_url": "https://share.kptncook.com/integration-test",
            "servings": 2,
            "working_time": 1,
            "keywords": [{"name": "integration-test"}],
            "cookbook": space_id,
            "steps": [
                {
                    "instruction": "Verify nested API ingredients.",
                    "ingredients": [
                        {
                            "amount": 1,
                            "unit": {"name": "g"},
                            "food": {"name": "integration-test ingredient"},
                        }
                    ],
                }
            ],
        }
        recipe_id = client.create_recipe(payload)
        client.upload_cover(recipe_id, image)

        response = client.client.get(f"api/recipe/{recipe_id}/")
        assert response.status_code == 200, "recipe read permission is required"
        persisted = response.json()
        stored_space = persisted["cookbook"]
        if isinstance(stored_space, dict):
            stored_space = stored_space["id"]
        assert int(stored_space) == space_id
        [step] = persisted["steps"]
        [ingredient] = step["ingredients"]
        assert ingredient["food"]["name"] == "integration-test ingredient"

        deleted_id = recipe_id
        deleted = client.client.delete(f"api/recipe/{deleted_id}/")
        assert deleted.status_code in {200, 202, 204}, (
            "delete permission is required to clean the disposable test recipe"
        )
        assert client.client.get(f"api/recipe/{deleted_id}/").status_code == 404
        recipe_id = None
    finally:
        try:
            if recipe_id is not None:
                deleted = client.client.delete(f"api/recipe/{recipe_id}/")
                if deleted.status_code not in {200, 202, 204, 404}:
                    pytest.fail(
                        "failed to clean up temporary recipe from disposable Tandoor"
                    )
        finally:
            client.close()
