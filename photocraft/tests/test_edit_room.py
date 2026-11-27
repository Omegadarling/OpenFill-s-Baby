# SPDX-License-Identifier: Apache-2.0
"""Room for an Automatic edit (edit_plan.roomy): what the selection becomes before the models see it."""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import edit_plan  # noqa: E402
from openfill_photocraft.bridge import PCImage, PCLayer  # noqa: E402
from openfill_photocraft.service import JobState, Service  # noqa: E402

from openfill.auto_feather import feather_selection  # noqa: E402
from openfill.geometry import Rect  # noqa: E402
from openfill.remote_models import H3_IMAGE_ID, QWEN21_LIGHTNING_ID, TURBO_ID  # noqa: E402

PICTURE = (800, 600)
SMALL = (240, 200)


def canvas(size=PICTURE, fill=0):
    return Image.new("L", size, fill)


def count(mask, level=1):
    """Pixels of ``mask`` at ``level`` or more."""
    return sum(mask.histogram()[level:])


def inside(mask, region, level=1):
    """Pixels of ``region`` (white) where ``mask`` is at ``level`` or more."""
    return count(ImageChops.darker(region, mask.point(lambda value: 255 if value >= level else 0)), 255)


def largest_step(mask):
    width, height = mask.size
    return max(ImageChops.difference(mask.crop((0, 0, width - 1, height)), mask.crop((1, 0, width, height))).getextrema()[1],
               ImageChops.difference(mask.crop((0, 0, width, height - 1)), mask.crop((0, 1, width, height))).getextrema()[1])


def mean(mask, region):
    """The mean level of ``mask`` over the white pixels of ``region``."""
    histogram = mask.histogram(mask=region)
    return sum(level * number for level, number in enumerate(histogram)) / sum(histogram)


def layer_mask(mask):
    """What the result layer gets with Auto-feather at 10%."""
    return Image.frombytes("L", mask.size, feather_selection(mask.tobytes(), *mask.size, 10.0))


class SurroundingsTests(unittest.TestCase):
    """A selection that is the picture around something is left as the artist made it."""

    def test_a_background_with_its_subject_left_out_is_not_grown_into_the_subject(self):
        subject = canvas()
        ImageDraw.Draw(subject).ellipse((300, 70, 500, 530), fill=255)
        for kind in ("swap", "look"):
            self.assertEqual(edit_plan.roomy(ImageChops.invert(subject), kind, 10.0), (None, False), kind)

    def test_everything_but_a_kept_item_stays_without_the_item(self):
        item = canvas()
        ImageDraw.Draw(item).rectangle((340, 240, 459, 359), fill=255)
        self.assertEqual(edit_plan.roomy(ImageChops.invert(item), "look", 10.0), (None, False))

    def test_a_sky_or_a_wall_along_three_edges_is_not_grown_into_what_it_borders(self):
        sky = canvas()
        draw = ImageDraw.Draw(sky)
        draw.rectangle((0, 0, 799, 299), fill=255)
        draw.rectangle((320, 60, 480, 299), fill=0)   # a tower
        wall = canvas()
        ImageDraw.Draw(wall).rectangle((0, 0, 399, 599), fill=255)
        for selection in (sky, wall):
            for kind in ("swap", "look"):
                self.assertEqual(edit_plan.roomy(selection, kind, 10.0), (None, False))


