# SPDX-License-Identifier: Apache-2.0
"""Automatic editing: which models it uses for which edit, and two labelled edits staged together."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import edit_plan, tips  # noqa: E402
from openfill_photocraft.bridge import PCImage, PCLayer  # noqa: E402
from openfill_photocraft.edit_plan import AUTO_EDIT  # noqa: E402
from openfill_photocraft.service import JobState, Service, WebRunner, _joined  # noqa: E402

from openfill.app import NothingSelected  # noqa: E402
from openfill.geometry import Rect  # noqa: E402
from openfill.remote_models import DEV_ID, H3_IMAGE_ID, QWEN21_FULL_ID, QWEN21_LIGHTNING_ID, TURBO_ID  # noqa: E402

SIZE = (240, 200)
EVERYTHING = frozenset({QWEN21_LIGHTNING_ID, QWEN21_FULL_ID, TURBO_ID, DEV_ID, H3_IMAGE_ID})


def selection(box=(100, 80, 140, 120)):
    chosen = Image.new("L", SIZE)
    ImageDraw.Draw(chosen).rectangle(box, fill=255)
    return chosen


class PlanTests(unittest.TestCase):
    def test_a_new_object_goes_to_fibo_first_and_a_new_look_to_qwen(self):
        for prompt in ("Add a garden gnome here.", "Replace the bicycle with a red Vespa scooter.",
                       "Change the coffee mug into a glass of orange juice.", "Put a vase on the table"):
            self.assertEqual(edit_plan.steps(prompt, 0.05, EVERYTHING), [TURBO_ID, QWEN21_LIGHTNING_ID], prompt)
        for prompt in ("Change the man's t-shirt to bright yellow.", "Make the sofa mustard yellow velvet.",
                       "Paint the lighthouse with red and white stripes."):
            self.assertEqual(edit_plan.steps(prompt, 0.05, EVERYTHING), [QWEN21_LIGHTNING_ID, TURBO_ID], prompt)

    def test_lettering_goes_to_qwen_even_when_it_is_replaced(self):
        for prompt in ("Replace the lettering on the sign with the word OPEN.", 'Add the caption "Sale" under the logo'):
            self.assertEqual(edit_plan.steps(prompt, 0.15, EVERYTHING)[0], QWEN21_LIGHTNING_ID, prompt)

    def test_the_whole_picture_never_goes_to_fibo(self):
        for share in (0.98, 1.0):
            self.assertEqual(edit_plan.steps("Add fresh snow everywhere.", share, EVERYTHING), [QWEN21_LIGHTNING_ID, H3_IMAGE_ID])
        self.assertEqual(edit_plan.steps("Make it night time.", 1.0, {QWEN21_LIGHTNING_ID, TURBO_ID}), [QWEN21_LIGHTNING_ID])
        self.assertEqual(edit_plan.kind("Make it night time.", 0.97), "look")

    def test_models_that_are_not_installed_give_way_to_the_next_of_their_family_or_the_next_family(self):
        self.assertEqual(edit_plan.steps("Add a cat.", 0.05, {QWEN21_FULL_ID, DEV_ID}), [DEV_ID, QWEN21_FULL_ID])
        self.assertEqual(edit_plan.steps("Add a cat.", 0.05, {QWEN21_LIGHTNING_ID, H3_IMAGE_ID}), [QWEN21_LIGHTNING_ID, H3_IMAGE_ID])
        self.assertEqual(edit_plan.steps("Make it red.", 0.05, {TURBO_ID}), [TURBO_ID])
        self.assertEqual(edit_plan.steps("Make it red.", 0.05, set()), [])

    def test_automatic_is_offered_only_where_a_selection_can_get_two_edits(self):
        self.assertTrue(edit_plan.offered(EVERYTHING))
        self.assertTrue(edit_plan.offered({QWEN21_LIGHTNING_ID, TURBO_ID}))
        self.assertTrue(edit_plan.offered({TURBO_ID, H3_IMAGE_ID}))
        self.assertFalse(edit_plan.offered({QWEN21_LIGHTNING_ID, QWEN21_FULL_ID}))  # one family
        self.assertFalse(edit_plan.offered({TURBO_ID, DEV_ID}))
        self.assertFalse(edit_plan.offered(set()))

    def test_room_for_an_edit(self):
        square = selection()
        self.assertEqual(edit_plan.roomy(square, "whole", 10.0), (None, False))
        self.assertEqual(edit_plan.roomy(square, "look", 0.0), (None, False))
        self.assertEqual(edit_plan.roomy(Image.new("L", SIZE), "look", 10.0), (None, False))
        grown, room = edit_plan.roomy(square, "look", 10.0)
        self.assertFalse(room)
        self.assertAlmostEqual(grown.histogram()[255] / square.histogram()[255], 1 / 0.9, delta=0.12)
        soft = square.filter(ImageFilter.GaussianBlur(3))   # the artist's own feather is kept
        kept, _room = edit_plan.roomy(soft, "look", 10.0)
        self.assertEqual(ImageChops.subtract(soft, kept).getbbox(), None)
        corner = Image.new("L", SIZE)
        ImageDraw.Draw(corner).polygon([(40, 40), (50, 40), (50, 150), (160, 150), (160, 160), (40, 160)], fill=255)
        roomy, room = edit_plan.roomy(corner, "object", 0.0)
        self.assertTrue(room)
        self.assertEqual(roomy.getpixel((80, 130)), 255)    # the hollow of the L
        self.assertEqual(roomy.getpixel((150, 50)), 0)      # beyond the hull
        same, room = edit_plan.roomy(corner, "look", 0.0)   # a recolour never gets the hull
        self.assertEqual((same, room), (None, False))

    def test_qwen_is_told_where_a_drawn_box_is(self):
        self.assertEqual(edit_plan.told("Add a garden gnome with a red hat standing on the lawn here."),
                         "Add a garden gnome with a red hat standing on the lawn in the middle of the picture.")
        self.assertEqual(edit_plan.told("Put a vase HERE!"), "Put a vase in the middle of the picture!")
        self.assertEqual(edit_plan.told("Add a red ball here"), "Add a red ball in the middle of the picture")
        self.assertEqual(edit_plan.told("Add a red ball."), "Add a red ball.")   # nothing points at the box: as typed
        self.assertEqual(edit_plan.told("Add where it is there"), "Add where it is there")   # "where", "there" are not "here"

    def test_the_plan_for_a_drawn_box(self):
        box = selection()
        plan = edit_plan.plan("Add a potted cactus here.", 0.05, EVERYTHING, box)
        self.assertEqual([(step.model, step.label, step.seed) for step in plan],
                         [(TURBO_ID, "FIBO Replace", 0), (QWEN21_LIGHTNING_ID, "Qwen", 0), (TURBO_ID, "FIBO Replace 2", edit_plan.OTHER_SEED)])
        self.assertEqual([step.prompt for step in plan], ["", "Add a potted cactus in the middle of the picture.", ""])
        self.assertEqual(plan[1].settings["context_padding"], edit_plan.TIGHT)
        self.assertNotIn("context_padding", plan[0].settings)
        self.assertEqual((plan[0].settings["fibo_edit_mode"], plan[2].settings["fibo_edit_mode"]), ("replace", "replace"))
        # Without Qwen: the two FIBO Replace edits. Without FIBO: Qwen told where, then H3.
        self.assertEqual([step.label for step in edit_plan.plan("Add a cactus here.", 0.05, {TURBO_ID, H3_IMAGE_ID}, box)],
                         ["FIBO Replace", "FIBO Replace 2"])
        plain = edit_plan.plan("Add a cactus here.", 0.05, {QWEN21_LIGHTNING_ID, H3_IMAGE_ID}, box)
        self.assertEqual([(step.label, step.prompt) for step in plain],
                         [("Qwen", "Add a cactus in the middle of the picture."), ("H3", "")])
        # A swap, a new look and the whole picture are planned as before.
        self.assertEqual([step.label for step in edit_plan.plan("Replace the bicycle with a scooter.", 0.05, EVERYTHING, box)],
                         ["FIBO Replace", "Qwen", "FIBO Preserve"])
        self.assertTrue(all(not step.prompt and not step.seed for step in edit_plan.plan("Make it red.", 0.05, EVERYTHING, box)))

    def test_told_never_changes_the_words_to_be_written(self):
        # Qwen is the one family that spells: words in quotes, or after "reading", "reads", "saying" or "with ... on it",
        # reach it as typed. Straight double quotes and "says" are lettering already (kind() is "look").
        for prompt in ("Add a sign reading 'Open here'", "Add a plaque reading Here Lies Bob", "Add a doormat with Welcome Here on it",
                       "add a sign saying 'come here'", "Add a neon sign: ‘Open here’", "add a doormat that reads WELCOME HERE",
                       "put a banner reading Here we go", 'Add a sign "come here, now"', "Add a sign with Open Here",
                       "Add a sign 'Don't park here'", "Add a sign: stop here", "Add a neon sign: Open here", "Add a sign (‘No entry here’)"):
            self.assertEqual(edit_plan.told(prompt), prompt, prompt)

    def test_told_changes_only_the_closing_here_that_points_at_the_box(self):
        self.assertEqual(edit_plan.told("Here's the spot, add a cactus here."), "Here's the spot, add a cactus in the middle of the picture.")
        self.assertEqual(edit_plan.told("Add a cactus right here."), "Add a cactus in the middle of the picture.")
        self.assertEqual(edit_plan.told("Add a cactus over here !"), "Add a cactus in the middle of the picture!")
        self.assertEqual(edit_plan.told("Add a kite flying up here."), "Add a kite flying up in the middle of the picture.")   # "up" stays
        self.assertEqual(edit_plan.told("Here, add a cactus."), "Here, add a cactus.")   # not the closing word
        self.assertEqual(edit_plan.told("Add flowers here and there."), "Add flowers here and there.")
        self.assertEqual(edit_plan.told("Add the man's hat here."), "Add the man's hat in the middle of the picture.")   # an apostrophe is no quote
        self.assertEqual(edit_plan.told("Add a cactus. Here we go"), "Add a cactus. Here we go")
        self.assertEqual(edit_plan.told("Add a sphere"), "Add a sphere")   # "here" inside a word
        self.assertEqual(edit_plan.told("Add one where"), "Add one where")

    def test_lettering_in_a_drawn_box_is_planned_as_before_with_qwen_given_the_words_as_typed(self):
        box = selection()
        for prompt in ("Add a sign reading 'Open here'", "Add a plaque reading Here Lies Bob", "Add a doormat with Welcome Here on it",
                       "add a sign saying 'come here'", "Add a neon sign: ‘Open here’", "add a doormat that reads WELCOME HERE",
                       "put a banner reading Here we go"):
            self.assertEqual(edit_plan.shape(prompt, 0.05, box), "spot", prompt)   # _LETTERING misses these
            plan = edit_plan.plan(prompt, 0.05, {TURBO_ID, QWEN21_LIGHTNING_ID, H3_IMAGE_ID}, box)
            self.assertEqual([step.label for step in plan], ["FIBO Replace", "Qwen", "FIBO Preserve"], prompt)
            self.assertTrue(all(not step.prompt and not step.seed and "context_padding" not in step.settings for step in plan), prompt)
            plain = edit_plan.plan(prompt, 0.05, {QWEN21_LIGHTNING_ID, H3_IMAGE_ID}, box)   # without FIBO
            self.assertEqual([(step.label, step.prompt) for step in plain], [("Qwen", ""), ("H3", "")], prompt)
            self.assertNotIn("context_padding", plain[0].settings)

    def test_an_add_onto_a_subject_or_over_an_area_in_a_marquee_keeps_fibo_preserve(self):
        wide = Image.new("L", (1600, 1200))
        ImageDraw.Draw(wide).rectangle((0, 700, 1599, 1199), fill=255)
        cases = [("Add sunglasses to the man.", wide, 0.417), ("Add thick fog over the field.", wide, 0.417),
                 ("Add round glasses to her face.", selection(), 0.05), ("Add a bandana to the dog here.", selection(), 0.05),
                 ("Put a crown on his head here.", selection(), 0.05), ("Add some fog here.", selection(), 0.05),
                 ("Add a potted cactus.", selection(), 0.05)]   # not pointed at the box: the panel rated only "here"
        for prompt, chosen, share in cases:
            self.assertEqual(edit_plan.shape(prompt, share, chosen), "spot", prompt)
            plan = edit_plan.plan(prompt, share, {TURBO_ID, QWEN21_LIGHTNING_ID, H3_IMAGE_ID}, chosen)
            self.assertEqual([step.label for step in plan], ["FIBO Replace", "Qwen", "FIBO Preserve"], prompt)
            self.assertTrue(all(not step.prompt and not step.seed and "context_padding" not in step.settings for step in plan), prompt)
            self.assertEqual([step.label for step in edit_plan.plan(prompt, share, {TURBO_ID}, chosen)], ["FIBO Replace", "FIBO Preserve"])
            plain = edit_plan.plan(prompt, share, {QWEN21_LIGHTNING_ID, H3_IMAGE_ID}, chosen)
            self.assertEqual([(step.label, step.prompt) for step in plain], [("Qwen", ""), ("H3", "")], prompt)
            self.assertNotIn("context_padding", plain[0].settings)

    def test_every_add_the_spot_panel_rated_gets_the_spot_plan(self):
        # The eleven instructions the spot panel rated, each in a drawn box.
        box = selection()
        for prompt in ("Add a red and white striped beach umbrella standing in the sand here.", "Add a small brown dog sitting on the grass here.",
                       "Add a glass bottle of olive oil here.", "Add a round wall clock here.", "Add a small bowl of fresh strawberries here.",
                       "Add a large potted plant standing on the pavement here.", "Add a white swan swimming on the water here.",
                       "Add a red toy ball on the floor here.", "Add a small potted cactus in a terracotta pot here.",
                       "Add a garden gnome with a red hat standing on the lawn here.", "Add a framed botanical print hanging on the wall here."):
            plan = edit_plan.plan(prompt, 0.05, EVERYTHING, box)
            self.assertEqual([step.label for step in plan], ["FIBO Replace", "Qwen", "FIBO Replace 2"], prompt)
            self.assertEqual(plan[1].prompt, prompt[:-len("here.")] + "in the middle of the picture.")
            self.assertEqual(plan[1].settings["context_padding"], edit_plan.TIGHT)

    def test_labels_fit_under_a_thumbnail(self):
        self.assertEqual([edit_plan.short_label(model) for model in (QWEN21_LIGHTNING_ID, TURBO_ID, H3_IMAGE_ID)], ["Qwen", "FIBO Turbo", "H3"])
        self.assertEqual(edit_plan.short_label("something-new", "Something New"), "Something New")
        self.assertEqual(_joined(["FIBO Turbo", "Qwen"], "edits"), "Two edits: FIBO Turbo and Qwen. Keep the better one.")

    def test_both_panels_get_a_tip_that_quotes_the_ratings(self):
        tip = tips.model_tips([])[AUTO_EDIT]
        self.assertIn("17", tip)
        self.assertIn("11", tip)
        self.assertEqual(tips.model_tip(AUTO_EDIT), tip)

    def test_the_browser_view_offers_it_when_the_engine_can_run_it(self):
        from openfill_photocraft.web import Api

        page = (ROOT / "photocraft/openfill_photocraft/static/index.html").read_text(encoding="utf-8")
        self.assertIn(f'const AUTO_EDIT = "{AUTO_EDIT}"', page)
        self.assertIn('opts.unshift(["Automatic",AUTO_EDIT])', page)

        def rows(*ids):
            return [{"id": model, "name": model, "visible": True, "installed": True, "runnable": True, "partial": [],
                     "capabilities": ["fill", "edit"], "missing_nodes": [], "missing_models": []} for model in ids]

        service = Mock(spec=["app", "state"])
        app = service.app
        app.settings = SimpleNamespace(remote_fibo=True, fibo_source="studio")
        app.task_status.return_value = {}
        app.server.pending_tasks.return_value = []
        app.source_label.return_value = "Studio"
        api = Api(service)
        app.model_rows.return_value = rows(QWEN21_LIGHTNING_ID, TURBO_ID)
        self.assertTrue(api.models("edit")["autoEdit"])
        app.model_rows.return_value = rows(QWEN21_LIGHTNING_ID)
        self.assertFalse(api.models("edit")["autoEdit"])
        hidden = rows(QWEN21_LIGHTNING_ID, TURBO_ID)
        hidden[1]["visible"] = False
        app.model_rows.return_value = hidden
        self.assertFalse(api.models("edit")["autoEdit"])


class FakeBridge:
    def __init__(self, profile, chosen):
        self.profile = profile
        self.chosen = chosen

    def _snapshot(self, image):
        return Image.new("RGBA", SIZE), self.chosen


def service(profile, results, installed=(QWEN21_LIGHTNING_ID, TURBO_ID, H3_IMAGE_ID), chosen=None, hidden=(), feather=10.0):
    """A Service whose app stages ``results[model]`` (a colour, or an exception) for each edit."""
    svc = Service.__new__(Service)
    svc.state = JobState(state="running")
    svc.bridge = FakeBridge(profile, selection() if chosen is None else chosen)
    svc.layers, svc.image, svc._choice_kind = [], None, ""
    svc._operation_lock = threading.Lock()
    calls, asked = [], []

    def generative_fill(image, prompt, negative, variations, strength, seed, model, remove=False):
        calls.append((model, variations))
        asked.append((model, prompt, seed))
        outcome = results[model]
        if isinstance(outcome, BaseException):
            raise outcome
        assert not image.pending, "an earlier edit must not reach the document before this one is made"
        made = []
        for index in range(variations):
            name = "Generative Fill" if variations == 1 else f"Generative Fill ({index + 1})"
            made.append(PCLayer(None, name, Rect(0, 0, *SIZE), Image.new("RGBA", SIZE, outcome).tobytes(), None, index == 0,
                                "selection", 0, 0.0, None, None))
        image.pending, image.pending_group = made, "Generative Fill" if len(made) > 1 else ""
        image.layers.extend(made)
        return made

    app = Mock()
    app.settings = SimpleNamespace(remote_fibo=True, fibo_source="studio", hidden_models=list(hidden),
                                   auto_feather=feather > 0, auto_feather_percent=feather)
    app.installed_models = lambda mode: [SimpleNamespace(id=i, name=i) for i in installed]
    app.generative_fill = generative_fill
    svc.app = svc._job_app = app
    svc.calls, svc.asked = calls, asked
    return svc


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.profile = Path(self.folder.name)

    def run_auto(self, svc, prompt="Make the sofa mustard yellow.", variations=1):
        image = PCImage(svc.bridge, "picture.png", 0)
        return image, svc._edit_auto(image, svc.app, prompt, "", None, 7, variations)

    def test_two_models_and_fibos_other_mode_make_three_labelled_choices_staged_together(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["Qwen", "FIBO Preserve", "FIBO Replace"])
        self.assertEqual([layer.name for layer in layers], ["Generative Fill (1)", "Generative Fill (2)", "Generative Fill (3)"])
        self.assertEqual([layer.visible for layer in layers], [True, False, False])
        self.assertEqual((image.pending, image.pending_group), (layers, "Generative Fill"))
        self.assertEqual(svc.calls, [(QWEN21_LIGHTNING_ID, 1), (TURBO_ID, 1), (TURBO_ID, 1)])
        self.assertEqual((svc.state.stage, svc.state.stage_word), ((0, 1), "Edit"))
        self.assertEqual(svc.state.notices, [])
        self.assertAlmostEqual(svc._edit_share, 41 * 41 / (SIZE[0] * SIZE[1]), places=4)

    def test_a_tight_selection_is_grown_so_the_feather_lies_outside_the_object(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        image, _layers = self.run_auto(svc)   # a recolour
        chosen = selection()
        self.assertEqual(image.widened.size, SIZE)
        self.assertEqual(ImageChops.subtract(chosen, image.widened).getbbox(), None)   # nothing taken away
        before, after = chosen.histogram()[255], image.widened.histogram()[255]
        self.assertGreater(after, before * 1.05)   # about a tenth more: the band Auto-feather softens
        self.assertLess(after, before * 1.35)
        self.assertEqual(svc.state.notices, [])

    def test_automatic_grows_the_selection_for_its_own_feather_whatever_is_saved(self):
        # Automatic runs with the settings its edits were rated with (edit_plan.SETTINGS), Auto-feather 10% among them.
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"}, feather=0)
        image, _layers = self.run_auto(svc)
        self.assertIsNotNone(image.widened)
        self.assertEqual(ImageChops.subtract(selection(), image.widened).getbbox(), None)

    def test_a_different_object_gets_room_where_the_old_one_was_hollow(self):
        frame = Image.new("L", SIZE)   # a bicycle-like outline: two thin bars
        draw = ImageDraw.Draw(frame)
        draw.rectangle((60, 60, 70, 150), fill=255)
        draw.rectangle((60, 140, 180, 150), fill=255)
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"}, chosen=frame)
        image, layers = self.run_auto(svc, "Replace the bicycle with a red Vespa scooter.")
        self.assertEqual(image.widened.getpixel((100, 120)), 255)   # inside the hull, off the bars
        self.assertEqual(image.widened.getpixel((200, 40)), 0)
        self.assertEqual(svc.state.notices, [edit_plan.ROOM_NOTICE])
        self.assertEqual([layer.label for layer in layers], ["FIBO Replace", "Qwen", "FIBO Preserve"])

    def test_a_marquee_for_a_new_object_is_roomy_already(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        image, _layers = self.run_auto(svc, "Add a potted cactus here.")
        self.assertEqual(svc.state.notices, [])
        self.assertLess(image.widened.histogram()[255], selection().histogram()[255] * 1.35)

    def test_an_edit_of_the_whole_picture_keeps_its_selection(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", H3_IMAGE_ID: "green"}, chosen=Image.new("L", SIZE, 255))
        image, _layers = self.run_auto(svc, "Add fresh snow.")
        self.assertIsNone(image.widened)

    def test_something_added_in_a_drawn_box_gets_fibo_replace_twice_and_qwen_told_where(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        _image, layers = self.run_auto(svc, "Add a potted cactus here.")
        self.assertEqual([layer.label for layer in layers], ["FIBO Replace", "Qwen", "FIBO Replace 2"])
        self.assertEqual(svc.asked, [(TURBO_ID, "Add a potted cactus here.", 7),
                                     (QWEN21_LIGHTNING_ID, "Add a potted cactus in the middle of the picture.", 7),
                                     (TURBO_ID, "Add a potted cactus here.", 7 + edit_plan.OTHER_SEED)])

    def test_a_random_seed_stays_random_for_the_second_fibo_replace(self):
        # The panel sends -1 for an empty Seed field (index.html seedOf; any negative is random). -1 + OTHER_SEED was the
        # fixed seed 7918: the same second FIBO Replace on every run. Each edit draws its own seed instead.
        from openfill.geometry import seed_or_random

        for seed in (-1, -5):
            svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
            image = PCImage(svc.bridge, "picture.png", 0)
            layers = svc._edit_auto(image, svc.app, "Add a potted cactus here.", "", 1.0, seed, 1)
            self.assertEqual([layer.label for layer in layers], ["FIBO Replace", "Qwen", "FIBO Replace 2"])
            self.assertEqual([asked_seed for _m, _p, asked_seed in svc.asked], [seed, seed, seed])
            self.assertEqual(len({seed_or_random(svc.asked[2][2]) for _ in range(5)}), 5)   # a fresh one on each run

    def test_an_object_added_onto_a_picked_subject_keeps_both_fibo_modes(self):
        blob = Image.new("L", SIZE)
        ImageDraw.Draw(blob).ellipse((80, 60, 160, 140), fill=255)
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"}, chosen=blob)
        _image, layers = self.run_auto(svc, "Add round glasses to her face.")
        self.assertEqual([layer.label for layer in layers], ["FIBO Preserve", "Qwen", "FIBO Replace"])
        self.assertTrue(all(prompt == "Add round glasses to her face." and seed == 7 for _m, prompt, seed in svc.asked))

    def test_the_whole_picture_uses_qwen_and_h3(self):
        whole = Image.new("L", SIZE, 255)
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", H3_IMAGE_ID: "green"}, chosen=whole)
        _image, layers = self.run_auto(svc, "Add fresh snow.")
        self.assertEqual([layer.label for layer in layers], ["Qwen", "H3"])

    def test_a_model_that_fails_leaves_its_share_to_the_next(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: RuntimeError("engine fell over"), TURBO_ID: "blue"})
        image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["FIBO Preserve", "FIBO Preserve", "FIBO Replace"])
        self.assertEqual(svc.calls, [(QWEN21_LIGHTNING_ID, 1), (TURBO_ID, 2), (TURBO_ID, 1)])
        self.assertEqual([layer.name for layer in layers], [f"Generative Fill ({n})" for n in (1, 2, 3)])
        self.assertIn("Qwen could not make an edit", svc.state.notices[0])
        self.assertEqual(image.pending, layers)

    def test_when_every_model_fails_the_first_reason_is_reported(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: RuntimeError("first"), TURBO_ID: RuntimeError("second")})
        with self.assertRaisesRegex(RuntimeError, "first"):
            self.run_auto(svc)

    def test_nothing_selected_is_reported_at_once(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: NothingSelected("select first"), TURBO_ID: "blue"},
                      chosen=Image.new("L", SIZE))
        with self.assertRaises(NothingSelected):
            self.run_auto(svc)
        self.assertEqual(svc.calls, [(QWEN21_LIGHTNING_ID, 1)])

    def test_more_variations_are_shared_between_the_edits(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        _image, layers = self.run_auto(svc, variations=4)
        self.assertEqual(svc.calls, [(QWEN21_LIGHTNING_ID, 2), (TURBO_ID, 1), (TURBO_ID, 1)])
        self.assertEqual([layer.name for layer in layers], [f"Generative Fill ({n})" for n in (1, 2, 3, 4)])
        self.assertEqual([layer.visible for layer in layers], [True, False, False, False])

    def test_hidden_and_missing_models_are_left_out(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", H3_IMAGE_ID: "green"}, hidden=(TURBO_ID,))
        _image, layers = self.run_auto(svc)
        self.assertEqual([layer.label for layer in layers], ["Qwen", "H3"])
        alone = service(self.profile, {TURBO_ID: "blue"}, installed=(TURBO_ID,))
        image, layers = self.run_auto(alone)
        # FIBO alone still shows both its modes
        self.assertEqual(([layer.label for layer in layers], image.pending_group), (["FIBO Preserve", "FIBO Replace"], "Generative Fill"))
        with self.assertRaisesRegex(RuntimeError, "Choose a Model"):
            self.run_auto(service(self.profile, {}, installed=()))

    def test_the_progress_line_counts_edits(self):
        svc = service(self.profile, {})
        svc.state.stage, svc.state.stage_word = (1, 2), "Edit"
        svc.app.job_state = svc.state
        WebRunner(svc.app, "Generative Fill", "Qwen")._progress(0.5, "Sampling")
        self.assertEqual((svc.state.text, svc.state.fraction), ("Edit 2 of 2: Sampling", 0.75))

    def test_keeping_one_of_two_edits_is_noted_without_the_prompt(self):
        svc = service(self.profile, {QWEN21_LIGHTNING_ID: "red", TURBO_ID: "blue"})
        _image, layers = self.run_auto(svc)
        svc.layers, svc._choice_kind = layers, "fill"
        svc._choice_info = {"edit": True, "automatic": True, "share": svc._edit_share}
        svc.state.shown = 1
        svc._log_choice("selected")
        row = json.loads((self.profile / "edit-choices.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((row["kept"], row["offered"], row["automatic"]), ("FIBO Preserve", ["Qwen", "FIBO Preserve", "FIBO Replace"], True))
        self.assertEqual(set(row), {"t", "kept", "offered", "automatic", "share"})
        self.assertFalse((self.profile / "removal-choices.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
