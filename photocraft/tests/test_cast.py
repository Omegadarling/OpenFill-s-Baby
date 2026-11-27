# SPDX-License-Identifier: Apache-2.0
"""Remove takes the selected object's own shadow and reflection along, and nothing else."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import cast, tips, wevisone  # noqa: E402
from openfill_photocraft.bridge import PCImage, PhotoCraftBridge  # noqa: E402
from openfill_photocraft.local_removal import removal_mask  # noqa: E402
from openfill_photocraft.pieces import pieces  # noqa: E402
from openfill_photocraft.service import JobState, Service  # noqa: E402

from openfill.backend import BackendUnavailable, GenerationCancelled  # noqa: E402
from openfill.comfy_api import ComfyApiError  # noqa: E402
from openfill.geometry import Rect  # noqa: E402

SIZE = (400, 300)
OBJECT = (150, 80, 210, 160)        # a 60 x 80 object
SHADOW = (205, 150, 300, 175)       # to its right, touching it
ELSEWHERE = (20, 240, 90, 280)      # a dark thing that does not touch it
# Three light glasses in a row, lit from the left, each with a dark band to its right. Each band ends 5 pixels
# from the next glass, inside the touching reach of it (7 pixels for a glass 120 high).
GLASSES = [(60, 80, 100, 200), (160, 80, 200, 200), (260, 80, 300, 200)]
BANDS = [(101, 185, 155, 200), (201, 185, 255, 200), (301, 185, 355, 200)]


def middle(box):
    return ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)


def scaled(box, size):
    """``box`` (or a point) drawn for ``SIZE``, in a picture of ``size``."""
    scale = size[0] / SIZE[0]
    return tuple(round(value * scale) for value in box)


def picture(shadows=(SHADOW,), level=120):
    image = Image.new("RGB", SIZE, (200, 200, 200))
    draw = ImageDraw.Draw(image)
    for box in shadows:
        draw.rectangle(box, fill=(level,) * 3)
    draw.rectangle(OBJECT, fill=(190, 30, 30))
    return image


def matte(*boxes, size=SIZE):
    mask = Image.new("L", size)
    for box in boxes:
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def model(names=("red box",), grounds=None, casts=None):
    """``ask`` and ``segment`` that answer like the selection model: names in turn, then one matte list per description."""
    asked, described = [], []
    answers = iter(names)

    def ask(shown, question):
        asked.append((shown.size, question))
        return f"<ref>A {next(answers)}</ref><box><10><10><900><900><OBJ></box> on a grey floor."

    def segment(shown, description):
        described.append(description)
        if " of the " in description:
            kind = description.split(" ")[1]
            return [m.resize(shown.size) for m in (casts or {}).get(kind, [])]
        found = (grounds or {}).get(description, [matte(OBJECT)])
        return [m.resize(shown.size) for m in found]

    return ask, segment, asked, described


class RuleTests(unittest.TestCase):
    def measured(self, found, image=None):
        return cast.measure(image or picture(), matte(OBJECT), found)

    def test_the_name_is_the_phrase_the_model_grounds_first(self):
        self.assertEqual(cast.named("<ref>A sleek black pen</ref><box><1><2><3><4><OBJ></box> lies on a desk."), "sleek black pen")
        self.assertEqual(cast.named("The red rowboat. It floats on a lake."), "red rowboat")
        self.assertEqual(cast.named("<ref>an <b>odd</b>   thing.</ref>"), "odd thing")
        self.assertEqual(cast.named(""), "")
        self.assertLessEqual(len(cast.named("<ref>" + "very " * 40 + "long name</ref>")), 80)

    def test_a_name_counts_only_when_it_leads_back_to_the_selection(self):
        chosen = matte(OBJECT)
        self.assertTrue(cast.leads_back([matte(OBJECT)], chosen))
        self.assertTrue(cast.leads_back([matte((150, 80, 210, 140))], chosen))      # most of it
        self.assertFalse(cast.leads_back([matte((0, 0, 399, 299))], chosen))        # the whole picture
        self.assertFalse(cast.leads_back([matte(ELSEWHERE)], chosen))               # something else
        self.assertFalse(cast.leads_back([Image.new("L", SIZE)], chosen))
        self.assertFalse(cast.leads_back([], chosen))

    def test_a_name_shared_with_a_neighbour_still_leads_back(self):
        # "the man" matches both men; the selected one is among the matches.
        chosen = matte(OBJECT)
        self.assertTrue(cast.leads_back([matte(ELSEWHERE), matte(OBJECT)], chosen))
        self.assertFalse(cast.leads_back([matte(ELSEWHERE), matte((230, 80, 290, 160))], chosen))  # two others
        halves = [matte((150, 80, 210, 120)), matte((150, 121, 210, 160))]   # a selection of several things
        self.assertTrue(cast.leads_back(halves, chosen))

    def test_a_dark_patch_that_touches_the_object_is_taken(self):
        found = self.measured(matte(SHADOW))
        self.assertGreater(found["touch"], cast.TOUCH)
        self.assertGreater(found["darker"], 50)
        self.assertEqual([kind for kind, _ in cast.accepted({"shadow": [found]})], ["shadow"])

    def test_the_object_again_is_not_a_find(self):
        self.assertIsNone(self.measured(matte(OBJECT)))
        self.assertIsNone(self.measured(matte((140, 70, 220, 170))))  # the object with a rim
        self.assertIsNone(self.measured(Image.new("L", SIZE)))

    def test_what_does_not_touch_is_someone_elses(self):
        image = picture(shadows=(SHADOW, ELSEWHERE))
        self.assertEqual(cast.accepted({"shadow": [self.measured(matte(ELSEWHERE), image)]}), [])

    def test_what_is_not_darker_than_the_surface_is_not_a_shadow(self):
        plain = picture(shadows=())
        self.assertEqual(cast.accepted({"shadow": [self.measured(matte(SHADOW), plain)]}), [])
        brighter = picture(level=250)
        self.assertEqual(cast.accepted({"reflection": [self.measured(matte(SHADOW), brighter)]}), [])

    def reflected(self, box, colour, floor=(20, 30, 20)):
        """A red object on a dark floor with a patch of ``colour`` at ``box``."""
        image = Image.new("RGB", SIZE, floor)
        draw = ImageDraw.Draw(image)
        draw.rectangle(box, fill=colour)
        draw.rectangle(OBJECT, fill=(190, 30, 30))
        return image

    def test_a_reflection_brighter_than_the_water_is_taken_for_its_colour(self):
        below = (150, 161, 210, 230)
        found = self.measured(matte(below), self.reflected(below, (150, 40, 40)))   # a dimmer image of the object, on dark water
        self.assertLess(found["darker"], 0)
        self.assertLess(found["unlike"], cast.RESEMBLE)
        self.assertTrue(found["under"])
        self.assertEqual([kind for kind, _ in cast.accepted({"reflection": [found]})], ["reflection"])

    def test_a_reflection_may_start_a_little_below_the_object(self):
        apart = (150, 185, 210, 250)   # a bank between the cabin and the lake
        found = self.measured(matte(apart), self.reflected(apart, (150, 40, 40)))
        self.assertEqual(found["touch"], 0)
        self.assertEqual([kind for kind, _ in cast.accepted({"reflection": [found]})], ["reflection"])
        far = (150, 240, 210, 290)     # too far below to be its mirror image
        self.assertEqual(cast.accepted({"reflection": [self.measured(matte(far), self.reflected(far, (150, 40, 40)))]}), [])

    def test_a_bright_patch_that_is_not_the_objects_colour_or_not_under_it_is_left(self):
        below = (150, 161, 210, 230)
        other = self.measured(matte(below), self.reflected(below, (60, 160, 220)))   # bright, but not its colours
        self.assertEqual(cast.accepted({"reflection": [other]}), [])
        beside = (211, 80, 280, 160)   # a look-alike standing next to it
        twin = self.measured(matte(beside), self.reflected(beside, (190, 30, 30)))
        self.assertFalse(twin["under"])
        self.assertEqual(cast.accepted({"reflection": [twin]}), [])

    def test_a_find_larger_than_the_object_needs_both_names_on_one_place(self):
        big = (205, 80, 300, 190)   # about 2.2 times the object
        image = picture(shadows=(big,))
        alone = {"shadow": [self.measured(matte(big), image)]}
        self.assertGreater(alone["shadow"][0]["area"], cast.LARGE)
        self.assertEqual(cast.accepted(alone), [])
        both = {"shadow": [self.measured(matte(big), image)], "reflection": [self.measured(matte(big), image)]}
        self.assertEqual([kind for kind, _ in cast.accepted(both)], ["shadow", "reflection"])
        apart = {"shadow": [self.measured(matte(big), image)], "reflection": [self.measured(matte(SHADOW), image)]}
        self.assertEqual([kind for kind, _ in cast.accepted(apart)], ["reflection"])

    def test_nothing_over_three_times_the_object_or_a_quarter_of_the_picture_is_taken(self):
        huge = (205, 20, 399, 299)
        image = picture(shadows=(huge,))
        both = {"shadow": [self.measured(matte(huge), image)], "reflection": [self.measured(matte(huge), image)]}
        self.assertGreater(both["shadow"][0]["area"], cast.LARGEST)
        self.assertEqual(cast.accepted(both), [])

    def test_a_dark_area_on_both_sides_is_a_shadow_the_object_stands_in(self):
        # A cone pasted in front of a motorcycle got the motorcycle's floor shadow, which runs out on both sides of it.
        band = (100, 150, 260, 175)
        found = self.measured(matte(band), picture(shadows=(band,)))
        self.assertGreater(found["touch"], cast.TOUCH)
        self.assertGreater(found["darker"], 50)
        self.assertGreater(min(found["left"], found["right"]), cast.BOTH_SIDES)
        self.assertEqual(cast.accepted({"shadow": [found], "reflection": [found]}), [])
        spill = (146, 150, 300, 175)   # its own shadow, starting a few pixels left of the object
        own = self.measured(matte(spill), picture(shadows=(spill,)))
        self.assertLess(min(own["left"], own["right"]), cast.BOTH_SIDES)
        self.assertEqual([kind for kind, _ in cast.accepted({"shadow": [own]})], ["shadow"])

    def test_under_a_dark_object_a_band_on_both_sides_does_not_pass_as_its_reflection(self):
        # The floor shadow a dark object stands in has nearly the object's colours and lies under it, so the
        # reflection rule alone would take it, asked for either.
        band, below = (100, 150, 260, 175), (150, 161, 210, 230)
        image = picture(shadows=(band,))
        ImageDraw.Draw(image).rectangle(OBJECT, fill=(60, 60, 70))
        found = self.measured(matte(band), image)
        self.assertTrue(cast.mirrored(found))
        self.assertLess(found["unlike"], cast.RESEMBLE - 0.1)   # far from the colour margin: this pins the rule
        self.assertGreater(min(found["left"], found["right"]), cast.BOTH_SIDES)
        self.assertGreater(min(found["far_left"], found["far_right"]), cast.BOTH_SIDES)   # and far past a reflection's margin
        self.assertEqual(cast.accepted({"shadow": [found]}), [])
        self.assertEqual(cast.accepted({"reflection": [found]}), [])
        self.assertEqual(cast.accepted({"shadow": [found], "reflection": [found]}), [])
        mirror = Image.new("RGB", SIZE, (200, 200, 200))
        ImageDraw.Draw(mirror).rectangle(below, fill=(80, 80, 90))
        ImageDraw.Draw(mirror).rectangle(OBJECT, fill=(60, 60, 70))
        own = self.measured(matte(below), mirror)   # its own reflection, straight under it, is still taken
        self.assertTrue(cast.mirrored(own))
        self.assertEqual([kind for kind, _ in cast.accepted({"reflection": [own]})], ["reflection"])

    def test_a_reflection_a_little_wider_than_its_object_is_still_its_own(self):
        # A reflection has straight sides, so part of a matte a few pixels wider than its object lies wholly left
        # and wholly right of the object's box; only what runs past the touching reach counts against it.
        below = (143, 161, 217, 230)   # 7 pixels wider on each side
        found = self.measured(matte(below), self.reflected(below, (150, 40, 40)))
        self.assertTrue(cast.mirrored(found))
        self.assertGreaterEqual(min(found["left"], found["right"]), cast.BOTH_SIDES)
        self.assertLess(min(found["far_left"], found["far_right"]), cast.BOTH_SIDES)
        self.assertEqual([kind for kind, _ in cast.accepted({"reflection": [found]})], ["reflection"])
        # Measurements made without the far shares are judged by the box, as they were before those were added.
        older = {key: value for key, value in found.items() if not key.startswith("far_")}
        self.assertEqual(cast.accepted({"reflection": [older]}), [])

    def test_a_shadow_a_little_wider_than_its_object_on_both_sides_is_still_turned_down(self):
        # The margin is for a reflection's straight sides only. The cone pasted in front of a motorcycle had 0.10
        # of the motorcycle's shadow wholly on each side of its box, but only 0.03 past the margin.
        band = (145, 150, 215, 175)   # 5 pixels past the object on each side
        image = picture(shadows=(band,))
        ImageDraw.Draw(image).rectangle(OBJECT, fill=(230, 200, 40))
        found = self.measured(matte(band), image)
        self.assertGreater(found["touch"], cast.TOUCH)
        self.assertGreater(found["darker"], 50)
        self.assertGreater(found["unlike"], cast.RESEMBLE + 0.1)   # a yellow object, far from the floor's grey: no reflection
        self.assertGreaterEqual(min(found["left"], found["right"]), cast.BOTH_SIDES)
        self.assertLess(min(found["far_left"], found["far_right"]), cast.BOTH_SIDES)
        self.assertEqual(cast.accepted({"shadow": [found], "reflection": [found]}), [])

    def test_mirrored_is_under_the_object_in_its_colours(self):
        self.assertTrue(cast.mirrored({"under": True, "unlike": cast.RESEMBLE}))
        self.assertFalse(cast.mirrored({"under": True, "unlike": cast.RESEMBLE + 0.01}))
        self.assertFalse(cast.mirrored({"under": False, "unlike": 0.0}))
        self.assertFalse(cast.mirrored({"under": True, "unlike": float("inf")}))   # an object the colour of its surroundings

    def test_a_name_leads_back_through_a_piece_of_one_matte(self):
        # "the green bottle" came back as one matte holding three bottles that look alike.
        chosen = matte(OBJECT)
        twins = matte((20, 80, 80, 160), OBJECT, (280, 80, 340, 160))
        self.assertTrue(cast.leads_back([twins], chosen))
        numbers = cast.back([twins], chosen)
        self.assertEqual(numbers["each"][0]["counted"], "pieces")
        self.assertLess(numbers["each"][0]["inside"], cast.LEADS_BACK)
        self.assertGreater(numbers["covered"], 0.9)
        self.assertFalse(cast.leads_back([matte((20, 80, 80, 160), (100, 40, 260, 200))], chosen))   # the piece on it is mostly outside
        self.assertEqual(cast.back([matte(OBJECT)], chosen)["each"][0]["counted"], "whole")

    def test_a_piece_of_a_find_goes_to_the_look_alike_it_reaches_first(self):
        mine, theirs = matte((100, 100, 139, 199)), matte((200, 100, 239, 199))   # the selected look-alike, another
        alikes = (mine, [cast.Alike(theirs, theirs, False)], 10)
        self.assertFalse(cast._nearer_another(matte((140, 150, 160, 160)), alikes))   # beside the selected one
        self.assertTrue(cast._nearer_another(matte((180, 150, 199, 160)), alikes))    # beside the other
        self.assertTrue(cast._nearer_another(matte((185, 150, 205, 160)), alikes))    # over a little of the other
        self.assertFalse(cast._nearer_another(matte((145, 150, 194, 160)), alikes))   # as near both: left to the selected one
        self.assertFalse(cast._nearer_another(matte((141, 150, 199, 160)), alikes))   # a pixel nearer the other is as near (CLOSER)
        self.assertTrue(cast._nearer_another(matte((142, 150, 199, 160)), alikes))    # two pixels nearer is nearer
        self.assertFalse(cast._nearer_another(matte((150, 260, 190, 280)), alikes))   # out of reach of both: left to it too
        self.assertFalse(cast._nearer_another(Image.new("L", SIZE), alikes))
        # A piece the other look-alike lies mostly in would take it along, whatever the piece meets first.
        self.assertTrue(cast._nearer_another(matte((200, 100, 239, 199)), alikes))
        self.assertTrue(cast._nearer_another(matte((135, 95, 245, 205)), alikes))     # over the selected one too
        self.assertTrue(cast._nearer_another(matte((205, 120, 230, 180)), alikes))    # and one mostly in it is nearer it
        # Only where the selected one's mirror image would lie is a look-alike in the piece, or the piece mostly in
        # it, the find itself: the model put the object's image into the matte for its name.
        image = matte((100, 205, 139, 260))
        mirrored = (mine, [cast.Alike(theirs, theirs, False), cast.Alike(image, image, True)], 10)
        self.assertFalse(cast._nearer_another(matte((100, 205, 139, 260)), mirrored))
        self.assertFalse(cast._nearer_another(matte((100, 205, 139, 290)), mirrored))   # the image and more below it
        self.assertFalse(cast._nearer_another(matte((105, 210, 130, 240)), mirrored))   # a part of the image
        self.assertTrue(cast._nearer_another(matte((100, 205, 139, 260)), (mine, [cast.Alike(image, image, False)], 10)))

    def test_only_a_look_alike_where_the_selected_ones_mirror_image_lies_can_be_the_find_itself(self):
        box = (100, 100, 140, 200)   # 40 x 100
        self.assertTrue(cast._mirror_place(box, (100, 201, 140, 260)))    # right under it
        self.assertTrue(cast._mirror_place(box, (100, 150, 140, 260)))    # from its lower half
        self.assertTrue(cast._mirror_place(box, (100, 250, 140, 300)))    # half its height below it (GAP)
        self.assertTrue(cast._mirror_place(box, (120, 201, 160, 260)))    # half of it in line (INLINE)
        self.assertFalse(cast._mirror_place(box, (100, 251, 140, 300)))   # further down
        self.assertFalse(cast._mirror_place(box, (100, 149, 140, 260)))   # from its upper half
        self.assertFalse(cast._mirror_place(box, (150, 100, 190, 200)))   # beside it
        self.assertFalse(cast._mirror_place(box, (121, 201, 161, 260)))   # under it, less than half in line
        # "the rider": the selected rider, his image in a puddle and another rider beside him, in one matte.
        chosen = matte((100, 40, 140, 140))
        named = matte((100, 40, 140, 140), (100, 148, 140, 250), (200, 40, 240, 140))
        _mine, others, _reach = cast._look_alikes([named], cast.back([named], chosen)["each"], chosen, chosen)
        self.assertEqual(sorted((other.whole.getbbox(), other.mirror) for other in others),
                         [((100, 148, 141, 251), True), ((200, 40, 241, 141), False)])

    def test_a_speck_of_the_matte_for_the_name_is_no_look_alike(self):
        # Of the other pieces of the matte for the name, only those from 2% of the selection's size up are
        # look-alikes (SPECK). A 6 x 6 speck of the model's lying in the selected glass's own shadow counted as
        # one and handed that shadow away.
        chosen = matte(GLASSES[1])                                     # 41 x 121 pixels: 2% is 99.2
        speck, over, under = (220, 190, 225, 195), (330, 250, 339, 259), (20, 250, 28, 260)   # 36, 100 and 99 pixels
        named = matte(*GLASSES, speck, over, under)
        _mine, others, _reach = cast._look_alikes([named], cast.back([named], chosen)["each"], chosen, chosen)
        self.assertEqual(sorted(other.whole.getbbox() for other in others),
                         [(60, 80, 101, 201), (260, 80, 301, 201), (330, 250, 340, 260)])
        # With only specks besides the selected one there are no others at all.
        named = matte(GLASSES[1], speck)
        self.assertIsNone(cast._look_alikes([named], cast.back([named], chosen)["each"], chosen, chosen))

    def test_a_look_alike_whose_box_misses_the_piece_is_not_counted_over_the_whole_picture(self):
        # 26 glasses on a shelf with 24 dark specks under the selected one took 3 s a find: how much of each
        # look-alike lay in each piece was counted over the whole picture. A look-alike keeps its box and size,
        # and what lies in a piece is counted only where their boxes meet.
        size = (1536, 1024)
        mine = matte((600, 100, 660, 400), size=size)
        theirs = [matte((left, 100, left + 60, 400), size=size) for left in (10, 127, 244, 1000, 1117)]
        alikes = (mine, [cast.Alike(other, other, False) for other in theirs], 18)
        with patch.object(ImageChops, "darker", wraps=ImageChops.darker) as darker:
            self.assertFalse(cast._nearer_another(matte((610, 402, 614, 405), size=size), alikes))
        self.assertEqual([call.args[0].size for call in darker.call_args_list if call.args[0].size == size], [])
        first = alikes[1][0]
        self.assertEqual((first.box, first.size, first.small_box), ((10, 100, 71, 401), 61 * 301, (10, 100, 71, 401)))

    def test_the_notice_says_what_went_and_how_to_keep_it(self):
        self.assertEqual(cast.notice("shadow"), "The object's shadow was removed too. Untick “With its shadow and reflection” to keep it.")
        self.assertIn("shadow and reflection were removed too", cast.notice("shadow and reflection"))
        self.assertIn("to keep them", cast.notice("shadow and reflection"))
        self.assertIn("shadow or reflection was removed too", cast.notice("shadow or reflection"))

    def test_both_panels_offer_the_option_with_a_tip(self):
        page = (ROOT / "photocraft/openfill_photocraft/static/index.html").read_text(encoding="utf-8")
        self.assertIn(f"<span>{cast.OPTION}</span>", page)
        self.assertIn('id="remove-cast" checked', page)
        self.assertIn('b.cast=$("remove-cast").checked', page)
        self.assertIn("shadow and reflection", tips.CONTROLS["remove.cast"])
        bar = (ROOT / "photocraft/openfill_photocraft/bar.py").read_text(encoding="utf-8")
        self.assertIn("cast=self.remove_cast.get()", bar)
        self.assertIn('self.prefs.get("removeCast", True)', bar)


class FindTests(unittest.TestCase):
    def test_the_objects_shadow_is_added_and_named(self):
        ask, segment, asked, described = model(casts={"shadow": [matte(SHADOW)]})
        found = cast.find(picture(), matte(OBJECT), ask, segment)
        self.assertEqual((found.words, found.name), ("shadow", "red box"))
        self.assertEqual(found.mask.size, SIZE)
        self.assertEqual(found.mask.getpixel((250, 162)), 255)
        self.assertEqual(found.mask.getpixel((50, 50)), 0)
        self.assertEqual(found.mask.getpixel((50, 260)), 0)
        self.assertEqual(described, ["the red box", "the shadow of the red box", "the reflection of the red box"])
        self.assertEqual(len(asked), 1)

    def test_a_shadow_and_a_reflection_in_two_places_are_both_added(self):
        below = (150, 158, 210, 200)
        ask, segment, _asked, _described = model(casts={"shadow": [matte(SHADOW)], "reflection": [matte(below)]})
        found = cast.find(picture(shadows=(SHADOW, below)), matte(OBJECT), ask, segment)
        self.assertEqual(found.words, "shadow and reflection")
        self.assertEqual(found.mask.getpixel((250, 162)), 255)
        self.assertEqual(found.mask.getpixel((180, 190)), 255)

    def test_one_place_under_both_names_is_a_shadow_or_reflection(self):
        ask, segment, _asked, _described = model(casts={"shadow": [matte(SHADOW)], "reflection": [matte(SHADOW)]})
        self.assertEqual(cast.find(picture(), matte(OBJECT), ask, segment).words, "shadow or reflection")

    def test_another_objects_shadow_is_left_alone(self):
        ask, segment, _asked, _described = model(casts={"shadow": [matte(ELSEWHERE)]})
        found = cast.find(picture(shadows=(ELSEWHERE,)), matte(OBJECT), ask, segment)
        self.assertIsNone(found.mask)
        self.assertEqual((found.words, found.name), ("", "red box"))

    def test_a_name_that_leads_elsewhere_gives_way_to_the_object_alone_on_grey(self):
        # A hose crosses the picture, and the crop's "main object" is the ball beside it.
        ask, segment, asked, described = model(names=("soccer ball", "yellow hose"), grounds={"the soccer ball": [matte(ELSEWHERE)]},
                                               casts={"shadow": [matte(SHADOW)]})
        found = cast.find(picture(), matte(OBJECT), ask, segment)
        self.assertEqual((found.name, found.words), ("yellow hose", "shadow"))
        self.assertEqual(described[:2], ["the soccer ball", "the yellow hose"])
        self.assertEqual(len(asked), 2)

    def test_no_name_that_leads_back_means_nothing_is_added(self):
        ask, segment, _asked, described = model(names=("mountain", "wooden pole"),
                                                grounds={"the mountain": [matte(ELSEWHERE)], "the wooden pole": [matte(ELSEWHERE)]},
                                                casts={"shadow": [matte(SHADOW)]})
        found = cast.find(picture(), matte(OBJECT), ask, segment)
        self.assertEqual((found.mask, found.words, found.name), (None, "", ""))
        self.assertEqual(described, ["the mountain", "the wooden pole"])  # the shadow is never asked for

    def test_the_whole_picture_or_no_selection_asks_nothing(self):
        ask, segment, asked, _described = model()
        self.assertIsNone(cast.find(picture(), Image.new("L", SIZE, 255), ask, segment).mask)
        self.assertIsNone(cast.find(picture(), Image.new("L", SIZE), ask, segment).mask)
        self.assertEqual(asked, [])

    def test_a_small_object_is_named_from_the_full_size_picture(self):
        large = picture().resize((4000, 3000))
        chosen = matte(OBJECT).resize((4000, 3000))
        ask, segment, asked, _described = model(casts={"shadow": [matte(SHADOW)]})
        found = cast.find(large, chosen, ask, segment)
        self.assertEqual(found.mask.size, (4000, 3000))
        self.assertGreater(max(asked[0][0]), 800)   # the crop keeps the object's own pixels
        self.assertLessEqual(max(asked[0][0]), 1536)

    def test_a_cancel_stops_the_questions(self):
        ask, segment, asked, described = model(casts={"shadow": [matte(SHADOW)]})
        self.assertIsNone(cast.find(picture(), matte(OBJECT), ask, segment, cancelled=lambda: True).mask)
        self.assertEqual((asked, described), ([], []))

    def test_a_part_found_with_less_confidence_goes_whole_and_the_models_haze_not_at_all(self):
        far, haze = (301, 150, 345, 175), (100, 100, 399, 260)
        unsure = Image.new("L", SIZE)
        draw = ImageDraw.Draw(unsure)
        draw.rectangle(haze, fill=20)     # the model's faint doubt around the shadow
        draw.rectangle(far, fill=107)     # the far end of the shadow, found with less confidence
        draw.rectangle(SHADOW, fill=255)
        ask, segment, _asked, _described = model(casts={"shadow": [unsure]})
        found = cast.find(picture(shadows=(SHADOW, far)), matte(OBJECT), ask, segment)
        self.assertEqual(found.words, "shadow")
        self.assertEqual(found.mask.getpixel((323, 162)), 255)   # at 107 the repair is laid half-transparent: a ghost
        self.assertEqual(found.mask.getpixel((120, 240)), 0)
        layer = removal_mask(found.mask)
        self.assertLess(sum(layer.histogram()[1:]) / (SIZE[0] * SIZE[1]), 0.15)   # FLUX 3 and LaMa redo every pixel above 0

    def test_three_look_alikes_in_one_matte_still_name_the_selected_one(self):
        twins = matte((20, 80, 80, 160), OBJECT, (280, 80, 340, 160))
        ask, segment, _asked, described = model(grounds={"the red box": [twins]}, casts={"shadow": [matte(SHADOW)]})
        found = cast.find(picture(), matte(OBJECT), ask, segment)
        self.assertEqual((found.name, found.words), ("red box", "shadow"))
        self.assertIn("the shadow of the red box", described)

    def test_the_object_standing_in_another_shadow_takes_none_of_it(self):
        band = (100, 150, 260, 175)
        ask, segment, _asked, _described = model(casts={"shadow": [matte(band)]})
        found = cast.find(picture(shadows=(band,)), matte(OBJECT), ask, segment)
        self.assertEqual((found.mask, found.name), (None, "red box"))

    def test_a_dark_object_standing_in_another_shadow_takes_none_of_it_either(self):
        band = (100, 150, 260, 175)
        image = picture(shadows=(band,))
        ImageDraw.Draw(image).rectangle(OBJECT, fill=(60, 60, 70))
        ask, segment, _asked, _described = model(casts={"shadow": [matte(band)], "reflection": [matte(band)]})
        found = cast.find(image, matte(OBJECT), ask, segment)
        self.assertEqual((found.mask, found.name), (None, "red box"))

    def test_a_narrow_posts_reflection_a_little_wider_than_the_post_is_taken(self):
        # A post 30 pixels wide in dark water, and the model's reflection matte 3 pixels wider on each side. At the
        # working size that is one pixel past a 10-pixel post, which, counted from the post's box, ran out on
        # both sides, so the reflection stayed.
        size = (1536, 1024)
        post, mirror, matted = (700, 200, 729, 600), (700, 601, 729, 900), (697, 601, 732, 900)
        water = Image.new("RGB", size, (20, 40, 60))
        draw = ImageDraw.Draw(water)
        draw.rectangle(mirror, fill=(140, 110, 80))
        draw.rectangle(post, fill=(200, 160, 110))
        answers = []
        ask, segment, _asked, _described = model(names=("wooden post",), grounds={"the wooden post": [matte(post, size=size)]},
                                                 casts={"reflection": [matte(matted, size=size)]})
        found = cast.find(water, matte(post, size=size), ask, segment, answers=answers)
        self.assertEqual((found.name, found.words), ("wooden post", "reflection"))
        self.assertEqual(found.mask.getpixel((714, 750)), 255)
        measured = answers[-1]["measure"]
        self.assertGreaterEqual(min(measured["left"], measured["right"]), cast.BOTH_SIDES)
        self.assertLess(min(measured["far_left"], measured["far_right"]), cast.BOTH_SIDES)

    def glasses(self, selected, bands=(0, 1, 2), glasses=GLASSES, shadows=BANDS, named=None):
        """``find`` for glass number ``selected`` of ``glasses``, with the dark ``shadows`` drawn over them. The
        model answers the name with one matte for all the glasses (or for the boxes ``named``) and the shadow
        with one matte for the ``bands`` of ``shadows`` it found."""
        image = Image.new("RGB", SIZE, (150, 140, 130))
        draw = ImageDraw.Draw(image)
        for box in glasses:
            draw.rectangle(box, fill=(220, 230, 240))
        for box in shadows:
            draw.rectangle(box, fill=(80, 75, 70))
        answers = []
        ask, segment, _asked, _described = model(names=("drinking glass",), grounds={"the drinking glass": [matte(*(named or glasses))]},
                                                 casts={"shadow": [matte(*[shadows[number] for number in bands])]})
        return cast.find(image, matte(glasses[selected]), ask, segment, answers=answers), answers

    def test_look_alikes_in_one_matte_give_only_the_selected_ones_shadow(self):
        # The leftmost glass selected. The other glasses stay, so their bands must too.
        found, answers = self.glasses(0)
        self.assertEqual((found.name, found.words), ("drinking glass", "shadow"))
        self.assertEqual(found.mask.getpixel((128, 192)), 255)   # its own band
        self.assertEqual(found.mask.getpixel((228, 192)), 0)     # the middle glass's
        self.assertEqual(found.mask.getpixel((328, 192)), 0)     # the right glass's
        self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["kept"]), ("pieces", 1, True))

    def test_each_look_alike_gets_its_own_shadow_and_not_its_neighbours(self):
        # The middle and the right glass: the band of the glass to the left ends inside the touching reach, and
        # judged together with the glass's own band the two ran out on both sides of it, so it got no shadow.
        for selected in (1, 2):
            found, answers = self.glasses(selected)
            self.assertEqual((found.name, found.words), ("drinking glass", "shadow"))
            for number, band in enumerate(BANDS):
                self.assertEqual(found.mask.getpixel(middle(band)), 255 if number == selected else 0, (selected, number))
            self.assertEqual((answers[1]["pieces"], answers[1]["theirs"], answers[1]["kept"]), (1, 1, True))

    def test_a_look_alike_whose_own_shadow_was_not_found_takes_none_of_its_neighbours(self):
        # The right glass, with the model's shadow matte holding only the other two bands. The middle glass's band
        # reaches it and was taken as its shadow, so the middle glass stayed without its shadow.
        found, answers = self.glasses(2, bands=(0, 1))
        self.assertEqual((found.mask, found.words, found.name), (None, "", "drinking glass"))
        self.assertEqual((answers[1]["pieces"], answers[1]["theirs"], answers[1]["measure"]), (0, 1, None))

    def test_a_look_alikes_own_shadow_running_onto_the_next_ones_foot_is_still_taken(self):
        # The middle glass's shadow touches it and runs over the foot of the glass to its right. Lying over that
        # glass counted as nearer it than touching its own glass a pixel further on, so the shadow stayed.
        glasses = [(60, 80, 100, 200), (160, 80, 200, 200), (240, 80, 280, 200)]
        band = (201, 185, 250, 200)
        found, answers = self.glasses(1, bands=(0,), glasses=glasses, shadows=[band])
        self.assertEqual(found.words, "shadow")
        self.assertEqual(found.mask.getpixel((220, 192)), 255)
        self.assertEqual((answers[1]["pieces"], answers[1]["theirs"]), (1, 0))

    def test_a_look_alikes_own_shadow_is_taken_when_the_models_piece_of_it_misses_its_foot(self):
        # The model's piece of the middle glass stops 50 pixels above the glass's foot, where its shadow lies,
        # and the right glass is 5 pixels from that shadow. Measured from the model's piece alone, the shadow
        # went to the right glass; the artist's selection of the middle glass reaches it first.
        found, answers = self.glasses(1, bands=(1,), named=[GLASSES[0], (160, 80, 200, 150), GLASSES[2]])
        self.assertEqual(found.words, "shadow")
        self.assertEqual(found.mask.getpixel(middle(BANDS[1])), 255)
        self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["theirs"]), ("pieces", 1, 0))

    def test_a_look_alikes_own_reflection_in_the_matte_for_its_name_is_still_taken(self):
        # "the rider" came back as one matte holding the rider and his image in the puddle, so the name led back
        # through a piece of it. That image is the reflection itself and no neighbour of his, but it was taken
        # for a look-alike the reflection lay on, so the reflection stayed.
        rider, image = (150, 40, 190, 140), (150, 148, 190, 250)
        puddle = Image.new("RGB", SIZE, (20, 30, 40))
        draw = ImageDraw.Draw(puddle)
        draw.rectangle(rider, fill=(190, 30, 30))
        draw.rectangle(image, fill=(150, 40, 40))
        answers = []
        ask, segment, _asked, _described = model(names=("rider",), grounds={"the rider": [matte(rider, image)]},
                                                 casts={"reflection": [matte(image)]})
        found = cast.find(puddle, matte(rider), ask, segment, answers=answers)
        self.assertEqual(found.words, "reflection")
        self.assertEqual(found.mask.getpixel(middle(image)), 255)
        self.assertEqual((answers[0]["each"][0]["counted"], answers[-1]["pieces"], answers[-1]["theirs"]), ("pieces", 1, 0))

    def test_a_shadow_piece_holding_a_neighbouring_look_alike_is_left_with_it(self):
        # Three dark bottles, the middle one selected and the right one 8 pixels from it. The model's shadow matte is
        # one piece: the right bottle with its contact pool, which reaches 4 pixels past its left side, and in the
        # last two also a sliver at the selected bottle's foot that joins the pool to it, a pixel off it or on it.
        # The right bottle lay mostly in the piece, so it counted as the find itself (or the piece touched its own
        # bottle too soon after it), and Remove took the right bottle along with the selected one.
        bottles = [(60, 80, 100, 200), (160, 80, 200, 200), (209, 80, 249, 200)]
        pool = (204, 196, 262, 203)
        for sliver in ((), ((201, 196, 203, 203),), ((200, 196, 203, 203),)):
            image = Image.new("RGB", SIZE, (200, 195, 190))
            draw = ImageDraw.Draw(image)
            for box in (pool, *sliver):
                draw.rectangle(box, fill=(90, 85, 80))
            for box in bottles:
                draw.rectangle(box, fill=(40, 70, 40))
            answers = []
            ask, segment, _asked, _described = model(names=("green bottle",), grounds={"the green bottle": [matte(*bottles)]},
                                                     casts={"shadow": [matte(bottles[2], pool, *sliver)]})
            found = cast.find(image, matte(bottles[1]), ask, segment, answers=answers)
            self.assertEqual((found.mask, found.words, found.name), (None, "", "green bottle"), sliver)
            self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["theirs"]), ("pieces", 0, 1), sliver)

    def test_a_thin_contact_line_at_a_neighbours_foot_is_left_to_the_neighbour(self):
        # Three light glasses in a 1536 x 1024 picture (working size 512), the middle one selected. The model's one
        # shadow matte holds its own band and the right glass's contact line, 3 pixels tall, which runs past that
        # glass's left side into the selected one's touching reach. Shrunk to the working size the line overlapped
        # the right glass by half, though it lies wholly below it, and was taken as the selected glass's shadow:
        # under the right glass, or, with the band on the far side, the two ran out on both sides and neither went.
        size = (1536, 1024)
        glasses = [(180, 240, 300, 600), (480, 240, 600, 600), (640, 240, 760, 600)]
        line = (618, 601, 770, 603)
        for band in ((601, 560, 608, 600), (400, 560, 479, 600)):
            image = Image.new("RGB", size, (150, 140, 130))
            draw = ImageDraw.Draw(image)
            for box in glasses:
                draw.rectangle(box, fill=(220, 230, 240))
            for box in (band, line):
                draw.rectangle(box, fill=(60, 55, 50))
            answers = []
            ask, segment, _asked, _described = model(names=("drinking glass",), grounds={"the drinking glass": [matte(*glasses, size=size)]},
                                                     casts={"shadow": [matte(band, line, size=size)]})
            found = cast.find(image, matte(glasses[1], size=size), ask, segment, answers=answers)
            self.assertEqual(found.words, "shadow", band)
            self.assertEqual(found.mask.getpixel(middle(band)), 255, band)
            self.assertEqual(found.mask.getpixel((700, 602)), 0, band)
            self.assertEqual((answers[1]["pieces"], answers[1]["theirs"]), (1, 1), band)

    def test_a_speck_of_the_matte_for_the_name_in_the_objects_own_shadow_leaves_the_shadow_to_it(self):
        # The middle glass's own band runs over its foot, and the model's matte for "the drinking glass" holds a
        # speck lying in that band, 6 x 6 or 4 x 4 pixels. The speck counted as a look-alike lying mostly in the
        # band, so the band went to it and the glass's shadow stayed. The same at 1536 x 1152 (working size 512).
        band = (190, 185, 240, 200)
        for size in (SIZE, (1536, 1152)):
            glasses = [scaled(box, size) for box in GLASSES]
            for speck in ((220, 190, 225, 195), (230, 192, 233, 195)):
                image = Image.new("RGB", size, (150, 140, 130))
                draw = ImageDraw.Draw(image)
                for box in glasses:
                    draw.rectangle(box, fill=(220, 230, 240))
                draw.rectangle(scaled(band, size), fill=(80, 75, 70))
                answers = []
                ask, segment, _asked, _described = model(
                    names=("drinking glass",), grounds={"the drinking glass": [matte(*glasses, scaled(speck, size), size=size)]},
                    casts={"shadow": [matte(scaled(band, size), size=size)]})
                found = cast.find(image, matte(glasses[1], size=size), ask, segment, answers=answers)
                self.assertEqual(found.words, "shadow", (size, speck))
                self.assertEqual(found.mask.getpixel(scaled((236, 196), size)), 255, (size, speck))
                self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["theirs"]),
                                 ("pieces", 1, 0), (size, speck))

    def test_a_neighbours_foot_cut_off_from_it_in_the_matte_for_the_name_is_still_a_look_alike(self):
        # Three wine glasses, the middle one selected, each with a band running from its foot to 5 pixels before the
        # next glass's foot, in one shadow matte. The model's matte for "the wine glass" lost the thin stems, so the
        # left glass's foot is a piece of its own, 5.6% of the selection. Under a tenth it was a speck: the left
        # glass's band was kept with the selected one's, the two ran out on both sides of it and neither went.
        def wine(left):
            return [(left, 80, left + 40, 150), (left + 18, 151, left + 22, 194), (left + 5, 195, left + 35, 200)]

        bands = [(96, 192, 159, 200), (196, 192, 259, 200)]
        for size in (SIZE, (1536, 1152)):
            glasses = [[scaled(box, size) for box in wine(left)] for left in (60, 160, 260)]
            image = Image.new("RGB", size, (150, 140, 130))
            draw = ImageDraw.Draw(image)
            for box in bands:
                draw.rectangle(scaled(box, size), fill=(80, 75, 70))
            for box in (box for glass in glasses for box in glass):
                draw.rectangle(box, fill=(220, 230, 240))
            answers = []
            named = matte(*(box for glass in glasses for box in (glass[0], glass[2])), size=size)   # bowls and feet
            ask, segment, _asked, _described = model(names=("wine glass",), grounds={"the wine glass": [named]},
                                                     casts={"shadow": [matte(*(scaled(box, size) for box in bands), size=size)]})
            found = cast.find(image, matte(*glasses[1], size=size), ask, segment, answers=answers)
            self.assertEqual(found.words, "shadow", size)
            self.assertEqual(found.mask.getpixel(scaled((230, 196), size)), 255, size)   # its own band
            self.assertEqual(found.mask.getpixel(scaled((130, 196), size)), 0, size)     # the left glass's
            self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["theirs"]), ("pieces", 1, 1), size)

    def test_a_small_neighbour_beside_the_selected_one_keeps_its_shadow(self):
        # A glass a quarter as tall, 6.9% of the selected one, stands left of it with its band running toward the
        # selected glass and ending 3 pixels before it. Under a tenth it was a speck, and its band was taken as the
        # selected glass's shadow.
        glasses = [(160, 80, 200, 200), (129, 169, 139, 199), (260, 80, 300, 200), (330, 80, 370, 200)]
        found, answers = self.glasses(0, bands=(0,), glasses=glasses, shadows=[(140, 192, 156, 199)])
        self.assertEqual((found.mask, found.words, found.name), (None, "", "drinking glass"))
        self.assertEqual((answers[0]["each"][0]["counted"], answers[1]["pieces"], answers[1]["theirs"]), ("pieces", 0, 1))

    def test_look_alikes_side_by_side_keep_only_the_reflection_under_the_selected_one(self):
        # Three red boxes 6 pixels apart in dark water, each with its reflection right under it, the middle one
        # selected. The neighbours' reflections come inside its touching reach (7 pixels), and judged together
        # with its own they ran out on both sides of it, so its reflection stayed. Each is nearer its own box.
        boxes = [(70, 40, 127, 160), (134, 40, 193, 160), (200, 40, 257, 160)]
        images = [(left, 161, right, 230) for left, _top, right, _bottom in boxes]
        water = Image.new("RGB", SIZE, (20, 30, 20))
        draw = ImageDraw.Draw(water)
        for box in images:
            draw.rectangle(box, fill=(150, 40, 40))
        for box in boxes:
            draw.rectangle(box, fill=(190, 30, 30))
        answers = []
        ask, segment, _asked, _described = model(grounds={"the red box": [matte(*boxes)]}, casts={"reflection": [matte(*images)]})
        found = cast.find(water, matte(boxes[1]), ask, segment, answers=answers)
        self.assertEqual(found.words, "reflection")
        self.assertEqual([found.mask.getpixel(middle(box)) for box in images], [0, 255, 0])
        self.assertEqual((answers[-1]["pieces"], answers[-1]["theirs"]), (1, 2))

    def test_look_alikes_in_one_matte_keep_a_reflection_that_starts_below_the_selected_one(self):
        twins = [(20, 80, 80, 160), OBJECT, (280, 80, 340, 160)]
        images = [(20, 185, 80, 250), (150, 185, 210, 250), (280, 185, 340, 250)]   # each below a bank
        water = Image.new("RGB", SIZE, (20, 30, 20))
        draw = ImageDraw.Draw(water)
        for box in images:
            draw.rectangle(box, fill=(150, 40, 40))
        for box in twins:
            draw.rectangle(box, fill=(190, 30, 30))
        ask, segment, _asked, _described = model(grounds={"the red box": [matte(*twins)]}, casts={"reflection": [matte(*images)]})
        found = cast.find(water, matte(OBJECT), ask, segment)
        self.assertEqual(found.words, "reflection")
        self.assertEqual(found.mask.getpixel((180, 218)), 255)
        self.assertEqual(found.mask.getpixel((50, 218)), 0)
        self.assertEqual(found.mask.getpixel((310, 218)), 0)
        # The same when the model put the three images into the matte for the name as well: the selected box's
        # image is then a piece of that matte, and it is the reflection, not a look-alike beside it.
        ask, segment, _asked, _described = model(grounds={"the red box": [matte(*twins, *images)]}, casts={"reflection": [matte(*images)]})
        found = cast.find(water, matte(OBJECT), ask, segment)
        self.assertEqual(found.words, "reflection")
        self.assertEqual([found.mask.getpixel(point) for point in ((180, 218), (50, 218), (310, 218))], [255, 0, 0])

    def test_a_name_that_counted_whole_still_judges_the_whole_matte(self):
        broken = (312, 150, 360, 175)   # the far end of its shadow, beyond a crack in the floor
        image = picture(shadows=(SHADOW, broken))
        whole = []
        ask, segment, _asked, _described = model(casts={"shadow": [matte(SHADOW, broken)]})
        found = cast.find(image, matte(OBJECT), ask, segment, answers=whole)
        self.assertEqual((found.words, found.mask.getpixel((336, 162))), ("shadow", 255))
        self.assertNotIn("pieces", whole[1])
        # The same matte under a name that led back only through a piece of its matte keeps only what reaches the object.
        twins = matte((20, 80, 80, 160), OBJECT, (20, 200, 80, 280))
        alike = []
        ask, segment, _asked, _described = model(grounds={"the red box": [twins]}, casts={"shadow": [matte(SHADOW, broken)]})
        found = cast.find(image, matte(OBJECT), ask, segment, answers=alike)
        self.assertEqual((found.words, found.mask.getpixel((250, 162)), found.mask.getpixel((336, 162))), ("shadow", 255, 0))
        self.assertEqual(alike[1]["pieces"], 1)

    def failing(self, error, casts, cancel=False, answers=None):
        """``find`` with the reflection question failing with ``error``; with ``cancel`` the artist cancelled then."""
        ask, segment, _asked, _described = model(casts=casts)
        requested = []

        def late(shown, description):
            if description.startswith("the reflection"):
                requested.extend([True] if cancel else [])
                raise error
            return segment(shown, description)

        return cast.find(picture(), matte(OBJECT), ask, late, lambda: bool(requested), answers)

    def test_a_later_question_that_fails_keeps_what_was_found(self):
        # All questions share one time limit: the shadow came back in time, the reflection did not.
        answers = []
        found = self.failing(RuntimeError("timed out waiting for the backend"), {"shadow": [matte(SHADOW)]}, answers=answers)
        self.assertEqual(found.words, "shadow")
        self.assertEqual(found.mask.getpixel((250, 162)), 255)
        self.assertEqual(answers[-1], {"question": "reflection", "failed": "timed out waiting for the backend"})

    def test_a_cancel_or_a_failure_before_anything_was_found_still_stops_it(self):
        with self.assertRaises(GenerationCancelled):
            self.failing(GenerationCancelled("Selection cancelled."), {"shadow": [matte(SHADOW)]})
        with self.assertRaisesRegex(RuntimeError, "cancelled"):   # a cancel the engine reports in its own words
            self.failing(RuntimeError("cancelled by the user"), {"shadow": [matte(SHADOW)]}, cancel=True)
        with self.assertRaisesRegex(RuntimeError, "timed out"):   # nothing to keep: the removal says the engine was busy
            self.failing(RuntimeError("timed out waiting for the backend"), {"shadow": [matte(ELSEWHERE)]})

    def test_every_answer_is_noted_with_what_the_rules_made_of_it(self):
        answers = []
        ask, segment, _asked, _described = model(casts={"shadow": [matte(SHADOW)], "reflection": [matte(ELSEWHERE), Image.new("L", SIZE)]})
        cast.find(picture(shadows=(SHADOW, ELSEWHERE)), matte(OBJECT), ask, segment, answers=answers)
        named, shadow, elsewhere, empty = answers
        self.assertEqual((named["question"], named["picture"], named["name"], named["leads_back"]), ("name", 1, "red box", True))
        self.assertIn("<ref>A red box</ref>", named["said"])
        self.assertGreater(named["covered"], 0.9)
        self.assertEqual(len(named["mattes"]), 1)
        self.assertEqual((shadow["question"], shadow["kept"]), ("shadow", True))
        self.assertGreater(shadow["measure"]["touch"], cast.TOUCH)
        self.assertEqual((elsewhere["question"], elsewhere["kept"]), ("reflection", False))
        self.assertIsNotNone(elsewhere["measure"])
        self.assertEqual((empty["measure"], empty["kept"]), (None, False))


class FakeClient:
    base_url = "http://engine"

    def __init__(self, nodes=("PreviewAny",), text="<ref>A cup</ref>", masks=1, fail=None):
        self.nodes, self.text, self.masks, self.fail = nodes, text, masks, fail
        self.uploads, self.graphs, self.cancelled = [], [], []

    def object_info(self, node):
        return {node: {}} if node in self.nodes else {}

    def upload_image(self, name, data, subfolder=""):
        self.uploads.append(name)
        return f"{subfolder}/{name}"

    def queue_prompt(self, graph):
        self.graphs.append(graph)
        return f"prompt-{len(self.graphs)}"

    def wait_for(self, prompt_id, steps, deadline, **options):
        self.options = options
        if self.fail:
            raise self.fail
        graph = self.graphs[-1]
        outputs = {"6": {"text": [self.text]}}
        if "5" in graph:
            outputs["5"] = {"images": [{"filename": f"m{n}.png", "subfolder": "", "type": "temp"} for n in range(self.masks)]}
        return {"outputs": outputs}

    def view(self, filename, subfolder, folder):
        import io

        stream = io.BytesIO()
        matte(SHADOW).save(stream, "PNG")
        return stream.getvalue()

    def cancel_prompt(self, prompt_id):
        self.cancelled.append(prompt_id)


class QuestionTests(unittest.TestCase):
    def test_a_question_in_words_needs_no_mattes(self):
        graph = wevisone.question_workflow("enc", "a.png", "freeform", "What is this?")
        self.assertEqual(graph["3"]["inputs"]["task"], "freeform")
        self.assertEqual(graph["6"], {"class_type": "PreviewAny", "inputs": {"source": ["3", 3]}})
        self.assertNotIn("5", graph)
        described = wevisone.question_workflow("enc", "a.png", "segmentation", "the shadow of the cup")
        self.assertEqual(described["3"]["inputs"]["task"], "segmentation")
        self.assertIn("5", described)
        self.assertEqual(wevisone.workflow("enc", "a.png", "x")["3"]["inputs"]["task"], "segmentation")  # Select's own graph is unchanged
        self.assertNotIn("6", wevisone.workflow("enc", "a.png", "x"))

    def test_a_picture_is_uploaded_once_for_all_its_questions(self):
        client = FakeClient(masks=2)
        ask, segment = wevisone.questions(client, "enc")
        shown = picture()
        self.assertEqual(ask(shown, "What is this?"), "<ref>A cup</ref>")
        mattes = segment(shown, "the shadow of the cup")
        self.assertEqual([m.size for m in mattes], [SIZE, SIZE])
        segment(shown, "the reflection of the cup")
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(len(client.graphs), 3)
        self.assertNotIn("wait_for_queue", client.options)  # it never waits behind somebody else's render

    def test_an_engine_that_cannot_answer_in_words_says_so(self):
        with self.assertRaisesRegex(ValueError, "answers in words"):
            wevisone.questions(FakeClient(nodes=()), "enc")

    def test_a_question_that_times_out_takes_its_own_prompt_off_the_queue(self):
        client = FakeClient(fail=RuntimeError("timed out waiting for the backend"))
        ask, _segment = wevisone.questions(client, "enc")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            ask(picture(), "What is this?")
        self.assertEqual(client.cancelled, ["prompt-1"])

    def test_a_cancel_stops_before_the_next_question(self):
        client = FakeClient()
        ask, _segment = wevisone.questions(client, "enc", cancel=lambda: True)
        with self.assertRaises(GenerationCancelled):
            ask(picture(), "What is this?")
        self.assertEqual(client.graphs, [])

    def test_the_local_selection_engine_is_not_started_for_a_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "selection-engine.json").write_text('{"port": 9, "comfy_dir": ".", "data_dir": ".", "python": "."}', encoding="utf-8")
            helper = wevisone.WeVisOne(folder)
            app = SimpleNamespace(selection_helper=True)
            with self.assertRaisesRegex(ValueError, "not running"):
                helper.questions(app)
            self.assertIsNone(helper.process)

    def courtesy(self, answers_on, cancel=lambda: False, seconds=30.0, step=5.0):
        """A server that answers its health check from the ``answers_on``-th try, with a clock that moves
        ``step`` seconds each time it is read."""
        tries, slept, now = [], [], [0.0]

        def ensure_running(progress, cancel):
            tries.append(1)
            if len(tries) < answers_on:
                raise BackendUnavailable("Studio is not answering at http://engine.")
            return "http://engine"

        def monotonic():
            now[0] += step
            return now[0]

        app = SimpleNamespace(selection_helper=False, settings=SimpleNamespace(backend="comfy"),
                              server=SimpleNamespace(ensure_running=ensure_running))
        clock = SimpleNamespace(monotonic=monotonic, sleep=slept.append)
        with tempfile.TemporaryDirectory() as folder, patch.object(wevisone, "time", clock), \
                patch.object(wevisone, "ComfyClient", return_value="client"):
            try:
                return wevisone.WeVisOne(folder)._courtesy_client(app, cancel, seconds), tries, slept
            except BackendUnavailable as err:
                return err, tries, slept

    def test_a_server_that_misses_a_health_check_is_asked_again(self):
        (client, left), tries, slept = self.courtesy(answers_on=2, step=1.0)
        self.assertEqual((client, len(tries), slept), ("client", 2, [1.0]))
        self.assertTrue(1.0 <= left < 30.0)   # the wait comes out of the questions' time

    def test_a_server_that_stays_silent_is_given_up_after_a_few_seconds(self):
        result, tries, _slept = self.courtesy(answers_on=99)
        self.assertIsInstance(result, BackendUnavailable)
        self.assertEqual(len(tries), 3)   # at 5, 10 and 15 s on this clock: past the patience on the third
        self.assertLess(wevisone.PATIENCE, cast.SECONDS)

    def test_a_short_time_limit_shortens_the_wait_and_a_cancel_ends_it(self):
        result, tries, _slept = self.courtesy(answers_on=99, seconds=4.0)
        self.assertIsInstance(result, BackendUnavailable)
        self.assertEqual(len(tries), 1)
        result, tries, _slept = self.courtesy(answers_on=99, cancel=lambda: True, step=0.1)
        self.assertIsInstance(result, BackendUnavailable)
        self.assertEqual(len(tries), 1)


class BridgeTests(unittest.TestCase):
    def bridge(self, bounds=(150, 80, 61, 81)):
        bridge = PhotoCraftBridge.__new__(PhotoCraftBridge)
        bridge.client = Mock()
        bridge.client.run.return_value = {"width": SIZE[0], "height": SIZE[1], "hasSelection": True, "selectionBounds": list(bounds)}
        bridge.activate = Mock()
        return bridge

    def test_a_widened_selection_decides_where_the_job_works(self):
        bridge = self.bridge()
        image = PCImage(bridge, "doc", 0)
        self.assertEqual(bridge.selection_rect(image), Rect(150, 80, 61, 81))
        image.widened = ImageChops.lighter(matte(OBJECT), matte(SHADOW))
        self.assertEqual(bridge.selection_rect(image), Rect(150, 80, 151, 96))

    def test_the_job_reads_the_widened_selection_and_the_next_job_does_not(self):
        bridge = self.bridge()
        image = PCImage(bridge, "doc", 0)
        visible, chosen = Image.new("RGBA", SIZE), matte(OBJECT)
        bridge._read_snapshot = Mock(return_value=(visible, chosen))
        self.assertIs(bridge._snapshot(image)[1], chosen)
        image.widened = ImageChops.lighter(chosen, matte(SHADOW))
        self.assertIs(bridge._snapshot(image)[1], image.widened)
        _mask_png, raw = bridge.export_selection_mask(image, Rect(150, 80, 151, 96))
        self.assertEqual(len(raw), 151 * 96)
        self.assertEqual(image.selection, image.widened.tobytes())
        bridge.discard(image)
        self.assertIsNone(image.widened)
        image.widened = matte(SHADOW)
        bridge.commit = Mock()
        bridge.finish(image)
        self.assertIsNone(image.widened)

    def test_a_widened_selection_of_another_size_is_ignored(self):
        bridge = self.bridge()
        image = PCImage(bridge, "doc", 0)
        chosen = matte(OBJECT)
        bridge._read_snapshot = Mock(return_value=(Image.new("RGBA", SIZE), chosen))
        image.widened = Image.new("L", (10, 10), 255)
        self.assertIs(bridge._snapshot(image)[1], chosen)


class SmoothOnlyTests(unittest.TestCase):
    """Automatic adds Local's repair only where it can interpolate the surroundings."""

    def bridge(self, smooth):
        from openfill_photocraft import bridge as module

        bridge = PhotoCraftBridge.__new__(PhotoCraftBridge)
        bridge._sample_patches = Mock(return_value=[b"patches"])
        self.patches = [unittest.mock.patch.object(module, "structured_background", lambda *a, **k: None),
                        unittest.mock.patch.object(module, "smooth_background", lambda *a, **k: smooth)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        return bridge

    def sample(self, bridge, **options):
        source = picture().convert("RGBA")
        return bridge._sample_texture(Mock(), source, matte(OBJECT), 1, 7, lambda *a: None, lambda: False, **options)

    def test_textured_surroundings_give_no_repair_instead_of_sampled_patches(self):
        bridge = self.bridge(None)
        self.assertEqual(self.sample(bridge, smooth_only=True), [])
        bridge._sample_patches.assert_not_called()
        self.assertEqual(self.sample(bridge), [b"patches"])   # Local itself still falls back to them

    def test_smooth_surroundings_give_the_interpolated_repair(self):
        bridge = self.bridge(Image.new("RGB", SIZE, (200, 200, 200)))
        self.assertEqual(len(self.sample(bridge, smooth_only=True)), 1)
        bridge._sample_patches.assert_not_called()

    def test_nothing_is_staged_when_there_is_no_smooth_repair(self):
        bridge = self.bridge(None)
        bridge._snapshot = Mock(return_value=(picture().convert("RGBA"), matte(OBJECT)))
        bridge.insert_results = Mock()
        self.assertEqual(bridge.remove_with_texture(Mock(), 1, 7, lambda *a: None, lambda: False, smooth_only=True), [])
        bridge.insert_results.assert_not_called()


class FakeBridge:
    def __init__(self, profile, visible, chosen):
        self.profile, self.visible, self.chosen = profile, visible, chosen

    def _snapshot(self, image):
        return self.visible, image.widened if image.widened is not None else self.chosen


def service(profile, casts=None, fail=None, chosen=None):
    svc = Service.__new__(Service)
    svc.state = JobState(state="running")
    visible = picture().convert("RGBA")
    svc.bridge = FakeBridge(Path(profile), visible, matte(OBJECT) if chosen is None else chosen)
    svc.app = Mock()
    svc._job_app = svc.app
    select_app = Mock(name="select-engine")
    svc.app_for = Mock(return_value=select_app)
    ask, segment, _asked, described = model(casts=casts if casts is not None else {"shadow": [matte(SHADOW)]})

    def questions(app, cancel, seconds):
        if fail:
            raise fail
        return ask, segment

    svc.selection = SimpleNamespace(model=SimpleNamespace(questions=Mock(side_effect=questions)))
    svc._remove_with = Mock(return_value=["layer"])
    svc._remove_auto = Mock(return_value=["one", "two"])
    return svc, described


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_a_removal_takes_the_shadow_along_and_says_so(self):
        svc, described = service(self.folder.name)
        image = PCImage(svc.bridge, "doc", 0)
        self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
        self.assertEqual(image.widened.getpixel((250, 162)), 255)   # the shadow
        self.assertEqual(image.widened.getpixel((180, 120)), 255)   # the object itself
        self.assertEqual(image.widened.getpixel((50, 50)), 0)
        self.assertEqual(svc.state.notices, [cast.notice("shadow")])
        self.assertEqual(svc._cast_words, "shadow")
        svc.app_for.assert_called_once_with("select")   # the engine Select uses answers, wherever Remove runs
        self.assertEqual(svc.selection.model.questions.call_args.args[2], cast.SECONDS)
        self.assertIn("the shadow of the red box", described)

    def test_the_option_turned_off_asks_nothing(self):
        svc, described = service(self.folder.name)
        image = PCImage(svc.bridge, "doc", 0)
        svc._remove(image, {"removal": "rorem", "cast": False}, 7, 1)
        self.assertIsNone(image.widened)
        self.assertEqual((svc.state.notices, described), ([], []))
        svc.selection.model.questions.assert_not_called()

    def test_automatic_gets_the_widened_selection_too(self):
        svc, _described = service(self.folder.name)
        image = PCImage(svc.bridge, "doc", 0)

        def auto(image, app, seed, variations):
            self.assertIsNotNone(image.widened)   # widened before the first method runs
            return ["one", "two"]

        svc._remove_auto.side_effect = auto
        self.assertEqual(svc._remove(image, {"removal": "openfill-auto"}, 7, 2), ["one", "two"])
        self.assertEqual(len(svc.state.notices), 1)

    def test_nothing_found_changes_nothing_and_says_nothing(self):
        svc, _described = service(self.folder.name, casts={})
        image = PCImage(svc.bridge, "doc", 0)
        svc._remove(image, {"removal": "rorem"}, 7, 1)
        self.assertIsNone(image.widened)
        self.assertEqual(svc.state.notices, [])

    def test_an_engine_that_cannot_answer_does_not_stop_the_removal(self):
        for problem in (ValueError("The local selection engine is not running."), RuntimeError("timed out waiting for the backend"),
                        OSError("connection refused")):
            svc, _described = service(self.folder.name, fail=problem)
            image = PCImage(svc.bridge, "doc", 0)
            self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
            self.assertIsNone(image.widened)
            self.assertEqual(svc.state.notices, [])

    def test_an_engine_too_busy_to_ask_is_said_so_because_the_shadow_stays(self):
        for problem in (BackendUnavailable("Studio is not answering at http://engine."), ComfyApiError("timed out waiting for the backend")):
            svc, _described = service(self.folder.name, fail=problem)
            image = PCImage(svc.bridge, "doc", 0)
            self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
            self.assertIsNone(image.widened)
            self.assertEqual(svc.state.notices, [cast.BUSY])
        self.assertIn("Select them too", cast.BUSY)

    def test_a_timeout_on_the_reflection_question_keeps_the_shadow_already_found(self):
        svc, _described = service(self.folder.name)
        ask, segment, _asked, _described = model(casts={"shadow": [matte(SHADOW)]})

        def late(shown, description):
            if description.startswith("the reflection"):
                raise ComfyApiError("timed out waiting for the backend")
            return segment(shown, description)

        svc.selection.model.questions.side_effect = lambda app, cancel, seconds: (ask, late)
        image = PCImage(svc.bridge, "doc", 0)
        self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
        self.assertEqual(image.widened.getpixel((250, 162)), 255)
        self.assertEqual(svc.state.notices, [cast.notice("shadow")])

    def test_the_next_removal_starts_afresh_after_a_busy_one(self):
        svc, _described = service(self.folder.name, fail=ComfyApiError("timed out waiting for the backend"))
        svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": "rorem"}, 7, 1)
        svc.state.notices.clear()
        svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": "rorem", "cast": False}, 7, 1)
        self.assertEqual(svc.state.notices, [])

    def test_a_cancel_while_looking_cancels_the_removal(self):
        svc, _described = service(self.folder.name, fail=GenerationCancelled("Selection cancelled."))
        with self.assertRaises(GenerationCancelled):
            svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": "rorem"}, 7, 1)
        svc._remove_with.assert_not_called()
        svc, _described = service(self.folder.name, fail=RuntimeError("cancelled by the user"))
        svc.state.cancel_requested = True
        with self.assertRaises(RuntimeError):
            svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": "rorem"}, 7, 1)

    def test_select_all_is_left_alone(self):
        svc, described = service(self.folder.name, chosen=Image.new("L", SIZE, 255))
        image = PCImage(svc.bridge, "doc", 0)
        svc._remove(image, {"removal": "rorem"}, 7, 1)
        self.assertIsNone(image.widened)
        self.assertEqual(described, [])
        svc.selection.model.questions.assert_not_called()

    def test_the_choice_note_records_what_was_taken_along(self):
        import json

        svc, _described = service(self.folder.name)
        svc.layers = [SimpleNamespace(label="FLUX 3"), SimpleNamespace(label="RORem")]
        svc._choice_kind, svc.state.shown = "fill", 1
        svc._choice_info = {"automatic": True, "share": 0.03, "cast": "shadow"}
        svc._log_choice("selected")
        row = json.loads((Path(self.folder.name) / "removal-choices.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((row["kept"], row["cast"]), ("RORem", "shadow"))
        svc._choice_info = {"automatic": True, "share": 0.03, "cast": ""}
        svc._log_choice("discarded")
        last = json.loads((Path(self.folder.name) / "removal-choices.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("cast", last)


class PiecesTests(unittest.TestCase):
    def test_each_piece_comes_back_on_its_own_largest_first(self):
        mask = matte((20, 80, 80, 160), (150, 80, 250, 160), (280, 200, 300, 220))
        found = pieces(mask)
        self.assertEqual([piece.getbbox() for piece in found], [(150, 80, 251, 161), (20, 80, 81, 161), (280, 200, 301, 221)])
        union = Image.new("L", SIZE)
        for piece in found:
            self.assertEqual(ImageChops.darker(union, piece).getbbox(), None)   # no pixel in two pieces
            union = ImageChops.lighter(union, piece)
        self.assertIsNone(ImageChops.difference(union, mask).getbbox())
        self.assertEqual(pieces(Image.new("L", SIZE)), [])

    def test_only_the_pieces_that_reach_a_mask_and_corners_hang_together(self):
        mask = matte((20, 80, 80, 160), OBJECT)
        self.assertEqual([piece.getbbox() for piece in pieces(mask, touching=matte((200, 100, 220, 120)))], [(150, 80, 211, 161)])
        corner = matte((10, 10, 19, 19), (20, 20, 29, 29))
        self.assertEqual(len(pieces(corner, block=1)), 1)
        self.assertEqual(len(pieces(matte((10, 10, 19, 19), (21, 21, 29, 29)), block=1)), 2)

    def test_a_matte_in_very_many_pieces_gives_only_its_largest(self):
        specks = matte(*[(x, y, x + 3, y + 3) for x in range(0, 400, 20) for y in range(0, 300, 20)], (100, 100, 140, 140))
        found = pieces(specks, most=5)
        self.assertEqual(len(found), 5)
        self.assertEqual(found[0].getpixel((110, 110)), 255)   # the block, with the specks it swallowed
        sizes = [piece.histogram()[255] for piece in found]
        self.assertEqual(sizes, sorted(sizes, reverse=True))



if __name__ == "__main__":
    unittest.main()
