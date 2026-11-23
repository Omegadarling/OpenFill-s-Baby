"""Every control, model and removal method has a tooltip, and both panels show them."""

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import tips, ui  # noqa: E402
from openfill_photocraft.removal_advice import AUTO, SCENE_PLACEHOLDER, TEXTURE_REMOVAL, removal_scenes  # noqa: E402
from openfill_photocraft.removal_plan import AUTO_REMOVAL, FAMILIES  # noqa: E402

from openfill import catalog, remote_models  # noqa: E402

PAGE = Path(tips.__file__).with_name("static") / "index.html"
REMOTE_IDS = [value for name, value in vars(remote_models).items() if name.endswith("_ID") and isinstance(value, str)]


class TipTextTests(unittest.TestCase):
    def test_every_control_tip_is_one_short_sentence_or_two(self):
        for key, text in tips.CONTROLS.items():
            with self.subTest(key):
                self.assertTrue(text.strip())
                self.assertLessEqual(len(text), 330)

    def test_every_model_an_engine_can_offer_lists_pros_and_cons(self):
        for model_id in REMOTE_IDS:
            with self.subTest(model_id):
                text = tips.model_tip(model_id)
                self.assertIn("\nPros: ", text)
                self.assertIn("\nCons: ", text)

    def test_every_downloadable_model_lists_pros_and_cons(self):
        rows = [{"id": entry.id, "template": entry.template, "installed": True}
                for entry in catalog.load_catalog() if entry.template]
        self.assertGreater(len(rows), 10)
        for row in rows:
            with self.subTest(row["id"]):
                text = tips.model_tip(row["id"], rows)
                self.assertIn("\nPros: ", text)
                self.assertIn("\nCons: ", text)

    def test_every_removal_method_lists_pros_and_cons(self):
        rows = [{"id": "lama-big", "installed": True}]
        methods = {method for family in FAMILIES.values() for method in family} | {"default", TEXTURE_REMOVAL, AUTO_REMOVAL}
        for method in methods:
            with self.subTest(method):
                text = tips.model_tip(method, rows)
                self.assertIn("\nPros: ", text)
                self.assertIn("\nCons: ", text)
        self.assertIn("own resolution", tips.model_tip("default", rows))

    def test_a_model_that_can_remove_says_how_it_rated_for_removal(self):
        for model_id in (remote_models.QWEN21_LIGHTNING_ID, remote_models.DEV_ID, remote_models.H3_IMAGE_ID,
                         remote_models.MOSS_ANYPAINT_ID):
            self.assertIn("\nFor Remove: ", tips.model_tip(model_id))
        self.assertIn("not offered for Remove", tips.model_tip(remote_models.QWEN21_FULL_ID))

    def test_an_unknown_model_has_no_tip(self):
        self.assertEqual(tips.model_tip("somebody-elses-checkpoint", [{"id": "somebody-elses-checkpoint"}]), "")

    def test_kind_of_removal_entries_say_what_they_pick(self):
        self.assertIn("two repairs", tips.scene_tip(AUTO))
        self.assertIn("choose the Method yourself", tips.scene_tip(SCENE_PLACEHOLDER))
        scenes = removal_scenes([remote_models.LAMA_ID, remote_models.ROREM_ID], True)
        wire = next(scene for scene in scenes if scene["scene"] == "Wire or repeating pattern")
        self.assertTrue(tips.scene_tip(wire["scene"], wire, "LaMa").startswith("Picks LaMa. "))
        lettering = next(scene for scene in scenes if scene["scene"] == "Covered lettering")
        self.assertTrue(tips.scene_tip(lettering["scene"], lettering).startswith("Picks no method"))