class HullTests(unittest.TestCase):
    """A different object gets the hull of each part, and nothing the artist left between or inside them."""

    def test_two_parts_get_a_hull_each_and_nothing_between_them(self):
        earrings = canvas()
        draw = ImageDraw.Draw(earrings)
        draw.ellipse((150, 280, 210, 380), fill=255)
        draw.ellipse((650, 280, 710, 380), fill=255)
        face = canvas()
        ImageDraw.Draw(face).rectangle((260, 280, 600, 380), fill=255)
        for kind in ("swap", "object"):
            fitted, _room = edit_plan.roomy(earrings, kind, 10.0)
            self.assertEqual(inside(fitted, face), 0, kind)

    def test_the_picture_inside_a_frame_stays_out(self):
        frame = canvas()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((200, 100, 600, 500), fill=255)
        draw.rectangle((230, 130, 570, 470), fill=0)
        picture = canvas()
        ImageDraw.Draw(picture).rectangle((262, 162, 538, 438), fill=255)   # the picture without the band the frame grows by
        for kind in ("swap", "object"):
            fitted, _room = edit_plan.roomy(frame, kind, 10.0)
            self.assertEqual(inside(fitted, picture), 0, kind)
            self.assertGreater(count(fitted, 128), count(frame, 128), kind)   # the frame itself still grows

    def test_separate_flower_heads_are_not_joined_into_one_bouquet(self):
        heads = canvas()
        draw = ImageDraw.Draw(heads)
        for x, y in ((200, 200), (300, 180), (260, 300), (380, 260)):
            draw.ellipse((x, y, x + 60, y + 60), fill=255)
        for kind in ("swap", "object"):
            fitted, room = edit_plan.roomy(heads, kind, 10.0)
            self.assertEqual(fitted.getpixel((285, 270)), 0, kind)   # between the heads
            self.assertFalse(room, kind)   # each head fills its own hull

    def test_a_thin_object_still_gets_room(self):
        bars = canvas(SMALL)   # a bicycle-like outline
        draw = ImageDraw.Draw(bars)
        draw.rectangle((60, 60, 70, 150), fill=255)
        draw.rectangle((60, 140, 180, 150), fill=255)
        for kind in ("swap", "object"):   # "object" is the older word for a swap
            fitted, room = edit_plan.roomy(bars, kind, 10.0)
            self.assertTrue(room, kind)
            self.assertEqual((fitted.getpixel((100, 120)), fitted.getpixel((200, 40))), (255, 0), kind)

    def test_an_object_that_fills_the_picture_with_its_room_is_left_alone(self):
        letter = canvas((1000, 800))   # a C: its hull and ring take in the whole picture
        draw = ImageDraw.Draw(letter)
        for bar in ((40, 30, 960, 120), (40, 680, 960, 770), (40, 30, 130, 770)):
            draw.rectangle(bar, fill=255)
        self.assertEqual(edit_plan.roomy(letter, "swap", 10.0), (None, False))

    def test_the_ring_is_not_cut_where_a_weak_part_reaches_far(self):
        arm = canvas((3000, 2400))
        draw = ImageDraw.Draw(arm)
        draw.rectangle((300, 300, 699, 699), fill=255)
        draw.rectangle((700, 480, 2300, 519), fill=60)   # matted weakly, joined to the object: the ring is 8% of both
        fitted, room = edit_plan.roomy(arm, "swap", 10.0)
        self.assertTrue(room)
        self.assertLess(largest_step(fitted), 96)

    def test_a_plate_with_a_soft_inside_is_filled_without_a_ring_around_it(self):
        plate = canvas(SMALL)   # the model returned the cookies on the plate at a third of full strength
        draw = ImageDraw.Draw(plate)
        draw.ellipse((60, 40, 180, 160), fill=255)
        draw.ellipse((75, 55, 165, 145), fill=90)
        disc = canvas(SMALL)
        ImageDraw.Draw(disc).ellipse((60, 40, 180, 160), fill=255)
        fitted, room = edit_plan.roomy(plate, "swap", 10.0)
        self.assertTrue(room)   # the new object got the inside the matte left weak
        self.assertEqual(fitted.getpixel((120, 100)), 255)
        self.assertLess(count(fitted, 128), 1.3 * count(disc, 255))   # no ring of 8% over the neighbours
        self.assertEqual(fitted.getpixel((120, 22)), 0)
        self.assertLess(largest_step(layer_mask(fitted)), 96)


