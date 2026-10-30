from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xraydo.nodes import NodeService
from xraydo.store import Store


class NodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.nodes = NodeService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_id_is_stable_across_rename_and_country_override(self) -> None:
        created = self.nodes.create("Germany Frankfurt", alias="de-1")
        self.assertEqual(created["country"], "DE")
        self.assertEqual(created["remark"], "🇩🇪 Germany Frankfurt")
        node_id = created["id"]

        renamed = self.nodes.rename("de-1", "Turkey Istanbul", "TR")
        self.assertEqual(renamed["id"], node_id)
        self.assertEqual(renamed["country_mode"], "manual")
        self.assertEqual(renamed["remark"], "🇹🇷 Turkey Istanbul")

    def test_duplicate_names_require_id_or_alias(self) -> None:
        first = self.nodes.create("edge", alias="edge-a")
        second = self.nodes.create("edge", alias="edge-b")
        with self.assertRaisesRegex(ValueError, "node_ambiguous"):
            self.nodes.find("edge")
        self.assertEqual(self.nodes.find(first["id"])["alias"], "edge-a")
        self.assertEqual(self.nodes.find("edge-b")["id"], second["id"])

    def test_alias_is_unique_and_validated(self) -> None:
        self.nodes.create("first", alias="edge-1")
        with self.assertRaisesRegex(ValueError, "duplicate_node_alias"):
            self.nodes.create("second", alias="EDGE-1")
        with self.assertRaisesRegex(ValueError, "invalid_node_alias"):
            self.nodes.create("third", alias="not valid")

    def test_ambiguous_unknown_none_and_global_disable_preserve_name(self) -> None:
        ambiguous = self.nodes.create("Congo-01")
        self.assertIsNone(ambiguous["country"])
        self.assertEqual(ambiguous["country_candidates"], ["CD", "CG"])
        self.assertEqual(ambiguous["remark"], "Congo-01")

        unknown = self.nodes.create("private-edge")
        self.assertEqual(unknown["remark"], "private-edge")

        data = self.store.load()
        data["settings"]["country_auto_flags"] = False
        self.store.save(data)
        disabled = self.nodes.create("Germany Berlin")
        self.assertEqual(disabled["country_mode"], "none")
        self.assertEqual(disabled["remark"], "Germany Berlin")

    def test_per_node_none_and_manual_remark_do_not_duplicate_flag(self) -> None:
        node = self.nodes.create("Germany", remark="🇩🇪 Premium")
        self.assertEqual(node["remark"], "🇩🇪 Premium")
        node = self.nodes.set_remark(node["id"], "🇫🇷 Premium")
        self.assertEqual(node["remark"], "🇩🇪 Premium")
        node = self.nodes.set_country(node["id"], "none")
        self.assertEqual(node["remark"], "🇫🇷 Premium")
