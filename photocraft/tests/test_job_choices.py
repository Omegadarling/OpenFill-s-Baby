# SPDX-License-Identifier: Apache-2.0
"""A job runs with the settings its results were rated with, whatever is saved, and saves none of them."""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import removal_plan  # noqa: E402
from openfill_photocraft.removal_advice import TEXTURE_REMOVAL  # noqa: E402
from openfill_photocraft.service import Service, _choices  # noqa: E402

from openfill.app import App  # noqa: E402
from openfill.config import Settings  # noqa: E402
from openfill.remote_models import ROREM_ID  # noqa: E402

CHOSEN = ("seam_correction", "grain_match", "fibo_edit_mode", "fibo_context_mode", "fibo_context_percent",
          "fibo_boundary_match", "fibo_boundary_strength", "auto_feather", "auto_feather_percent")


def bare_app(**saved):
    app = App.__new__(App)   # as the app's own tests build one: no profile, no engine
    app.settings = Settings(**saved)
    return app


class OptionTests(unittest.TestCase):
    def test_a_job_sees_its_own_choice_and_the_saved_setting_stays(self):
        app = bare_app(seam_correction=False, grain_match=False, fibo_edit_mode="preserve")
        self.assertIs(app.option("seam_correction"), False)
        with app.choosing(seam_correction=True, fibo_edit_mode="replace"):
            self.assertIs(app.option("seam_correction"), True)
            self.assertEqual(app.option("fibo_edit_mode"), "replace")
            self.assertIs(app.option("grain_match"), False)        # not chosen: what is saved
            self.assertIs(app.settings.seam_correction, False)     # nothing is written to the settings
            self.assertEqual(app.settings.fibo_edit_mode, "preserve")
        self.assertIs(app.option("seam_correction"), False)
        self.assertEqual(app.job_options, {})

    def test_choices_nest_and_end_with_the_job_even_when_it_fails(self):
        app = bare_app(fibo_context_mode="local")
        with self.assertRaises(RuntimeError), app.choosing(fibo_context_mode="full"):
            with app.choosing(seam_correction=False):
                self.assertEqual((app.option("fibo_context_mode"), app.option("seam_correction")), ("full", False))
            self.assertIs(app.option("seam_correction"), True)
            raise RuntimeError("the engine failed")
        self.assertEqual(app.option("fibo_context_mode"), "local")

    def test_a_job_may_choose_how_much_a_fill_sees_around_the_selection(self):
        from openfill import geometry

        app = bare_app()
        self.assertEqual(app._job_padding(False, False), geometry.fill_padding(False, False))
        self.assertEqual(app._job_padding(True, True), geometry.fill_padding(True, True))
        with app.choosing(context_padding=0.2):
            self.assertEqual(app._job_padding(False, False), 0.2)
        self.assertEqual(app._job_padding(False, False), geometry.PROMPT_PADDING)
        tree = ast.parse((ROOT / "gimp-openfill/openfill/app.py").read_text(encoding="utf-8"))
        fill = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "generative_fill")
        calls = {node.func.attr for node in ast.walk(fill) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertIn("_job_padding", calls)
        self.assertNotIn("fill_padding", calls)   # read through the job, so Automatic's choice reaches the plan

    def test_the_fill_reads_every_such_setting_through_the_job(self):
        # A setting a job may choose must not be read straight from the saved settings inside generative_fill.
        tree = ast.parse((ROOT / "gimp-openfill/openfill/app.py").read_text(encoding="utf-8"))
        fill = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "generative_fill")
        direct = [node.attr for node in ast.walk(fill)
                  if isinstance(node, ast.Attribute) and node.attr in CHOSEN
                  and isinstance(node.value, ast.Attribute) and node.value.attr == "settings"]
        self.assertEqual(direct, [])


class ChoosingApp:
    """An app double that records the choices in force while it fills."""

    def __init__(self):
        self.job_options, self.seen = {}, []
        self.settings = SimpleNamespace(remote_fibo=True, hidden_models=[])

    choosing = App.choosing
    option = App.option

    def generative_fill(self, image, prompt, negative, variations, strength, seed, how, remove=False, as_picked=False):
        self.seen.append((how, dict(self.job_options)))
        return [SimpleNamespace(name="Object Removal", label="")]

    def installed_models(self, mode):
        return []


