from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kptncook_tandoor_sync.config import ConfigurationError, Settings
from kptncook_tandoor_sync.source import KptnCookSource, SourceError
from kptncook_tandoor_sync.state import StateError, StateStore
from kptncook_tandoor_sync.sync import sync_once
from kptncook_tandoor_sync.tandoor import TandoorClient, TandoorError


def settings(path: Path) -> Settings:
    return Settings(
        kptncook_api_key="source-key",
        kptncook_access_token="source-token",
        kptncook_account_id="test-account",
        kptncook_api_url="https://source.example",
        kptncook_favorites_complete=True,
        kptncook_lang="en",
        kptncook_store="us",
        tandoor_url="https://tandoor.example",
        tandoor_token="destination-token",
        tandoor_space_id=7,
        interval_seconds=86400,
        state_path=path,
        request_timeout_seconds=10,
    )


class FakeRecipeModel:
    @staticmethod
    def model_validate(value):
        if not isinstance(value, dict) or not value.get("id", {}).get("oid"):
            raise ValueError("invalid")
        return SimpleNamespace(
            id=SimpleNamespace(oid=value["id"]["oid"]),
            uid=value.get("uid"),
            data=value,
        )


class FakeSourceClient:
    def __init__(self, summaries, recipes):
        self.summaries = summaries
        self.recipes = recipes

    def get_favorites(self):
        return self.summaries

    def resolve_recipe_summaries(self, _summaries):
        return self.recipes


class FakeSource:
    def __init__(self, favorites):
        self.favorites = favorites

    def fetch_all(self):
        return self.favorites


class FakeExporter:
    def get_recipe_payload(self, _recipe):
        return {
            "name": "Soup",
            "description": "",
            "source_url": "https://share.kptncook.com/uid-1",
            "servings": 2,
            "steps": [
                {
                    "instruction": "Cook",
                    "ingredients": [{"food": {"name": "carrot"}, "amount": 1}],
                }
            ],
            "ingredients": [{"food": {"name": "carrot"}, "amount": 1}],
            "keywords": [],
        }

    def get_cover_image_bytes(self, _recipe):
        return None


class FakeDestination:
    build_payload = staticmethod(TandoorClient.build_payload)
    ensure_no_secrets = staticmethod(TandoorClient.ensure_no_secrets)

    def __init__(self, fail=False):
        self.fail = fail
        self.created = []

    def create_recipe(self, payload):
        self.created.append(payload)
        if self.fail:
            raise TandoorError("remote outcome is uncertain")
        return 42

    def upload_cover(self, _recipe_id, _image):
        raise AssertionError("no image expected")


class SourceTests(unittest.TestCase):
    def source(self, summaries, recipes):
        source = object.__new__(KptnCookSource)
        source.client = FakeSourceClient(summaries, recipes)
        source.recipe_model = FakeRecipeModel
        source.complete_inventory = True
        return source

    def test_uid_aliases_resolve_and_deduplicate_by_canonical_oid(self):
        full = {"id": {"oid": "oid-1"}, "uid": "uid-1"}
        source = self.source(
            [{"uid": "uid-1"}, {"identifier": "oid-1"}],
            [full],
        )
        result = source.fetch_all()
        self.assertEqual([(item.oid, item.recipe.uid) for item in result], [("oid-1", "uid-1")])

    def test_unresolved_favorite_fails_closed(self):
        source = self.source([{"uid": "missing"}], [])
        with self.assertRaisesRegex(SourceError, "unresolved or partial"):
            source.fetch_all()

    def test_empty_favorites_is_successful_noop(self):
        self.assertEqual(self.source([], []).fetch_all(), [])

    def test_non_list_inventory_is_not_treated_as_empty(self):
        source = self.source({"results": []}, [])
        with self.assertRaisesRegex(SourceError, "not a complete list"):
            source.fetch_all()

    def test_inventory_must_be_confirmed_complete_before_source_request(self):
        source = self.source([], [])
        source.complete_inventory = False
        with self.assertRaisesRegex(SourceError, "completeness has not been confirmed"):
            source.fetch_all()


