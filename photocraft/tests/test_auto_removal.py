# SPDX-License-Identifier: Apache-2.0
"""Automatic removal: the method plan, the object-still-there check, and two labelled repairs."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import removal_plan, removal_table  # noqa: E402
from openfill_photocraft.bridge import PCImage, PCLayer  # noqa: E402
from openfill_photocraft.removal_check import object_remains, persistence  # noqa: E402
from openfill_photocraft.removal_plan import AUTO_REMOVAL  # noqa: E402
from openfill_photocraft.service import JobState, Service, WebRunner, _joined  # noqa: E402

from openfill import quality  # noqa: E402
from openfill.geometry import Rect  # noqa: E402
from openfill.remote_models import H3_IMAGE_ID, LAMA_ID, QWEN21_LIGHTNING_ID, ROREM_ID  # noqa: E402

# The cascade is tested on a fixed order, so the tests hold when new ratings rewrite ``removal_table``.
TABLE = (
    (0.01, (("rorem", 1), ("lama", 1), ("qwen", 1))),
    (0.05, (("rorem", 1), ("qwen", 1), ("lama", 1))),
    (1.01, (("rorem", 2), ("qwen", 1), ("lama", 1))),
)
SIZE = (240, 200)
HOLE = (100, 80, 140, 120)  # 3.5% of the picture: a mid-sized selection


def scene(hole=HOLE):
    """A striped wall with a checkered object, and the selection around the object."""
    picture = Image.new("RGB", SIZE, (120, 130, 140))
    draw = ImageDraw.Draw(picture)
    for x in range(0, SIZE[0], 12):
        draw.line((x, 0, x, SIZE[1]), fill=(105, 115, 125))
    for y in range(hole[1] + 6, hole[3] - 6, 8):
        for x in range(hole[0] + 6, hole[2] - 6, 8):
            draw.rectangle((x, y, x + 3, y + 3), fill=(230, 60, 40))
    selection = Image.new("L", SIZE)
    ImageDraw.Draw(selection).rectangle(hole, fill=255)
    return picture, selection


def wall():
    picture = Image.new("RGB", SIZE, (120, 130, 140))
    draw = ImageDraw.Draw(picture)
    for x in range(0, SIZE[0], 12):
        draw.line((x, 0, x, SIZE[1]), fill=(105, 115, 125))
    return picture


def staged(picture, selection, label=""):
    """A whole-canvas result layer masked by the selection, as a remote removal stages it."""
    return PCLayer(
        None,
        "Object Removal",
        Rect(0, 0, *SIZE),
        picture.convert("RGBA").tobytes(),
        None,
        True,
        "pixels",
        0,
        0.0,
        selection.tobytes(),
        None,
        label,
    )


class CheckTests(unittest.TestCase):
    def test_an_untouched_object_scores_high_and_a_repaired_wall_low(self):
        picture, selection = scene()
        self.assertGreater(persistence(picture, picture, selection), 0.9)
        self.assertLess(persistence(picture, wall(), selection), quality.STILL_THERE)
        self.assertTrue(object_remains(picture, selection, staged(picture, selection)))
        self.assertFalse(object_remains(picture, selection, staged(wall(), selection)))

    def test_flat_repairs_and_missing_selections_are_never_called_failures(self):
        picture, selection = scene()
        flat = Image.new("RGB", SIZE, (120, 130, 140))
        self.assertEqual(persistence(flat, flat, selection), 0.0)  # nothing to correlate: not "still there"
        self.assertFalse(object_remains(flat, selection, staged(flat, selection)))
        self.assertFalse(object_remains(picture, None, staged(picture, selection)))
        self.assertIsNone(persistence(picture, picture, Image.new("L", SIZE)))


class PlanTests(unittest.TestCase):
    everything = frozenset({ROREM_ID, LAMA_ID, QWEN21_LIGHTNING_ID, H3_IMAGE_ID})

    def test_every_size_starts_with_a_rated_method_and_offers_two_results(self):
        for share in (0.001, 0.03, 0.4):
            order = removal_plan.steps(share, self.everything)
            self.assertGreaterEqual(sum(count for _method, count in order), removal_plan.RESULTS)
            self.assertEqual(len({method for method, _count in order}), len(order))
            self.assertNotIn(H3_IMAGE_ID, [method for method, _count in order])

    def test_methods_that_are_not_installed_drop_out(self):
        order = removal_plan.steps(0.03, {LAMA_ID, QWEN21_LIGHTNING_ID})
        self.assertEqual({method for method, _count in order}, {LAMA_ID, QWEN21_LIGHTNING_ID})
        self.assertIsNone(removal_plan.first_method({QWEN21_LIGHTNING_ID, H3_IMAGE_ID}))
        self.assertIsNone(removal_plan.first_method(set()))
        self.assertEqual(removal_plan.first_method(self.everything), removal_plan.steps(0.03, self.everything)[0][0])

    def test_the_first_method_is_the_one_the_table_names_first_for_a_mid_sized_selection(self):
        for family, method in (("rorem", ROREM_ID), ("qwen", QWEN21_LIGHTNING_ID)):
            table = ((0.01, TABLE[0][1]), (0.05, ((family, 2), ("lama", 1), ("qwen", 1), ("rorem", 1))), (1.01, TABLE[2][1]))
            with patch.object(removal_table, "SIZES", table):
                self.assertEqual(removal_plan.first_method(self.everything), method)
                self.assertEqual(removal_plan.steps(0.03, self.everything)[0], (method, 2))

    def test_a_managed_install_resolves_its_own_ids(self):
        order = removal_plan.steps(0.03, {"rorem-sdxl", "lama-big"})
        self.assertEqual({method for method, _count in order}, {"rorem-sdxl", "lama-big"})
        self.assertEqual(removal_plan.short_label("lama-big"), "LaMa")
        self.assertEqual(removal_plan.short_label("something-new", "Something New"), "Something New")


class FakeBridge:
    def __init__(self, profile):
        self.profile = profile
        self.picture, self.selection = scene()

    def _snapshot(self, image):
        return self.picture.convert("RGBA"), self.selection


def service(profile, results, installed=(ROREM_ID, LAMA_ID, QWEN21_LIGHTNING_ID)):
    """A Service whose app stages ``results[method]`` (a picture, or an exception) for each removal."""
    svc = Service.__new__(Service)
    svc.state = JobState(state="running")
    svc.bridge = FakeBridge(profile)
    svc.layers, svc.image, svc._choice_kind = [], None, ""
    svc._operation_lock = threading.Lock()
    calls = []

    def generative_fill(image, prompt, negative, variations, strength, seed, how, remove=False, as_picked=False):
        calls.append((how, variations))
        outcome = results[how]
        if isinstance(outcome, BaseException):
            raise outcome
        assert not image.pending, "an earlier repair must not reach the document before this one is made"
        made = []
        for index in range(variations):
            layer = staged(outcome, svc.bridge.selection)
            layer.visible = index == 0
            made.append(layer)
        image.pending, image.pending_group = made, "Object Removal" if len(made) > 1 else ""
        image.layers.extend(made)
        return made

    names = {
        ROREM_ID: "RORem (SDXL)",
        LAMA_ID: "LaMa",
        QWEN21_LIGHTNING_ID: "Qwen Image 2.1 Lightning",
        H3_IMAGE_ID: "MiniMax H3 Image",
    }
    app = Mock()
    app.settings = SimpleNamespace(remote_fibo=True, fibo_source="studio", hidden_models=[])
    app.installed_models = lambda mode: [SimpleNamespace(id=i, name=names[i]) for i in installed]
    app.generative_fill = generative_fill
    svc.app = svc._job_app = app
    svc.app_for = lambda mode: app
    svc.busy = lambda: False
    svc.photocraft = lambda: {"selectionKey": "selection-1"}
    svc.calls = calls
    return svc


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.profile = Path(self.folder.name)
        self.picture, self.selection = scene()
        pinned = patch.object(removal_table, "SIZES", TABLE)
        pinned.start()
        self.addCleanup(pinned.stop)

    def run_auto(self, svc, variations=1):
        image = PCImage(svc.bridge, "picture.png", 0)
        layers = svc._remove(image, {"removal": AUTO_REMOVAL}, 7, variations)
        return image, layers

    def test_two_methods_make_two_labelled_choices_staged_together(self):
        svc = service(self.profile, {ROREM_ID: wall(), QWEN21_LIGHTNING_ID: wall(), LAMA_ID: wall()})
        image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["RORem", "Qwen"])
        self.assertEqual([layer.name for layer in layers], ["Object Removal (1)", "Object Removal (2)"])
        self.assertEqual([layer.visible for layer in layers], [True, False])
        self.assertEqual((image.pending, image.pending_group), (layers, "Object Removal"))
        self.assertEqual(svc.calls, [(ROREM_ID, 1), (QWEN21_LIGHTNING_ID, 1)])
        self.assertEqual(svc.state.stage, (0, 1))
        self.assertEqual(svc.state.notices, [])

    def test_a_repair_that_leaves_the_object_gives_way_to_the_next_method(self):
        svc = service(self.profile, {ROREM_ID: wall(), QWEN21_LIGHTNING_ID: self.picture, LAMA_ID: wall()})
        image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["RORem", "LaMa"])
        self.assertEqual(len(image.layers), 2)  # the rejected repair is not tracked as a result
        self.assertIn("Qwen left the object in place", svc.state.notices[0])

    def test_a_method_that_fails_does_not_cost_the_other_repairs(self):
        svc = service(
            self.profile, {ROREM_ID: RuntimeError("engine fell over"), QWEN21_LIGHTNING_ID: wall(), LAMA_ID: wall()}
        )
        _image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["Qwen", "LaMa"])
        self.assertIn("RORem could not make a repair", svc.state.notices[0])

    def test_when_every_repair_leaves_the_object_the_artist_still_sees_them_with_a_warning(self):
        svc = service(self.profile, {ROREM_ID: self.picture, QWEN21_LIGHTNING_ID: self.picture, LAMA_ID: self.picture})
        _image, layers = self.run_auto(svc)
        self.assertEqual(len(layers), 2)
        self.assertIn("Every method left part of the object", svc.state.notices[-1])

    def test_no_repair_at_all_is_an_error_and_more_variations_ask_for_more_results(self):
        failing = service(
            self.profile,
            {
                ROREM_ID: ValueError("no background left"),
                QWEN21_LIGHTNING_ID: RuntimeError("down"),
                LAMA_ID: RuntimeError("down"),
            },
        )
        with self.assertRaisesRegex(ValueError, "no background left"):  # the first method's own reason
            self.run_auto(failing)
        self.assertEqual(failing.state.notices, [])
        svc = service(self.profile, {ROREM_ID: wall(), QWEN21_LIGHTNING_ID: wall(), LAMA_ID: wall()})
        _image, layers = self.run_auto(svc, variations=3)
        self.assertEqual([layer.label for layer in layers], ["RORem", "Qwen", "LaMa"])

    def test_a_large_selection_takes_both_results_from_the_first_method_in_one_call(self):
        svc = service(self.profile, {ROREM_ID: wall(), QWEN21_LIGHTNING_ID: wall(), LAMA_ID: wall()})
        svc.bridge.picture, svc.bridge.selection = scene((60, 40, 180, 160))  # 30% of the picture
        _image, layers = self.run_auto(svc)
        self.assertEqual(svc.calls, [(ROREM_ID, 2)])
        self.assertEqual([layer.label for layer in layers], ["RORem", "RORem"])

    def test_a_manual_method_is_labelled_too(self):
        svc = service(self.profile, {LAMA_ID: wall()})
        image = PCImage(svc.bridge, "picture.png", 0)
        layers = svc._remove(image, {"removal": LAMA_ID}, 7, 1)
        self.assertEqual([layer.label for layer in layers], ["LaMa"])
        self.assertEqual(svc.calls, [(LAMA_ID, 1)])

    def test_advice_is_the_plan_where_rated_methods_are_installed_and_h3_elsewhere(self):
        svc = service(self.profile, {})
        advice = svc.removal_advice([ROREM_ID, LAMA_ID, QWEN21_LIGHTNING_ID, H3_IMAGE_ID])
        self.assertEqual(
            (advice["status"], advice["method"], advice["displayMethod"], advice["source"]),
            ("ready", AUTO_REMOVAL, ROREM_ID, "studio"),
        )
        self.assertEqual(advice["key"], "selection-1")
        other = svc.removal_advice([QWEN21_LIGHTNING_ID, H3_IMAGE_ID])
        self.assertEqual((other["method"], other["displayMethod"]), (H3_IMAGE_ID, H3_IMAGE_ID))
        svc.photocraft = dict
        self.assertEqual(svc.removal_advice([ROREM_ID, LAMA_ID])["status"], "empty")

    def test_a_removal_without_a_chosen_method_uses_the_plan_where_one_exists(self):
        svc = service(self.profile, {})
        with patch("openfill_photocraft.service.threading.Thread") as thread:
            svc._start("fill", {"remove": True})
        self.assertEqual(thread.call_args.kwargs["args"][1]["removal"], AUTO_REMOVAL)
        svc.app.installed_models = lambda mode: [SimpleNamespace(id=H3_IMAGE_ID, name="MiniMax H3 Image")]
        with patch("openfill_photocraft.service.threading.Thread") as thread:
            svc._start("fill", {"remove": True})
        self.assertEqual(thread.call_args.kwargs["args"][1]["removal"], H3_IMAGE_ID)

    def test_keeping_a_result_records_its_method_without_pixels(self):
        svc = service(self.profile, {})
        svc.layers = [staged(wall(), self.selection, "RORem"), staged(wall(), self.selection, "Qwen")]
        svc._choice_kind, svc._choice_info = "fill", {"automatic": True, "share": 0.075}
        svc.state.shown = 1
        svc._log_choice("selected")
        svc._log_choice("none")
        rows = [
            json.loads(line)
            for line in (self.profile / "removal-choices.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["kept"] for row in rows], ["Qwen", "none"])
        self.assertEqual(rows[0]["offered"], ["RORem", "Qwen"])
        self.assertEqual((rows[0]["automatic"], rows[0]["share"]), (True, 0.075))
        self.assertEqual(set(rows[0]), {"t", "kept", "offered", "automatic", "share"})

    def test_thumbnail_captions_name_the_method_and_fit_narrow_cells(self):
        from openfill_photocraft.bar import thumb_caption

        self.assertEqual(thumb_caption(0, "RORem", 140), "1 · RORem")
        self.assertEqual(thumb_caption(2, "", 140), "3")
        narrow = thumb_caption(1, "AnyPaint", 60)
        self.assertTrue(narrow.startswith("2 · ") and narrow.endswith("…") and len(narrow) <= 8, narrow)

    def test_progress_and_the_done_line_name_each_repair(self):
        state = JobState(state="running")
        state.stage = (1, 2)
        runner = WebRunner(SimpleNamespace(job_state=state), "Object Removal", "LaMa")
        runner._progress(0.5, "Removing object...")
        self.assertEqual((state.fraction, state.text), (0.75, "Repair 2 of 2: Removing object..."))
        state.stage = (0, 1)
        runner._progress(0.5, "Removing object...")
        self.assertEqual((state.fraction, state.text), (0.5, "Removing object..."))
        self.assertEqual(_joined(["RORem", "Qwen"]), "Two repairs: RORem and Qwen. Keep the better one.")
        self.assertEqual(_joined(["RORem"]), "")
        self.assertEqual(state.snapshot()["variationLabels"], [])


if __name__ == "__main__":
    unittest.main()