class GrowthTests(unittest.TestCase):
    """The growth that moves Auto-feather's band off the object keeps out of what the artist left out."""

    def test_a_detail_left_out_of_the_object_stays_out(self):
        selection, detail = canvas(), canvas()
        ImageDraw.Draw(selection).rectangle((250, 150, 549, 449), fill=255)
        ImageDraw.Draw(selection).rectangle((380, 280, 419, 319), fill=0)
        ImageDraw.Draw(detail).rectangle((380, 280, 419, 319), fill=255)
        fitted, room = edit_plan.roomy(selection, "look", 10.0)
        self.assertFalse(room)
        self.assertEqual(inside(fitted, detail), 0)
        self.assertGreater(count(fitted, 128), count(selection, 128) * 1.05)   # the outside still grows

    def test_a_detail_left_out_of_a_feathered_selection_stays_out(self):
        selection, detail = canvas(), canvas()
        ImageDraw.Draw(selection).rectangle((250, 150, 549, 449), fill=255)
        ImageDraw.Draw(selection).rectangle((370, 250, 429, 349), fill=0)
        ImageDraw.Draw(detail).rectangle((370, 250, 429, 349), fill=255)
        selection = selection.filter(ImageFilter.GaussianBlur(12))   # the artist's feather reaches into the detail
        fitted, _room = edit_plan.roomy(selection, "look", 10.0)
        self.assertLessEqual(fitted.getpixel((400, 300)), selection.getpixel((400, 300)) + 4)
        self.assertLessEqual(mean(layer_mask(fitted), detail), mean(layer_mask(selection), detail) + 4)
        self.assertGreater(count(fitted, 128), count(selection, 128) * 1.05)   # the outside still grows

    def test_a_pinhole_in_the_matte_is_still_closed(self):
        selection = canvas()
        ImageDraw.Draw(selection).rectangle((250, 150, 549, 449), fill=255)
        ImageDraw.Draw(selection).rectangle((398, 298, 405, 305), fill=0)
        fitted, _room = edit_plan.roomy(selection, "look", 10.0)
        self.assertEqual(fitted.getpixel((401, 301)), 255)

    def test_a_weak_patch_inside_the_object_gets_the_whole_edit_in_a_recolour(self):
        selection = canvas()
        ImageDraw.Draw(selection).rectangle((250, 150, 549, 449), fill=255)
        ImageDraw.Draw(selection).rectangle((350, 250, 449, 349), fill=60)   # the model was unsure here
        fitted, _room = edit_plan.roomy(selection, "look", 10.0)
        self.assertEqual(fitted.getpixel((400, 300)), 255)

    def test_a_weak_patch_with_a_soft_rim_leaves_no_ring_in_a_small_object(self):
        selection = canvas(SMALL)   # little growth, so it cannot close the patch's rim by itself
        ImageDraw.Draw(selection).rectangle((70, 50, 169, 149), fill=255)
        ImageDraw.Draw(selection).rectangle((100, 80, 139, 119), fill=60)
        selection = selection.filter(ImageFilter.GaussianBlur(3))
        fitted, _room = edit_plan.roomy(selection, "look", 10.0)
        self.assertEqual(fitted.getpixel((120, 100)), 255)
        self.assertGreaterEqual(fitted.crop((82, 62, 158, 138)).getextrema()[0], 250)   # inside, away from its edge

    def test_a_neighbours_weak_matte_is_not_grown(self):
        selection, strip = canvas(), canvas()
        ImageDraw.Draw(selection).ellipse((150, 200, 350, 400), fill=255)
        ImageDraw.Draw(selection).rectangle((450, 150, 469, 449), fill=60)   # 100 px away: a cable the model half took
        ImageDraw.Draw(strip).rectangle((420, 0, 799, 599), fill=255)
        for kind in ("look", "swap"):
            fitted, _room = edit_plan.roomy(selection, kind, 10.0)
            self.assertEqual(inside(fitted, strip), inside(selection, strip), kind)
            self.assertGreater(count(fitted, 128), count(selection, 128) * 1.05, kind)   # the disc still grows

    def test_a_weak_fringe_of_an_old_object_is_replaced_whole_in_a_swap(self):
        selection = canvas()
        draw = ImageDraw.Draw(selection)
        draw.rectangle((250, 150, 449, 449), fill=255)
        draw.rectangle((450, 250, 549, 349), fill=60)   # a part the model matted weakly
        fitted, _room = edit_plan.roomy(selection, "swap", 10.0)
        self.assertEqual(fitted.getpixel((520, 300)), 255)
        recoloured, _room = edit_plan.roomy(selection, "look", 10.0)   # a recolour keeps the matte's own strength
        self.assertEqual(recoloured.getpixel((520, 300)), 60)

    def test_nothing_is_taken_away(self):
        soft = canvas(SMALL)
        ImageDraw.Draw(soft).ellipse((60, 40, 180, 160), fill=255)
        soft = soft.filter(ImageFilter.GaussianBlur(5))
        for kind in ("look", "swap"):
            fitted, _room = edit_plan.roomy(soft, kind, 10.0)
            self.assertIsNone(ImageChops.subtract(soft, fitted).getbbox(), kind)