class TandoorPayloadTests(unittest.TestCase):
    def test_zip_ingredient_list_removed_but_nested_step_ingredients_preserved(self):
        recipe = SimpleNamespace()
        payload = TandoorClient.build_payload(FakeExporter(), recipe, 7)
        self.assertNotIn("ingredients", payload)
        self.assertEqual(payload["cookbook"], 7)
        self.assertEqual(payload["steps"][0]["ingredients"][0]["food"]["name"], "carrot")

    def test_group_labels_are_kept_as_step_ingredient_notes(self):
        class GroupExporter(FakeExporter):
            def get_recipe_payload(self, _recipe):
                value = super().get_recipe_payload(_recipe)
                value["ingredients"] = [
                    {"note": "Dressing", "is_header": True, "amount": 0},
                    {"food": {"name": "carrot"}, "amount": 1},
                ]
                return value

        payload = TandoorClient.build_payload(GroupExporter(), SimpleNamespace(), 7)
        self.assertEqual(payload["steps"][0]["ingredients"][0]["note"], "Dressing")

    def test_credential_bearing_urls_and_secrets_are_rejected(self):
        with self.assertRaises(TandoorError):
            TandoorClient.ensure_no_secrets({"source_url": "https://example.test/?token=secret"}, ("secret",))
        with self.assertRaises(TandoorError):
            TandoorClient.ensure_no_secrets({"description": "source-token"}, ("source-token",))


class StateTests(unittest.TestCase):
    def test_imported_history_is_permanent_and_bound_to_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = StateStore(path, "account", "https://tandoor/space:7")
            store.initialize()
            store.begin_create("oid-1")
            store.mark_imported("oid-1", 42)
            self.assertEqual(store.get_status("oid-1"), "imported")
            with self.assertRaises(StateError):
                StateStore(path, "account", "https://other/space:7").initialize()

    def test_uncertain_write_is_not_retryable_until_explicitly_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "history.sqlite3", "account", "destination")
            store.initialize()
            store.begin_create("oid-1")
            store.initialize()
            self.assertEqual(store.get_status("oid-1"), "uncertain")
            store.reconcile_clear("oid-1")
            self.assertIsNone(store.get_status("oid-1"))

    def test_reconciled_import_becomes_tombstone(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "history.sqlite3", "account", "destination")
            store.initialize()
            store.begin_create("oid-1")
            store.mark_uncertain("oid-1")
            store.reconcile_imported("oid-1", 9)
            self.assertEqual(store.get_status("oid-1"), "imported")


class SyncTests(unittest.TestCase):
    def test_successful_import_and_rerun_do_not_create_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory) / "history.sqlite3")
            favorite = SimpleNamespace(oid="oid-1", recipe=SimpleNamespace())
            source = FakeSource([favorite])
            destination = FakeDestination()
            first = sync_once(config, source, destination, FakeExporter())
            second = sync_once(config, source, destination, FakeExporter())
            self.assertEqual(first.imported, 1)
            self.assertEqual(second.already_handled, 1)
            self.assertEqual(len(destination.created), 1)

    def test_uncertain_create_is_not_retried_on_later_run(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory) / "history.sqlite3")
            favorite = SimpleNamespace(oid="oid-1", recipe=SimpleNamespace())
            source = FakeSource([favorite])
            destination = FakeDestination(fail=True)
            first = sync_once(config, source, destination, FakeExporter())
            second = sync_once(config, source, destination, FakeExporter())
            self.assertEqual(first.uncertain, 1)
            self.assertEqual(second.uncertain, 1)
            self.assertEqual(len(destination.created), 1)


class ConfigurationTests(unittest.TestCase):
    def test_unsecured_remote_http_url_is_rejected(self):
        with patch.dict("os.environ", {"TANDOOR_URL": "http://example.test"}):
            with self.assertRaises(ConfigurationError):
                from kptncook_tandoor_sync.config import _base_url
                _base_url("TANDOOR_URL")


if __name__ == "__main__":
    unittest.main()