class RemovalTests(unittest.TestCase):
    def service(self):
        svc = Service.__new__(Service)
        svc.bridge = Mock()
        return svc

    def test_an_engine_removal_runs_with_the_corrections_its_ratings_were_made_with(self):
        self.assertEqual(removal_plan.SETTINGS, {"seam_correction": True, "grain_match": True})
        app, image = ChoosingApp(), SimpleNamespace(pending_group="", pending=[])
        self.service()._remove_with(image, app, ROREM_ID, 7, 1)
        self.assertEqual(app.seen, [(ROREM_ID, {"seam_correction": True, "grain_match": True})])
        self.assertEqual(app.job_options, {})   # and only while it runs

    def test_an_installer_that_saved_them_off_no_longer_decides(self):
        app = bare_app(seam_correction=False, grain_match=False)
        with _choices(app, **removal_plan.SETTINGS):
            self.assertEqual((app.option("seam_correction"), app.option("grain_match")), (True, True))
        self.assertEqual((app.settings.seam_correction, app.settings.grain_match), (False, False))

    def test_an_app_without_choices_runs_as_it_is(self):
        with _choices(Mock(), seam_correction=True):
            pass
        with _choices(SimpleNamespace(), seam_correction=True):
            pass

    def test_local_repair_needs_no_engine_settings(self):
        self.assertNotEqual(TEXTURE_REMOVAL, ROREM_ID)
        source = (ROOT / "photocraft/openfill_photocraft/service.py").read_text(encoding="utf-8")
        self.assertIn("with _choices(app, **removal_plan.SETTINGS):", source)