class BrowserViewTests(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_every_key_the_page_asks_for_has_a_tip(self):
        keys = set(re.findall(r'data-tip="([^"$]+)"', self.html))
        self.assertGreater(len(keys), 50)
        self.assertEqual(keys - set(tips.CONTROLS), set())

    def test_the_page_gets_control_tips_once_and_model_tips_with_the_models(self):
        page = tips.page()
        self.assertIs(page["controls"], tips.CONTROLS)
        self.assertEqual(page["manage"], tips.MANAGE)
        self.assertIn('get("/api/tips")', self.html)
        self.assertIn("modelTips=m.tips||{}", self.html)
        rows = [{"id": remote_models.ROREM_ID, "template": "sdxl_eraser"}, {"id": "other", "template": "sdxl_edit"}]
        listed = tips.model_tips(rows)
        self.assertEqual(listed[remote_models.ROREM_ID], tips.MODELS[remote_models.ROREM_ID])
        self.assertEqual(listed["other"], tips.TEMPLATES["sdxl_edit"])
        self.assertIn(TEXTURE_REMOVAL, listed)

    def test_the_go_button_and_dropdowns_are_tipped_for_the_current_mode(self):
        for needle in ('c[mode==="remove"?"method":"model"]', '$("go").title=c["go."+mode]', "TIPS.manage"):
            self.assertIn(needle, self.html)
        for mode in ("select", "fill", "remove", "expand", "txt2img"):
            self.assertIn(f"go.{mode}", tips.CONTROLS)


class ServedTipTests(unittest.TestCase):
    def test_the_companion_serves_the_tips_and_adds_model_tips_to_the_models(self):
        import json
        import urllib.request
        from unittest.mock import Mock

        from openfill_photocraft.web import Api, serve

        api = Api(Mock())
        api.models = lambda mode="edit": {"rows": [{"id": remote_models.ROREM_ID, "template": "sdxl_eraser"}]}
        server, url = serve(api, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base, key = url.split("/?key=")

        def get(path):
            request = urllib.request.Request(base + path, headers={"X-OpenFill-Key": key})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)

        self.assertEqual(get("/api/tips")["controls"], tips.CONTROLS)
        models = get("/api/models?mode=remove")
        self.assertEqual(models["tips"][remote_models.ROREM_ID], tips.MODELS[remote_models.ROREM_ID])
        self.assertEqual(len(models["rows"]), 1)


class NativeTipTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.close)

    def close(self):
        ui._tips(self.root).hide()
        ui._cache.clear()
        ui.FONTS = None
        self.root.destroy()

    def test_a_segment_shows_its_own_tip_and_the_gap_shows_none(self):
        seg = ui.Seg(self.root, (("a", "First"), ("b", "Second")), width=200)
        seg.set_tips(lambda key: {"a": "one", "b": "two"}.get(key, ""))
        self.assertEqual(ui.tip_of(seg, SimpleNamespace(x=20)), "one")
        self.assertEqual(ui.tip_of(seg, SimpleNamespace(x=180)), "two")
        self.assertEqual(ui.tip_of(seg, SimpleNamespace(x=500)), "")

    def test_a_select_explains_itself_and_its_current_choice(self):
        select = ui.Select(self.root, 200)
        select.set_values(["RORem", "LaMa"])
        select.set("LaMa")
        select.set_tips(lambda: "The method.", {"RORem": "best rated", "LaMa": "fast"}.get)
        self.assertEqual(ui.tip_of(select), "The method.\n\nfast")
        self.assertEqual(select.item_tip("RORem"), "best rated")

    def test_the_tip_window_appears_after_the_pointer_rests_and_goes_on_leave(self):
        label = ui.small(self.root, "Model")
        ui.tip(label, "What it does.\nPros: fast.\nCons: soft.")
        tips_window = ui._tips(self.root)
        label.event_generate("<Enter>")
        tips_window.wait(label, ui.tip_of(label))
        self.assertIsNone(tips_window.window)  # not before the pointer has rested
        tips_window.show()
        self.assertEqual(tips_window.window.winfo_children()[0].cget("text"), "What it does.\nPros: fast.\nCons: soft.")
        tips_window.hide()
        self.assertIsNone(tips_window.window)

    def test_a_new_text_replaces_the_tip_without_binding_twice(self):
        label = ui.small(self.root, "Model")
        ui.tip(label, "first")
        bound = label.bind("<Enter>")
        ui.tip(label, "second")
        self.assertEqual(ui.tip_of(label), "second")
        self.assertEqual(label.bind("<Enter>"), bound)


if __name__ == "__main__":
    unittest.main()