class SoftEdgeTests(unittest.TestCase):
    """A soft selection keeps a soft edge in the result layer: no hard shape pasted over a soft matte."""

    def test_the_artists_feather_survives(self):
        disc = canvas(SMALL)
        ImageDraw.Draw(disc).ellipse((60, 40, 180, 160), fill=255)
        soft = disc.filter(ImageFilter.GaussianBlur(12))
        fitted, room = edit_plan.roomy(soft, "look", 10.0)
        self.assertFalse(room)
        self.assertGreater(count(fitted, 128), count(soft, 128) * 1.05)   # still grown
        self.assertLessEqual(largest_step(fitted), largest_step(soft) + 8)
        self.assertLessEqual(largest_step(layer_mask(fitted)), largest_step(layer_mask(soft)) + 4)

    def test_a_soft_selection_under_a_hull_has_no_cliff(self):
        bars = canvas((900, 700))   # a bicycle-like outline, feathered; this large so the feather is not just 2 pixels
        draw = ImageDraw.Draw(bars)
        draw.rectangle((200, 150, 230, 520), fill=255)
        draw.rectangle((200, 490, 700, 520), fill=255)
        fitted, room = edit_plan.roomy(bars.filter(ImageFilter.GaussianBlur(20)), "swap", 10.0)
        self.assertTrue(room)
        self.assertLess(largest_step(layer_mask(fitted)), 96)

    def test_a_matte_with_a_faint_halo_has_no_cliff(self):
        matte = canvas()   # a solid object with the model's faint halo around it
        ImageDraw.Draw(matte).ellipse((200, 100, 600, 500), fill=20)
        ImageDraw.Draw(matte).ellipse((250, 150, 550, 450), fill=255)
        matte = matte.filter(ImageFilter.GaussianBlur(1))
        for kind in ("look", "swap"):
            fitted, _room = edit_plan.roomy(matte, kind, 10.0)
            self.assertLess(largest_step(layer_mask(fitted)), 96, kind)


class FakeBridge:
    def __init__(self, chosen):
        self.profile = None
        self.chosen = chosen

    def _snapshot(self, image):
        return Image.new("RGBA", self.chosen.size), self.chosen


class AutomaticTests(unittest.TestCase):
    def test_replacing_the_background_leaves_the_subject_out(self):
        subject = canvas()
        ImageDraw.Draw(subject).ellipse((300, 70, 500, 530), fill=255)
        svc = Service.__new__(Service)
        svc.state = JobState(state="running")
        svc.bridge = FakeBridge(ImageChops.invert(subject))
        svc.layers, svc.image, svc._choice_kind = [], None, ""
        svc._operation_lock = threading.Lock()

        def generative_fill(image, prompt, negative, variations, strength, seed, model, remove=False):
            made = [PCLayer(None, "Generative Fill", Rect(0, 0, *PICTURE), Image.new("RGBA", PICTURE, "red").tobytes(),
                            None, True, "selection", 0, 0.0, None, None) for _ in range(variations)]
            image.pending, image.pending_group = made, ""
            image.layers.extend(made)
            return made

        app = Mock()
        app.settings = SimpleNamespace(remote_fibo=True, fibo_source="studio", hidden_models=[], auto_feather=True,
                                       auto_feather_percent=10.0)
        app.installed_models = lambda mode: [SimpleNamespace(id=i, name=i) for i in (QWEN21_LIGHTNING_ID, TURBO_ID, H3_IMAGE_ID)]
        app.generative_fill = generative_fill
        svc.app = svc._job_app = app
        image = PCImage(svc.bridge, "picture.png", 0)
        svc._edit_auto(image, app, "Replace the background with a brick wall.", "", None, 7, 1)
        self.assertIsNone(image.widened)
        self.assertNotIn(edit_plan.ROOM_NOTICE, svc.state.notices)


if __name__ == "__main__":
    unittest.main()
