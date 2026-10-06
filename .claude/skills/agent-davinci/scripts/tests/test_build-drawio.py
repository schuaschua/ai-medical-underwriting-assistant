#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for build-drawio.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_build-drawio.py
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch_fixtures import *  # noqa: E402,F403


class BuildTests(Project):
    def test_builds_valid_stamped_drawio(self):
        code, out = self.build(containers_model())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["cited_ads"], ["AD-1", "AD-2", "AD-3"])
        self.assertTrue(out["page"]["readable"], out["page"])
        self.assertIn("skipped", out["exports"])
        root = ET.parse(self.folder / "c4-containers.drawio").getroot()
        cells = {el.get("id"): el for el in root.iter() if el.get("id")}
        self.assertEqual(cells["0"].get("arch_type"), "c4-containers")
        self.assertEqual(cells["0"].get("spine"), SPINE_REL)
        self.assertIn("AD-2", json.loads(cells["0"].get("ad_hashes")))
        self.assertIn("_bmad-output/specs/SPEC.md", json.loads(cells["0"].get("source_hashes")))
        for cid in ("n-api", "n-db", "g-sys", "e-1", "e-2", "e-3"):
            self.assertTrue(cells[cid].get("cite"), cid)
        api = cells["n-api"].find("mxCell")
        self.assertEqual(api.get("parent"), "g-sys")  # nested in its boundary
        self.assertIn("No AD states the Azure region.", cells["notes"].get("label"))
        ids = [el.get("id") for el in root.iter() if el.tag in ("object", "mxCell") and el.get("id")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_nodes_in_a_layer_do_not_overlap(self):
        self.build(containers_model())
        root = ET.parse(self.folder / "c4-containers.drawio").getroot()
        boxes = []
        for obj in root.iter("object"):
            if obj.get("id", "").startswith("n-") and obj.find("mxCell").get("parent") == "g-sys":
                g = obj.find("mxCell/mxGeometry")
                boxes.append(tuple(float(g.get(k)) for k in ("x", "y", "width", "height")))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                self.assertFalse(overlap, (a, b))

    def test_refuses_uncited_and_unknown_cites(self):
        model = containers_model()
        model["nodes"][1]["cite"] = []
        model["nodes"][2]["cite"] = "AD-9"
        model["edges"][0]["cite"] = "README.md §1"
        code, out = self.build(model)
        self.assertEqual(code, 1)
        for text in ("node api: no cite", "cites AD-9, which the spine does not define", "'README.md §1' is neither"):
            self.assertIn(text, out["message"])
        self.assertFalse((self.folder / "c4-containers.drawio").exists())

    def test_refuses_sources_outside_bmad_output(self):
        (self.root / "src").mkdir()
        (self.root / "src/app.cs").write_text("//", encoding="utf-8")
        code, out = self.build(containers_model(sources=["src/app.cs"]))
        self.assertEqual(code, 1)
        self.assertIn("outside _bmad-output/", out["message"])

    def test_kinds_and_tags_are_checked(self):
        model = containers_model()
        model["nodes"][1]["kind"] = "process"
        model["edges"][1]["data"] = ["PCI"]
        code, out = self.build(model)
        self.assertEqual(code, 1)
        self.assertIn("kind must be one of", out["message"])
        self.assertIn("data tag 'PCI'", out["message"])

    def test_existing_file_needs_force(self):
        self.assertEqual(self.build(containers_model())[0], 0)
        code, out = self.build(containers_model())
        self.assertEqual(code, 1)
        self.assertIn("--force", out["message"])
        self.assertEqual(self.build(containers_model(), "c4-containers", "--force")[0], 0)

    def test_every_type_builds(self):
        dfd = {
            "type": "data-flow", "title": "Chat data", "spine": SPINE_REL,
            "groups": [{"id": "tenant", "label": "Org Azure tenant", "kind": "trust_boundary", "cite": "AD-1"}],
            "nodes": [{"id": "c", "label": "Customer", "kind": "person", "cite": "AD-1"},
                      {"id": "api", "label": "Chat API", "kind": "process", "group": "tenant", "cite": "AD-1"},
                      {"id": "db", "label": "Conversations", "kind": "store", "group": "tenant", "data": ["PII"], "cite": "AD-2"}],
            "edges": [{"from": "c", "to": "api", "label": "message", "cite": "AD-1"},
                      {"from": "api", "to": "db", "label": "conversation", "data": ["PII"], "cite": "AD-2"}],
        }
        dep = {
            "type": "deployment", "title": "Hosting", "spine": SPINE_REL,
            "groups": [{"id": "sub", "label": "Subscription", "kind": "subscription", "cite": "AD-1"},
                       {"id": "rg", "label": "rg-chat", "kind": "resource_group", "parent": "sub", "cite": "AD-1"}],
            "nodes": [{"id": "app", "label": "App Service", "kind": "service", "group": "rg", "cite": "AD-1",
                       "icon": "app_services/App_Services"},
                      {"id": "cosmos", "label": "Cosmos DB", "kind": "service", "group": "rg", "cite": "AD-2",
                       "icon": "databases/Azure_Cosmos_DB"}],
            "edges": [{"from": "app", "to": "cosmos", "label": "data", "cite": "AD-2"}],
        }
        ctx = {"type": "c4-context", "title": "Chat", "spine": SPINE_REL,
               "nodes": [{"id": "c", "label": "Customer", "kind": "person", "cite": "AD-1"},
                         {"id": "s", "label": "Chat service", "kind": "system", "cite": "AD-1"},
                         {"id": "o", "label": "Azure OpenAI", "kind": "system_ext", "cite": "AD-3"}],
               "edges": [{"from": "c", "to": "s", "label": "asks", "cite": "AD-1"},
                         {"from": "s", "to": "o", "label": "asks", "cite": "AD-3"}]}
        seq = {"type": "sequence", "title": "One chat turn", "spine": SPINE_REL,
               "participants": [{"id": "c", "label": "Customer", "kind": "person", "cite": "AD-1"},
                                {"id": "api", "label": "Chat API", "kind": "container", "cite": "AD-1"},
                                {"id": "o", "label": "Azure OpenAI", "kind": "external", "cite": "AD-3"}],
               "messages": [{"from": "c", "to": "api", "label": "question", "cite": "AD-1"},
                            {"from": "api", "to": "api", "label": "load history", "cite": "AD-2"},
                            {"from": "api", "to": "o", "label": "prompt", "cite": "AD-3"},
                            {"from": "o", "to": "api", "label": "answer", "return": True, "cite": "AD-3"}]}
        for name, model in (("data-flow", dfd), ("deployment", dep), ("c4-context", ctx), ("sequence-chat-turn", seq)):
            code, out = self.build(model, name)
            self.assertEqual(code, 0, (name, out))
            ET.parse(self.folder / f"{name}.drawio")
        rg = next(o for o in ET.parse(self.folder / "deployment.drawio").getroot().iter("object") if o.get("id") == "g-rg")
        self.assertEqual(rg.find("mxCell").get("parent"), "g-sub")
        cosmos = next(o for o in ET.parse(self.folder / "deployment.drawio").getroot().iter("object") if o.get("id") == "n-cosmos")
        self.assertIn("image=img/lib/azure2/databases/Azure_Cosmos_DB.svg", cosmos.find("mxCell").get("style"))

    def test_group_icons_kubernetes_icons_and_enclosed_regions(self):
        region = lambda r: [  # noqa: E731
            {"id": f"reg{r}", "label": f"Region {r}", "kind": "region", "parent": "azure", "cite": "AD-1"},
            {"id": f"aks{r}", "label": "AKS cluster", "kind": "boundary", "parent": f"reg{r}", "icon": "compute/Kubernetes_Services", "cite": "AD-1"}]
        model = {"type": "deployment", "title": "Two regions", "spine": SPINE_REL,
                 "groups": [{"id": "azure", "label": "Azure", "kind": "cloud", "cite": "AD-1"}] + region(1) + region(2),
                 "nodes": [{"id": f"web{r}", "label": "Front-end", "tech": "React", "kind": "service", "group": f"aks{r}",
                            "icon": "kubernetes:pod", "cite": "AD-1"} for r in (1, 2)]}
        code, out = self.build(model, "deployment")
        self.assertEqual(code, 0, out)
        cells = {o.get("id"): o for o in ET.parse(self.folder / "deployment.drawio").getroot().iter("object")}
        self.assertIn("Kubernetes_Services.svg", cells["g-aks1-icon"].find("mxCell").get("style"))
        self.assertEqual(cells["g-aks1-icon"].find("mxCell").get("parent"), "g-aks1")
        self.assertIn("prIcon=pod", cells["n-web2"].find("mxCell").get("style"))
        self.assertEqual(cells["g-reg2"].find("mxCell").get("parent"), "g-azure")

        for g in model["groups"]:
            if g["kind"] == "region":
                g.pop("parent")
        model["groups"] = model["groups"][1:]
        code, out = self.build(model, "deployment", "--force")
        self.assertEqual(code, 1)
        self.assertIn("put every region group inside one enclosing group", out["message"])

    def test_page_defaults_to_a2_landscape_and_follows_the_model(self):
        code, out = self.build(containers_model())
        self.assertEqual((out["page"]["size"], out["page"]["orientation"], out["page"]["pages"]), ("A2", "landscape", 1))
        graph = ET.parse(self.folder / "c4-containers.drawio").getroot().find("diagram/mxGraphModel")
        self.assertEqual((graph.get("pageWidth"), graph.get("pageHeight")), ("2339", "1654"))
        code, out = self.build(containers_model(page={"size": "A3", "orientation": "landscape"}), "c4-containers", "--force")
        self.assertEqual((out["page"]["size"], out["page"]["orientation"]), ("A3", "landscape"))
        code, out = self.build(containers_model(page={"size": "B5"}), "c4-containers", "--force")
        self.assertEqual(code, 1)
        self.assertIn("page must be", out["message"])

    def crowded_model(self) -> dict:
        long = "a deliberately long label that has to wrap over several lines to fit"
        return {"type": "deployment", "title": "Crowded", "spine": SPINE_REL,
                "groups": [{"id": "azure", "label": "Azure with a rather long header label", "kind": "cloud", "cite": "AD-1"},
                           {"id": "rg", "label": "Resource group with its own long header", "kind": "resource_group",
                            "parent": "azure", "icon": "general/Resource_Groups", "cite": "AD-1"}],
                "nodes": [{"id": "u", "label": "Customer", "kind": "person", "cite": "AD-1"},
                          {"id": "a", "label": "App", "description": long, "kind": "service", "group": "rg",
                           "icon": "app_services/App_Services", "cite": ["AD-1", "AD-2"]},
                          {"id": "b", "label": "Queue worker " + long, "kind": "service", "group": "rg", "cite": "AD-1"},
                          {"id": "c", "label": "Cosmos DB", "kind": "service", "group": "rg", "icon": "databases/Azure_Cosmos_DB",
                           "cite": "AD-2", "layer": 3},
                          {"id": "d", "label": "OpenAI", "kind": "service", "icon": "ai_machine_learning/Azure_OpenAI", "cite": "AD-3"}],
                "edges": [{"from": "u", "to": "a", "label": long, "cite": "AD-1"},
                          {"from": "a", "to": "b", "label": "hands work to " + long, "cite": "AD-1"},
                          {"from": "a", "to": "c", "label": "reads and writes " + long, "cite": "AD-2"},
                          {"from": "a", "to": "d", "label": "asks " + long, "cite": "AD-3"},
                          {"from": "u", "to": "c", "label": "a long jump over two rows " + long, "cite": "AD-2"}]}

    def test_crowded_diagram_builds_without_overlaps(self):
        code, out = self.build(self.crowded_model(), "deployment")
        self.assertEqual(code, 0, out)
        root = ET.parse(self.folder / "deployment.drawio").getroot()
        cells = {o.get("id"): o.find("mxCell") for o in root.iter("object")}
        self.assertIn("labelWidth=", cells["n-a"].get("style"))          # icon labels wrap where they were measured
        for i in range(1, 6):
            self.assertIn("edgeStyle=none", cells[f"e-{i}"].get("style"))  # arrows follow the routed lanes
            self.assertIsNotNone(cells[f"e-{i}"].find("mxGeometry/Array"))

    def module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_drawio", SCRIPTS / "build-drawio.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_overlap_check_catches_a_collision(self):
        mod = self.module()
        model = self.crowded_model()
        mod.validate(model, self.root)
        plan = mod.plan_graph(model, 0)
        self.assertEqual(mod.overlaps(model, plan), [])
        a, b = plan["vis"]["a"], plan["vis"]["b"]
        a.x, a.y = b.x + 5, b.y + 5  # plant one icon on the box beside it
        self.assertTrue(any("node a overlaps node b" in p or "node b overlaps node a" in p for p in mod.overlaps(model, plan)))

    def test_arrows_share_lanes_to_save_space(self):
        mod = self.module()
        fan = lambda src, to: [{"id": src, "label": src, "kind": "container", "cite": "AD-1"}] + [  # noqa: E731
            {"id": t, "label": t, "kind": "container", "cite": "AD-1"} for t in to]
        model = {"type": "c4-containers", "title": "Two fans", "spine": SPINE_REL,
                 "nodes": fan("a", ["a1", "a2"]) + fan("b", ["b1", "b2"]),
                 "edges": [{"from": s, "to": f"{s}{k}", "label": "calls", "cite": "AD-1"} for s in "ab" for k in (1, 2)]}
        mod.validate(model, self.root)
        own = mod.plan_graph(model, 0)
        apart = mod.plan_graph(model, 0, mod.share_lanes(own, False))
        over = mod.plan_graph(model, 0, mod.share_lanes(own, True))
        self.assertEqual(set(mod.share_lanes(own, False).values()), {0})  # four arrows, clear of each other, in one lane
        self.assertLess(mod.extent(apart)[1], mod.extent(own)[1])
        self.assertLessEqual(mod.extent(over)[1], mod.extent(apart)[1])
        for plan in (apart, over):  # lines may cross or lie over each other; words stay clear
            self.assertEqual(mod.overlaps(model, plan), [])

    def test_web_icon_is_kept_beside_the_model_and_embedded(self):
        mod = self.module()
        url = "https://example.com/logos/Snowflake.svg"
        model = {"type": "deployment", "title": "Hosting", "spine": SPINE_REL,
                 "nodes": [{"id": "dw", "label": "Snowflake", "kind": "service", "icon": url, "cite": "AD-2"}]}
        (self.folder / "icons").mkdir()  # already fetched: the build needs no network
        (self.folder / "icons" / f"{mod.icon_file(url)}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 24"><rect width="48" height="24"/></svg>', encoding="utf-8")
        code, out = self.build(model, "deployment")
        self.assertEqual(code, 0, out)
        dw = next(o for o in ET.parse(self.folder / "deployment.drawio").getroot().iter("object") if o.get("id") == "n-dw")
        self.assertIn("image=data:image/svg+xml,PHN2Zy", dw.find("mxCell").get("style"))
        geo = dw.find("mxCell/mxGeometry")
        self.assertEqual(float(geo.get("width")), 2 * float(geo.get("height")))  # the icon keeps its shape
        model["nodes"][0]["icon"] = "http://example.com/logo.svg"
        code, out = self.build(model, "deployment", "--force")
        self.assertEqual(code, 1)
        self.assertIn("is not an https:// address", out["message"])
        (self.folder / "icons" / f"{mod.icon_file(url)}.svg").write_text("<html>not found</html>", encoding="utf-8")
        model["nodes"][0]["icon"] = url
        code, out = self.build(model, "deployment", "--force")
        self.assertIn("is neither an SVG nor a PNG", out["message"])

    def test_cited_sections_must_exist(self):
        model = containers_model()
        model["nodes"][0]["cite"] = "SPEC.md §9.9"
        model["edges"][0]["cite"] = 'SPEC.md "Users"'  # a heading title is fine
        code, out = self.build(model)
        self.assertEqual(code, 1)
        self.assertIn("cite 'SPEC.md §9.9' names no section of SPEC.md", out["message"])
        self.assertIn("2 Users", out["message"])
        self.assertNotIn("SPEC.md \"Users\"", out["message"])

    def test_data_services_stay_out_of_compute_clusters(self):
        base = {"type": "deployment", "title": "Hosting", "spine": SPINE_REL,
                "groups": [{"id": "vnet", "label": "Virtual network", "kind": "vnet", "cite": "AD-1"},
                           {"id": "sn", "label": "Subnet", "kind": "subnet", "parent": "vnet", "cite": "AD-1"},
                           {"id": "aks", "label": "AKS cluster", "kind": "boundary", "parent": "sn",
                            "icon": "compute/Kubernetes_Services", "cite": "AD-1"}],
                "nodes": [{"id": "web", "label": "Front-end", "kind": "service", "group": "aks", "icon": "kubernetes:pod", "cite": "AD-1"},
                          {"id": "db", "label": "Cosmos DB", "kind": "service", "group": "aks",
                           "icon": "databases/Azure_Cosmos_DB", "cite": "AD-2"}]}
        code, out = self.build(base, "deployment")
        self.assertEqual(code, 1)
        self.assertIn("sits inside the compute cluster 'aks'", out["message"])
        base["nodes"][1]["group"] = "sn"
        code, out = self.build(base, "deployment", "--force")
        self.assertIn("shares subnet 'sn' with a compute cluster", out["message"])
        base["groups"].append({"id": "sndb", "label": "Data subnet", "kind": "subnet", "parent": "vnet", "cite": "AD-2"})
        base["nodes"][1]["group"] = "sndb"
        code, out = self.build(base, "deployment", "--force")
        self.assertEqual(code, 0, out)

    def test_sequence_participants_ordered_by_first_appearance(self):
        seq = {"type": "sequence", "title": "Turn", "spine": SPINE_REL,
               "participants": [{"id": "o", "label": "OpenAI", "kind": "external", "cite": "AD-3"},
                                {"id": "c", "label": "Customer", "kind": "person", "cite": "AD-1"},
                                {"id": "api", "label": "Chat API", "kind": "container", "cite": "AD-1"}],
               "messages": [{"from": "c", "to": "api", "label": "question", "cite": "AD-1"},
                            {"from": "api", "to": "o", "label": "prompt", "cite": "AD-3"}]}
        self.assertEqual(self.build(seq, "sequence-turn")[0], 0)
        xs = {o.get("id"): float(o.find("mxCell/mxGeometry").get("x"))
              for o in ET.parse(self.folder / "sequence-turn.drawio").getroot().iter("object") if o.get("id", "").startswith("p-")}
        self.assertLess(xs["p-c"], xs["p-api"])
        self.assertLess(xs["p-api"], xs["p-o"])

    def test_unknown_icon_or_provider_is_refused_with_suggestions(self):
        model = {"type": "deployment", "title": "Hosting", "spine": SPINE_REL,
                 "nodes": [{"id": "db", "label": "Cosmos", "kind": "service", "icon": "databases/Cosmos", "cite": "AD-2"}]}
        code, out = self.build(model, "deployment")
        self.assertEqual(code, 1)
        self.assertIn("databases/Azure_Cosmos_DB", out["message"])
        code, out = self.build({**model, "provider": "oracle"}, "deployment")
        self.assertEqual(code, 1)
        self.assertIn("'oracle' has no stencil catalogue", out["message"])


if __name__ == "__main__":
    unittest.main()