class EditChoicesTests(unittest.TestCase):
    """Automatic Edit runs FIBO in the mode that suits the edit, and the other mode as a third result."""

    def automatic(self, prompt, chosen, saved_mode="preserve"):
        from openfill_photocraft.bridge import PCImage
        from openfill_photocraft.service import JobState
        from PIL import Image

        from openfill.remote_models import H3_IMAGE_ID, QWEN21_LIGHTNING_ID, TURBO_ID

        seen = []

        class App(ChoosingApp):
            def __init__(self):
                super().__init__()
                self.settings = Settings(remote_fibo=True, fibo_edit_mode=saved_mode, auto_feather=False,
                                         fibo_boundary_strength=50.0)

            def installed_models(self, mode):
                return [SimpleNamespace(id=i, name=i) for i in (QWEN21_LIGHTNING_ID, TURBO_ID, H3_IMAGE_ID)]

            def generative_fill(self, image, prompt, negative, variations, strength, seed, model, remove=False):
                seen.append((model, self.option("fibo_edit_mode"), self.option("fibo_context_mode"),
                             self.option("auto_feather"), self.option("fibo_boundary_strength")))
                return [SimpleNamespace(name="Generative Fill", label="", visible=True)]

        svc = Service.__new__(Service)
        svc.state = JobState(state="running")
        svc.bridge = SimpleNamespace(_snapshot=lambda image: (Image.new("RGBA", chosen.size), chosen))
        app = App()
        image = PCImage(svc.bridge, "picture.png", 0)
        layers = svc._edit_auto(image, app, prompt, "", None, 7, 1)
        self.assertEqual(app.job_options, {})   # nothing chosen is left behind, and nothing was saved
        self.assertEqual(app.settings.fibo_edit_mode, saved_mode)
        return [layer.label for layer in layers], seen

    def blob(self):
        from PIL import Image, ImageDraw

        chosen = Image.new("L", (200, 160))
        ImageDraw.Draw(chosen).ellipse((40, 30, 120, 120), fill=255)
        return chosen

    def test_a_new_look_runs_fibo_in_preserve_and_then_in_replace(self):
        from openfill.remote_models import QWEN21_LIGHTNING_ID, TURBO_ID

        labels, seen = self.automatic("Make the sofa mustard yellow velvet.", self.blob(), saved_mode="replace")
        self.assertEqual(labels, ["Qwen", "FIBO Preserve", "FIBO Replace"])
        self.assertEqual([(model, mode, context) for model, mode, context, _feather, _boundary in seen],
                         [(QWEN21_LIGHTNING_ID, "replace", "local"),   # Qwen ignores FIBO's settings; these are the saved ones
                          (TURBO_ID, "preserve", "full"), (TURBO_ID, "replace", "local")])
        self.assertEqual({(feather, boundary) for _m, _mode, _c, feather, boundary in seen}, {(True, 25.0)})   # as rated

    def test_a_different_object_runs_fibo_in_replace_first(self):
        from openfill.remote_models import TURBO_ID

        labels, seen = self.automatic("Replace the bicycle with a red Vespa scooter.", self.blob())
        self.assertEqual(labels, ["FIBO Replace", "Qwen", "FIBO Preserve"])
        self.assertEqual((seen[0][0], seen[0][1], seen[0][2]), (TURBO_ID, "replace", "local"))

    def test_something_added_in_a_drawn_box_is_a_new_object_and_onto_a_subject_a_new_look(self):
        from PIL import Image, ImageDraw

        box = Image.new("L", (200, 160))
        ImageDraw.Draw(box).rectangle((40, 30, 120, 120), fill=255)
        self.assertEqual(self.automatic("Add a framed print on the wall here.", box)[0][0], "FIBO Replace")
        self.assertEqual(self.automatic("Add round glasses to her face.", self.blob())[0][0], "FIBO Preserve")
        from openfill_photocraft import edit_plan

        self.assertTrue(edit_plan.drawn_box(box))
        self.assertFalse(edit_plan.drawn_box(self.blob()))
        self.assertFalse(edit_plan.drawn_box(None))
        self.assertEqual(edit_plan.shape("Make it winter.", 1.0, None), "whole")
        self.assertEqual(edit_plan.shape("Change the mug into a glass of juice.", 0.04, self.blob()), "swap")

    def padded(self, prompt, seed=7):
        """(model, words, seed, context_padding) for each edit of ``prompt`` in a drawn box, and what is chosen after."""
        from openfill_photocraft.bridge import PCImage
        from openfill_photocraft.service import JobState
        from PIL import Image, ImageDraw

        from openfill.remote_models import H3_IMAGE_ID, QWEN21_LIGHTNING_ID, TURBO_ID

        box = Image.new("L", (200, 160))
        ImageDraw.Draw(box).rectangle((40, 30, 120, 120), fill=255)
        seen = []

        class App(ChoosingApp):
            def installed_models(self, mode):
                return [SimpleNamespace(id=i, name=i) for i in (QWEN21_LIGHTNING_ID, TURBO_ID, H3_IMAGE_ID)]

            def generative_fill(self, image, prompt, negative, variations, strength, seed, model, remove=False):
                seen.append((model, prompt, seed, self.job_options.get("context_padding")))   # as App._job_padding reads it
                return [SimpleNamespace(name="Generative Fill", label="", visible=True)]

        svc = Service.__new__(Service)
        svc.state = JobState(state="running")
        svc.bridge = SimpleNamespace(_snapshot=lambda image: (Image.new("RGBA", box.size), box))
        app = App()
        svc._edit_auto(PCImage(svc.bridge, "picture.png", 0), app, prompt, "", None, seed, 1)
        return seen, app.job_options

    def test_only_qwen_in_a_drawn_box_gets_the_tight_crop_and_it_ends_with_its_edit(self):
        from openfill_photocraft import edit_plan

        from openfill.remote_models import QWEN21_LIGHTNING_ID, TURBO_ID

        seen, after = self.padded("Add a potted cactus here.")
        self.assertEqual([(model, padding) for model, _words, _seed, padding in seen],
                         [(TURBO_ID, None), (QWEN21_LIGHTNING_ID, edit_plan.TIGHT), (TURBO_ID, None)])
        self.assertEqual(after, {})   # the next job sees the usual crop
        # Lettering in a drawn box: planned as before, every model given the words as typed, no tight crop.
        seen, after = self.padded("Add a sign reading 'Open here'")
        self.assertEqual(seen, [(TURBO_ID, "Add a sign reading 'Open here'", 7, None),
                                (QWEN21_LIGHTNING_ID, "Add a sign reading 'Open here'", 7, None),
                                (TURBO_ID, "Add a sign reading 'Open here'", 7, None)])
        self.assertEqual(after, {})
        # A random seed stays random for every edit, the second FIBO Replace too.
        seen, _after = self.padded("Add a potted cactus here.", seed=-1)
        self.assertEqual([seed for _model, _words, seed, _padding in seen], [-1, -1, -1])

    def test_fibo_reads_its_own_context_and_never_the_jobs_padding(self):
        tree = ast.parse((ROOT / "gimp-openfill/openfill/app.py").read_text(encoding="utf-8"))
        fill = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "generative_fill")
        chosen = [node for node in ast.walk(fill) if isinstance(node, ast.IfExp)
                  and isinstance(node.orelse, ast.Call) and getattr(node.orelse.func, "attr", "") == "_job_padding"]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(ast.unparse(chosen[0].test), "fibo_edit")
        self.assertIn("fibo_context_percent", ast.unparse(chosen[0].body))


if __name__ == "__main__":
    unittest.main()
