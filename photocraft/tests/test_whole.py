# SPDX-License-Identifier: Apache-2.0
"""Object Selection brings along the parts of the object it had left out, and leaves a picked part alone."""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import cast, tips, wevisone, whole  # noqa: E402
from openfill_photocraft.local_removal import grow_support  # noqa: E402
from openfill_photocraft.selection import Selection  # noqa: E402

from openfill.backend import GenerationCancelled  # noqa: E402

SIZE = (400, 300)
BODY = (150, 80, 230, 200)      # the bin
WHEEL = (226, 180, 250, 204)    # its wheel, left out of the selection
OTHER = (20, 20, 80, 70)        # something else in the picture
STRING = (189, 200, 191, 290)   # a string hanging from it, three pixels wide


def matte(*boxes, size=SIZE):
    mask = Image.new("L", size)
    for box in boxes:
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def model(answers, names=("green bin",)):
    """``ask`` and ``segment`` like the selection model: names in turn, and one matte list per question asked."""
    asked, questions = [], []
    replies = iter(names)

    def ask(shown, question):
        asked.append(question)
        return f"<ref>A {next(replies)}</ref><box><1><2><3><4><OBJ></box>"

    def segment(shown, description):
        questions.append(description)
        return [m.resize(shown.size) for m in answers.get(description, [])]

    return ask, segment, asked, questions


def painted(ground, *items):
    """A picture of ``ground`` colour with (box, colour) rectangles on it."""
    picture = Image.new("RGB", SIZE, ground)
    for box, colour in items:
        ImageDraw.Draw(picture).rectangle(box, fill=colour)
    return picture


def question(name):
    return whole.QUESTION.format(name=name)


def strings(name):
    return whole.STRINGS.format(name=name)


def reflected(name):
    # A third question an earlier version asked when a piece under the object looked like its reflection. It is
    # gone; the model below answers it anyway, to show it is never asked.
    return f"the reflection of the {name}"


def disc(box, size):
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


TOP = (100, 100, 300, 130)   # a table top, selected; its legs below it, in the same wood, left out
LEGS = [(108, 131, 118, 230), (282, 131, 292, 230), (140, 131, 148, 200), (252, 131, 260, 200)]
WOOD = (160, 110, 60)


class RuleTests(unittest.TestCase):
    def test_a_part_the_answer_adds_is_taken(self):
        about, beyond = whole.parts(matte(BODY), [matte(BODY, WHEEL)])
        self.assertTrue(about)
        self.assertEqual(beyond.getpixel((242, 192)), 255)   # the wheel
        self.assertEqual(beyond.getpixel((190, 140)), 0)     # not the body again

    def test_an_answer_about_something_else_is_not_about_this_selection(self):
        self.assertEqual(whole.parts(matte(BODY), [matte(OTHER)]), (False, None))
        self.assertEqual(whole.parts(matte(BODY), []), (False, None))
        self.assertEqual(whole.parts(Image.new("L", SIZE), [matte(BODY)]), (False, None))

    def test_the_same_object_again_adds_nothing(self):
        self.assertEqual(whole.parts(matte(BODY), [matte(BODY)]), (True, None))
        rim = (149, 79, 231, 201)   # a matte one pixel wider all round: an edge, not a part
        self.assertEqual(whole.parts(matte(BODY), [matte(rim)]), (True, None))

    def test_a_part_picked_on_purpose_does_not_become_the_whole_object(self):
        # The wheel was selected; the answer is the bin, many times its size.
        about, beyond = whole.parts(matte(WHEEL), [matte(BODY, WHEEL)])
        self.assertEqual((about, beyond), (True, None))

    def test_mattes_that_do_not_reach_the_selection_are_not_its_parts(self):
        about, beyond = whole.parts(matte(BODY), [matte(BODY), matte(OTHER)])
        self.assertEqual((about, beyond), (True, None))

    def test_a_line_that_runs_on_from_the_object_is_its_string(self):
        found = whole.strand(matte(BODY), [matte(STRING)])
        self.assertEqual(found.getpixel((190, 260)), 255)
        self.assertEqual(found.getpixel((190, 140)), 0)      # not the body
        together = whole.strand(matte(BODY), [matte(BODY, STRING)])   # the model may return the object with it
        self.assertEqual(together.getpixel((190, 260)), 255)

    def test_what_is_not_long_and_thin_is_not_a_string(self):
        self.assertIsNone(whole.strand(matte(BODY), [matte(WHEEL)]))                     # a blob beside it
        self.assertIsNone(whole.strand(matte(BODY), [matte((180, 200, 200, 290))]))      # a band, not a line
        self.assertIsNone(whole.strand(matte(BODY), [matte((189, 200, 191, 215))]))      # a stub
        self.assertIsNone(whole.strand(matte(BODY), [matte(BODY)]))                      # the object again
        self.assertIsNone(whole.strand(matte(BODY), []))
        self.assertIsNone(whole.strand(Image.new("L", SIZE), [matte(STRING)]))

    def test_a_speck_elsewhere_in_the_same_answer_is_left_out(self):
        speck = (300, 250, 304, 254)   # the model's matte for the kite's string also touched a bird
        found = whole.strand(matte(BODY), [matte(STRING, speck)])
        self.assertEqual((found.getpixel((190, 260)), found.getpixel((302, 252))), (255, 0))
        broken = whole.strand(matte(BODY), [matte((189, 200, 191, 240), (189, 246, 191, 290))])   # a gap of a few pixels
        self.assertEqual((broken.getpixel((190, 220)), broken.getpixel((190, 270))), (255, 255))

    def test_a_string_picked_on_its_own_does_not_pull_in_what_it_is_tied_to(self):
        piece, speck = (184, 190, 196, 199), (300, 280, 303, 283)   # a bit of the kite at its end, and a speck far off
        self.assertIsNone(whole.strand(matte(STRING), [matte(STRING, piece, speck)]))

    def test_a_line_that_does_not_reach_the_object_is_someone_elses(self):
        self.assertIsNone(whole.strand(matte(BODY), [matte((40, 100, 42, 280))]))
        self.assertIsNone(whole.strand(matte(BODY), [matte(BODY), matte((40, 100, 42, 280))]))

    def test_lines_many_times_the_objects_size_are_not_its_string(self):
        wires = matte(*[(0, y, 399, y + 2) for y in range(84, 200, 12)], (188, 0, 192, 299))   # a fence behind it
        self.assertIsNone(whole.strand(matte((180, 120, 200, 140)), [wires]))

    def test_a_matte_a_few_pixels_looser_all_round_adds_nothing_at_any_size(self):
        # The rule judges at the model's frame. A rim of 1.2% of the object was 2 px on anything under 200 px,
        # so an answer 3 or 4 px looser passed for a part on small objects (+43% on a 30 px disc).
        size = (800, 600)
        for side in (16, 30, 60, 150, 400):
            for loose in (2, 3, 4):
                disc, looser = Image.new("L", size), Image.new("L", size)
                ImageDraw.Draw(disc).ellipse((100, 100, 100 + side, 100 + side), fill=255)
                ImageDraw.Draw(looser).ellipse((100 - loose, 100 - loose, 100 + side + loose, 100 + side + loose), fill=255)
                square = matte((100, 100, 100 + side, 100 + side), size=size)
                for chosen, answer in ((disc, looser), (square, grow_support(square, loose))):
                    with self.subTest(side=side, loose=loose):
                        self.assertEqual(whole.parts(chosen, [answer]), (True, None))
                        self.assertIsNone(whole.strand(chosen, [answer]))

    def test_an_answer_6_to_8_px_looser_round_a_small_object_adds_nothing(self):
        # The rim is 5 px on a small object. An answer just past it leaves only a thin ring there, or caps of
        # one, between LEAST and MOST of the selection; and _kept then took the whole loose ring along.
        size = (200, 200)
        for side in (10, 16, 20, 24, 30):
            for loose in (6, 7, 8):
                round_one = disc((60, 60, 60 + side, 60 + side), size)
                looser = disc((60 - loose, 60 - loose, 60 + side + loose, 60 + side + loose), size)
                square = matte((60, 60, 60 + side, 60 + side), size=size)
                for chosen, answer in ((round_one, looser), (square, grow_support(square, loose))):
                    with self.subTest(side=side, loose=loose):
                        self.assertEqual(whole.parts(chosen, [answer]), (True, None))
                        self.assertIsNone(whole.strand(chosen, [answer]))

    def test_a_loose_answer_round_a_small_object_brings_only_the_part_that_reaches_out(self):
        size = (800, 600)
        cup = disc((100, 100, 130, 130), size)
        handle = (131, 110, 150, 120)   # reaches 20 px out on the right
        answer = ImageChops.lighter(disc((93, 93, 137, 137), size), matte(handle, size=size))   # and 7 px looser all round
        about, beyond = whole.parts(cup, [answer])
        self.assertTrue(about)
        self.assertEqual(beyond.getpixel((146, 115)), 255)
        self.assertEqual(whole._count(ImageChops.subtract(beyond, matte((126, 100, 160, 130), size=size))), 0)   # nothing of the ring
        ask, segment, _asked, _questions = model({question("cup"): [answer]})
        result = whole.complete(Image.new("RGB", size, (120, 130, 120)), cup, ask, segment, described="cup")
        self.assertEqual((result.getpixel((146, 115)), result.getpixel((96, 115)), result.getpixel((115, 96))), (255, 0, 0))

    def test_a_loose_answer_round_a_small_object_on_a_large_document_adds_nothing(self):
        # 6000 x 4000 is judged at 1536 x 1024: objects of 40 to 120 px are 10 to 31 px there, and the answer
        # comes back 6 or 8 px looser at that size. A 40 px disc gained 3.4 times its area this way.
        document = Image.new("RGB", (6000, 4000), (120, 130, 120))
        frame = (1536, 1024)
        for side in (40, 120):
            chosen = disc((3000, 2000, 3000 + side, 2000 + side), document.size)
            small = whole._solid(chosen.resize(frame, Image.Resampling.BILINEAR)).getbbox()
            for loose in (6, 8):
                with self.subTest(side=side, loose=loose):
                    answer = disc((small[0] - loose, small[1] - loose, small[2] - 1 + loose, small[3] - 1 + loose), frame)
                    ask, segment, _asked, _questions = model({question("ball"): [answer], strings("ball"): [answer]})
                    self.assertIsNone(whole.complete(document, chosen, ask, segment, described="ball"))

    def test_the_rest_of_a_part_the_selection_already_half_holds_is_taken(self):
        # A click took the bin with most of its wheel; what is left is under LEAST of the selection, but it
        # reaches well beyond the selection, which the edge of a matte never does.
        chosen, answer = matte(BODY, (226, 186, 249, 196)), matte(BODY, (226, 186, 268, 196))
        about, beyond = whole.parts(chosen, [answer])
        self.assertTrue(about)
        self.assertLess(whole._count(beyond), whole.LEAST * whole._count(chosen))
        self.assertEqual((beyond.getpixel((262, 191)), beyond.getpixel((190, 140))), (255, 0))
        ask, segment, _asked, _questions = model({question("green bin"): [answer]})
        self.assertEqual(whole.complete(Image.new("RGB", SIZE, (120, 130, 120)), chosen, ask, segment).getpixel((266, 191)), 255)
        # A bump of the same size that stops a few pixels out is the edge of a matte.
        self.assertEqual(whole.parts(chosen, [matte(BODY, (226, 186, 258, 196))]), (True, None))

    def test_the_rest_of_a_string_the_selection_mostly_holds_is_taken(self):
        found = whole.strand(matte(BODY, (189, 200, 191, 270)), [matte(STRING)])
        self.assertEqual((found.getpixel((190, 288)), found.getpixel((190, 140))), (255, 0))

    def test_a_piece_that_floats_free_of_the_selection_is_not_a_part(self):
        blob = (252, 110, 264, 122)   # in the same answer, but not hanging on the bin
        _about, beyond = whole.parts(matte(BODY), [matte(BODY, WHEEL, blob)])
        self.assertEqual((beyond.getpixel((242, 192)), beyond.getpixel((258, 116))), (255, 0))
        # A leash picked alone: the answer held a bit of the dog's tail beside its end, and nothing else.
        leash = (100, 150, 300, 152)
        self.assertEqual(whole.parts(matte(leash), [matte(leash, (312, 140, 320, 148))]), (True, None))

    def test_specks_strewn_along_a_string_picked_alone_are_not_a_line(self):
        # A kite's line picked alone: the answer added a bird beside it and a bit at its end. Together they
        # span a long box, but cover a third of a pixel of it per pixel.
        line = (100, 10, 102, 290)
        self.assertIsNone(whole.strand(matte(line), [matte(line, (106, 100, 116, 106), (100, 291, 102, 297))]))

    def test_a_loose_answer_with_a_handle_adds_the_handle_and_none_of_the_ring_beyond_it(self):
        # A continuous loose ring was one piece with the handle, so the whole ring came along with it, the side
        # away from the handle too (+1.1 to +1.5 times a 30 px object). Only past the slack band are they apart.
        size = (800, 600)
        for shape, side, loose in (("square", 30, 6), ("square", 30, 7), ("disc", 30, 8), ("disc", 40, 8)):
            if shape == "disc":
                cup = disc((100, 100, 100 + side, 100 + side), size)
                ring = disc((100 - loose, 100 - loose, 100 + side + loose, 100 + side + loose), size)
            else:
                cup = matte((100, 100, 100 + side, 100 + side), size=size)
                ring = grow_support(cup, loose)
            middle = 100 + side // 2
            handle = (101 + side, middle - 5, 120 + side, middle + 5)   # reaches 20 px out on the right
            answer = ImageChops.lighter(ring, matte(handle, size=size))
            with self.subTest(shape=shape, side=side, loose=loose):
                about, beyond = whole.parts(cup, [answer])
                self.assertTrue(about)
                self.assertEqual(beyond.getpixel((118 + side, middle)), 255)
                self.assertEqual(whole._count(beyond.crop((0, 0, 101 + side, size[1]))), 0)   # none of the ring
                ask, segment, _asked, _questions = model({question("cup"): [answer]})
                result = whole.complete(Image.new("RGB", size, (120, 130, 120)), cup, ask, segment, described="cup")
                added = ImageChops.subtract(whole._solid(result), cup)
                self.assertEqual(added.getpixel((118 + side, middle)), 255)   # the handle, to its end
                self.assertEqual(added.getpixel((101 + side, middle)), 255)   # and from its root
                self.assertEqual(whole._count(added.crop((0, 0, middle, size[1]))), 0)   # nothing left of the middle

    def test_a_loose_strings_answer_with_the_string_adds_only_the_string(self):
        # The strings answer may hold the object, loosely, with its string. The ring counted as part of the line,
        # and the whole ring came along with the string (+0.33 to +0.37 of a 100 px kite, against +0.076).
        size = (800, 600)
        kite = matte((300, 100, 400, 200), size=size)
        string = matte((350, 201, 351, 590), size=size)   # 2 px wide
        for loose in (6, 7, 8):
            answer = ImageChops.lighter(grow_support(kite, loose), string)
            with self.subTest(loose=loose):
                line = whole.strand(kite, [answer])
                self.assertEqual(line.getpixel((350, 580)), 255)
                self.assertEqual(whole._count(ImageChops.subtract(line, string)), 0)   # the line is the string alone
                ask, segment, _asked, _questions = model({question("kite"): [kite], strings("kite"): [answer]}, names=("kite",))
                result = whole.complete(Image.new("RGB", size, (120, 130, 120)), kite, ask, segment, described="kite")
                added = ImageChops.subtract(whole._solid(result), kite)
                self.assertEqual(whole._count(ImageChops.darker(added, string)), whole._count(string))   # all of it
                self.assertEqual(whole._count(added.crop((0, 0, size[0], 150))), 0)   # nothing on the kite's top half
                # and of the ring, only the bit beside the string's root
                self.assertEqual(whole._count(ImageChops.subtract(added, matte((334, 190, 367, 599), size=size))), 0)

    def test_an_oar_leaving_the_object_at_a_shallow_angle_keeps_most_of_its_root(self):
        # Judged past the slack band, an oar at 20 degrees to the hull starts some 20 px along from where it
        # leaves it. Taken back from there by 2 rims only, 95.0% of it came along; by 2 rims and SLACK, 97.3%
        # (99.8% before the slack band, which a loose ring needs).
        picture = Image.new("RGB", SIZE, (120, 130, 120))
        hull = matte((100, 100, 300, 140))
        oar = Image.new("L", SIZE)
        ImageDraw.Draw(oar).line((200, 140, 200 + 120 * 0.9397, 140 + 120 * 0.3420), fill=255, width=5)
        oar = ImageChops.subtract(oar, hull)
        ask, segment, _asked, _questions = model({question("boat"): [ImageChops.lighter(hull, oar)]})
        result = whole.complete(picture, hull, ask, segment, described="boat")
        self.assertGreaterEqual(whole._count(ImageChops.darker(whole._solid(result), oar)), 0.96 * whole._count(oar))

    def test_a_ring_9_or_10_px_looser_round_an_object_of_10_to_24_px_adds_nothing(self):
        # Past the slack band only a few caps of such a ring are left, and they would pass for a part; all the
        # answer adds past the rim is more than MOST, though, as 0.26.0 judged it too.
        size = (200, 200)
        for side in (10, 16, 20, 24):
            for loose in (9, 10):
                round_one = disc((60, 60, 60 + side, 60 + side), size)
                looser = disc((60 - loose, 60 - loose, 60 + side + loose, 60 + side + loose), size)
                square = matte((60, 60, 60 + side, 60 + side), size=size)
                for chosen, answer in ((round_one, looser), (square, grow_support(square, loose))):
                    with self.subTest(side=side, loose=loose):
                        self.assertEqual(whole.parts(chosen, [answer]), (True, None))
                        self.assertIsNone(whole.strand(chosen, [answer]))

    def test_both_panels_offer_the_option_with_a_tip(self):
        page = (ROOT / "photocraft/openfill_photocraft/static/index.html").read_text(encoding="utf-8")
        self.assertIn(f"<span>{whole.OPTION}</span>", page)
        self.assertIn('id="select-whole" checked', page)
        self.assertIn('whole:$("select-whole").checked', page)
        self.assertIn("attached", tips.CONTROLS["select.whole"])
        view = (ROOT / "photocraft/openfill_photocraft/select_view.py").read_text(encoding="utf-8")
        self.assertIn('"whole": self.whole.get()', view)
        self.assertIn('bar.prefs.get("selectWhole", True)', view)


class ReflectionTests(unittest.TestCase):
    """A piece under the object in its colours is a part like any other, as in 0.26.0. A rule that refused the
    object's reflection cost a table its legs whenever the model called the shadow the reflection, and Remove
    takes an object's reflection along anyway. The model below would answer that question; it is never asked."""

    def test_same_coloured_legs_under_a_table_top_are_parts(self):
        picture = painted((200, 200, 200), (TOP, WOOD), *[(leg, WOOD) for leg in LEGS])
        answer = matte(TOP, *LEGS)
        _about, beyond = whole.parts(matte(TOP), [answer])
        self.assertEqual([beyond.getpixel(((leg[0] + leg[2]) // 2, leg[3] - 5)) for leg in LEGS], [255] * 4)
        ask, segment, _asked, questions = model({question("wooden table"): [answer], reflected("wooden table"): [matte(*LEGS)]})
        result = whole.complete(picture, matte(TOP), ask, segment, described="wooden table")
        legs = matte(*LEGS)
        self.assertEqual(whole._count(ImageChops.darker(result, legs)), whole._count(legs))   # whole legs, roots and all
        self.assertEqual(questions, [question("wooden table"), strings("wooden table")])

    def test_legs_stay_parts_when_the_model_would_call_the_shadow_the_reflection(self):
        # The three cases that still lost legs: a coffee table's shadow, which touches the legs and not the top; a
        # tall table's; and a dark patch the table stands in, out on both sides of it.
        for heights, shade in (((30, 21), (95, 145, 305, 170)), ((100, 70), (100, 170, 300, 240)), ((100, 70), (60, 135, 340, 200))):
            long, short = heights
            legs = [(108, 131, 118, 130 + long), (282, 131, 292, 130 + long), (140, 131, 148, 130 + short), (252, 131, 260, 130 + short)]
            picture = painted((200, 200, 200), (shade, (120, 120, 122)), (TOP, WOOD), *[(leg, WOOD) for leg in legs])
            with self.subTest(shade=shade):
                ask, segment, _asked, questions = model({question("table"): [matte(TOP, *legs)], reflected("table"): [matte(shade)]})
                result = whole.complete(picture, matte(TOP), ask, segment, described="table")
                self.assertEqual(whole._count(ImageChops.darker(result, matte(*legs))), whole._count(matte(*legs)))
                self.assertEqual(questions, [question("table"), strings("table")])

    def test_the_objects_reflection_comes_along_as_any_part(self):
        # A blue hull with a paler blue image below it on cream water, which the model calls its reflection; a
        # vase's foot in the same glaze; a grey wheel under a green bin, which cast's colour test takes for a
        # reflection. All three are added as 0.26.0 added them.
        hull, mirrored = (150, 120, 250, 160), (150, 165, 250, 172)
        body, foot = (170, 60, 230, 200), (182, 201, 218, 225)
        wheel = (195, 201, 235, 220)
        cases = [
            ("blue sailboat", painted((235, 230, 215), (hull, (30, 50, 160)), (mirrored, (70, 90, 170))), hull, mirrored, (200, 168)),
            ("blue vase", painted((230, 225, 215), (body, (40, 80, 160)), (foot, (45, 85, 165))), body, foot, (200, 215)),
            ("green bin", painted((150, 150, 145), (BODY, (40, 90, 50)), (wheel, (35, 35, 35))), BODY, wheel, (215, 215)),
        ]
        for name, picture, chosen, below, inside in cases:
            with self.subTest(name=name):
                ask, segment, _asked, questions = model({question(name): [matte(chosen, below)], reflected(name): [matte(below)]})
                result = whole.complete(picture, matte(chosen), ask, segment, described=name)
                self.assertEqual(result.getpixel(inside), 255)
                self.assertEqual(questions, [question(name), strings(name)])
        measured = cast.measure(cases[2][1], matte(BODY), matte(wheel))
        self.assertTrue(measured["under"] and measured["unlike"] <= cast.RESEMBLE)


class CompleteTests(unittest.TestCase):
    def picture(self):
        return Image.new("RGB", SIZE, (120, 130, 120))

    def test_the_left_out_part_joins_the_selection(self):
        ask, segment, asked, questions = model({question("green bin"): [matte(BODY, WHEEL)]})
        result = whole.complete(self.picture(), matte(BODY), ask, segment)
        self.assertEqual(result.size, SIZE)
        self.assertEqual(result.getpixel((242, 192)), 255)   # the wheel
        self.assertEqual(result.getpixel((190, 140)), 255)   # the body is still selected
        self.assertEqual(result.getpixel((50, 45)), 0)
        self.assertEqual((len(asked), questions), (1, [question("green bin"), strings("green bin")]))

    def test_a_description_from_the_artist_needs_no_naming(self):
        ask, segment, asked, questions = model({question("green trash bin"): [matte(BODY, WHEEL)]})
        result = whole.complete(self.picture(), matte(BODY), ask, segment, described="The green trash bin.")
        self.assertEqual(result.getpixel((242, 192)), 255)
        self.assertEqual((asked, questions), ([], [question("green trash bin"), strings("green trash bin")]))

    def test_a_string_is_asked_for_by_name_and_joins_the_selection(self):
        # "Everything attached" returned the kite alone; asked for its string, the model finds it.
        ask, segment, _asked, questions = model({question("kite"): [matte(BODY)], strings("kite"): [matte(STRING)]}, names=("kite",))
        result = whole.complete(self.picture(), matte(BODY), ask, segment)
        self.assertEqual(result.getpixel((190, 260)), 255)   # the string
        self.assertEqual(result.getpixel((190, 140)), 255)
        self.assertEqual(result.getpixel((100, 260)), 0)
        self.assertEqual(questions, [question("kite"), strings("kite")])

    def test_only_the_string_joins_not_a_speck_from_the_same_answer(self):
        speck = (300, 250, 304, 254)
        ask, segment, _asked, _questions = model({question("kite"): [matte(BODY)], strings("kite"): [matte(STRING, speck)]}, names=("kite",))
        result = whole.complete(self.picture(), matte(BODY), ask, segment)
        self.assertEqual((result.getpixel((190, 260)), result.getpixel((302, 252))), (255, 0))

    def test_a_part_and_a_string_both_join(self):
        ask, segment, _asked, _questions = model({question("green bin"): [matte(BODY, WHEEL)], strings("green bin"): [matte(STRING)]})
        result = whole.complete(self.picture(), matte(BODY), ask, segment)
        self.assertEqual((result.getpixel((242, 192)), result.getpixel((190, 260))), (255, 255))

    def test_an_answer_that_is_no_string_adds_nothing(self):
        ask, segment, _asked, _questions = model({question("green bin"): [matte(BODY)], strings("green bin"): [matte(OTHER), matte((180, 200, 200, 290))]})
        self.assertIsNone(whole.complete(self.picture(), matte(BODY), ask, segment))

    def test_a_string_question_that_fails_keeps_the_parts(self):
        ask, segment, _asked, _questions = model({question("green bin"): [matte(BODY, WHEEL)]})

        def flaky(shown, description):
            if description == strings("green bin"):
                raise RuntimeError("timed out waiting for the backend")
            return segment(shown, description)

        result = whole.complete(self.picture(), matte(BODY), ask, flaky)
        self.assertEqual(result.getpixel((242, 192)), 255)

        def stopped(shown, description):
            if description == strings("green bin"):
                raise GenerationCancelled("Selection cancelled.")
            return segment(shown, description)

        with self.assertRaises(GenerationCancelled):
            whole.complete(self.picture(), matte(BODY), ask, stopped, described="green bin")

    def test_a_whole_selection_comes_back_as_none(self):
        ask, segment, _asked, _questions = model({question("green bin"): [matte(BODY)]})
        self.assertIsNone(whole.complete(self.picture(), matte(BODY), ask, segment))

    def test_a_wrong_name_gives_way_to_the_object_alone_on_grey(self):
        ask, segment, asked, questions = model({question("soccer ball"): [matte(OTHER)], question("yellow hose"): [matte(BODY, WHEEL)]},
                                               names=("soccer ball", "yellow hose"))
        result = whole.complete(self.picture(), matte(BODY), ask, segment)
        self.assertEqual(result.getpixel((242, 192)), 255)
        # the string is asked for only under the name that proved to be this object's
        self.assertEqual((len(asked), questions), (2, [question("soccer ball"), question("yellow hose"), strings("yellow hose")]))

    def test_no_answer_about_the_selection_means_none(self):
        ask, segment, _asked, questions = model({}, names=("thing", "other thing"))
        self.assertIsNone(whole.complete(self.picture(), matte(BODY), ask, segment))
        self.assertEqual(len(questions), 2)

    def test_the_whole_picture_or_nothing_selected_asks_nothing(self):
        ask, segment, asked, questions = model({})
        self.assertIsNone(whole.complete(self.picture(), Image.new("L", SIZE, 255), ask, segment))
        self.assertIsNone(whole.complete(self.picture(), Image.new("L", SIZE), ask, segment))
        self.assertEqual((asked, questions), ([], []))

    def test_a_cancel_stops_the_questions(self):
        ask, segment, _asked, questions = model({question("green bin"): [matte(BODY, WHEEL)]})
        self.assertIsNone(whole.complete(self.picture(), matte(BODY), ask, segment, cancelled=lambda: True))
        self.assertEqual(questions, [])

    def test_a_large_document_gets_its_parts_at_full_size(self):
        large = self.picture().resize((4000, 3000))
        chosen = matte(BODY).resize((4000, 3000))
        ask, segment, _asked, _questions = model({question("green bin"): [matte(BODY, WHEEL)]})
        result = whole.complete(large, chosen, ask, segment)
        self.assertEqual(result.size, (4000, 3000))
        self.assertEqual(result.getpixel((2420, 1920)), 255)


class EngineTests(unittest.TestCase):
    def helper(self, complete=None, fail=None):
        helper = wevisone.WeVisOne.__new__(wevisone.WeVisOne)
        helper._client = Mock(side_effect=fail) if fail else Mock(return_value=Mock())
        self.asked_with = {}

        def fake_questions(client, name, cancel, seconds):
            self.asked_with.update(seconds=seconds)
            return "ask", "segment"

        self.patches = [
            unittest.mock.patch.object(wevisone, "questions", fake_questions),
            unittest.mock.patch.object(wevisone, "encoder", lambda client: "enc"),
            unittest.mock.patch.object(whole, "complete", complete or (lambda *a, **k: None)),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        return helper

    def test_the_completed_selection_is_returned(self):
        better = matte(BODY, WHEEL)
        helper = self.helper(complete=lambda source, chosen, ask, segment, described, cancel: better)
        self.assertIs(helper.whole(Mock(), Image.new("RGB", SIZE), matte(BODY), "", lambda: False), better)
        # what is left of the budget after the engine was reached: all of it, less the moment that took
        self.assertAlmostEqual(self.asked_with["seconds"], whole.SECONDS, delta=1.0)
        self.assertLessEqual(self.asked_with["seconds"], whole.SECONDS)
        self.assertEqual(helper._client.call_args.kwargs, {"start": False})   # never starts an engine for this

    def test_nothing_to_add_returns_the_selection_itself(self):
        chosen = matte(BODY)
        self.assertIs(self.helper().whole(Mock(), Image.new("RGB", SIZE), chosen, "", lambda: False), chosen)

    def test_a_failure_leaves_the_selection_as_it_is(self):
        chosen = matte(BODY)
        for problem in (ValueError("This ComfyUI cannot return the selection model's answers in words."), OSError("refused"),
                        RuntimeError("timed out waiting for the backend")):
            self.assertIs(self.helper(fail=problem).whole(Mock(), Image.new("RGB", SIZE), chosen, "", lambda: False), chosen)

    def test_a_cancel_is_a_cancel(self):
        with self.assertRaises(GenerationCancelled):
            self.helper(fail=GenerationCancelled("Selection cancelled.")).whole(Mock(), Image.new("RGB", SIZE), matte(BODY), "", lambda: False)
        with self.assertRaises(RuntimeError):
            self.helper(fail=RuntimeError("cancelled by the user")).whole(Mock(), Image.new("RGB", SIZE), matte(BODY), "", lambda: True)


class SelectTests(unittest.TestCase):
    """``Selection.run`` up to the selection it loads into PhotoCraft."""

    def run_selection(self, params, completed=None):
        doc = {"name": "doc", "width": SIZE[0], "height": SIZE[1], "revision": 3, "hasSelection": False, "history": []}
        selection = Selection.__new__(Selection)
        selection.source = Image.new("RGB", SIZE)
        selection.key, selection.matte_key, selection.matte = "key", "", None
        first = matte(BODY)
        calls = {}

        def mask(app, source, query, point, progress, cancel, *, box=None):
            calls["mask"] = (query, point, box)
            return first

        def complete(app, source, chosen, described, cancel):
            calls["whole"] = described
            return completed if completed is not None else chosen

        selection.model = SimpleNamespace(mask=mask, whole=complete)
        loaded = {}
        bridge = Mock()
        bridge._open.side_effect = lambda guide, stem: (loaded.update(alpha=guide.getchannel("A")), (7, 70))[1]
        bridge.session.return_value = {"active": 7}
        bridge.client.call.return_value = {"document": doc, "session": {"active": 0}}
        bridge.client.run.return_value = doc
        selection.bridge = bridge
        from openfill_photocraft import selection as module

        with unittest.mock.patch.object(module, "key_for", lambda index, d: "key"):
            message = selection.run(Mock(), {"key": "key", "operation": "replace", **params}, lambda *a: None, lambda: False, lambda: None)
        return message, loaded["alpha"], calls

    def test_a_click_selects_the_object_with_its_parts(self):
        message, alpha, calls = self.run_selection({"action": "click", "point": [0.5, 0.5]}, completed=matte(BODY, WHEEL))
        self.assertEqual(message, "Object selected, with its attached parts.")
        self.assertEqual(alpha.getpixel((242, 192)), 255)
        self.assertEqual(calls["whole"], "")   # a click has no description: the model names the object

    def test_a_description_is_passed_on_as_the_objects_name(self):
        _message, _alpha, calls = self.run_selection({"action": "describe", "query": " the green bin "}, completed=matte(BODY, WHEEL))
        self.assertEqual(calls["whole"], "the green bin")

    def test_a_whole_object_reads_as_before(self):
        message, alpha, _calls = self.run_selection({"action": "click", "point": [0.5, 0.5]})
        self.assertEqual(message, "Object selected.")
        self.assertEqual(alpha.getpixel((242, 192)), 0)

    def test_the_option_turned_off_asks_nothing(self):
        message, alpha, calls = self.run_selection({"action": "click", "point": [0.5, 0.5], "whole": False}, completed=matte(BODY, WHEEL))
        self.assertEqual(message, "Object selected.")
        self.assertNotIn("whole", calls)
        self.assertEqual(alpha.getpixel((242, 192)), 0)


class StartTests(unittest.TestCase):
    """The option has to survive the job's own list of selection settings (it did not at first, and
    the end-to-end test was what noticed)."""

    def started(self, params):
        import threading

        from openfill_photocraft.service import JobState, Service

        svc = Service.__new__(Service)
        svc.state = JobState()
        svc.layers = []
        svc.app = Mock()
        svc.app.settings.remote_fibo = True
        svc.app_for = Mock(return_value=svc.app)
        svc.selection = Mock()
        svc.references = Mock()
        svc.busy = Mock(return_value=False)
        with unittest.mock.patch.object(threading, "Thread") as thread:
            svc._start("select", params)
        return thread.call_args.kwargs["args"][1], svc.selection.validate.call_args.args[0]

    def test_the_option_reaches_the_selection_job(self):
        job, checked = self.started({"action": "click", "key": "k", "point": [0.5, 0.5], "operation": "replace", "whole": False, "junk": 1})
        self.assertIs(job["whole"], False)
        self.assertIs(checked["whole"], False)
        self.assertNotIn("junk", job)
        job, _checked = self.started({"action": "click", "key": "k", "point": [0.5, 0.5]})
        self.assertNotIn("whole", job)   # left out, the selection asks for the parts

    def test_the_option_must_be_on_or_off(self):
        selection = Selection.__new__(Selection)
        selection.key, selection.source = "key", Image.new("RGB", SIZE)
        selection.bridge = Mock()
        doc = {"name": "doc", "width": SIZE[0], "height": SIZE[1], "revision": 1, "hasSelection": False}
        selection.bridge.client.call.return_value = {"document": doc, "session": {"active": 0}}
        from openfill_photocraft import selection as module

        with unittest.mock.patch.object(module, "key_for", lambda index, d: "key"):
            selection.validate({"action": "click", "key": "key", "point": [0.5, 0.5], "whole": True})
            with self.assertRaisesRegex(ValueError, "on or off"):
                selection.validate({"action": "click", "key": "key", "point": [0.5, 0.5], "whole": "yes"})


class AlongTheEdgeTests(unittest.TestCase):
    """A part that runs along the object's edge comes back whole (third round of review, 2026-11-25)."""

    def grey(self, size):
        return Image.new("RGB", size, (200, 200, 200))

    def run_complete(self, size, body, part, *more):
        """The share of ``part`` (outside the selection) that comes back, when the answer holds ``body``,
        ``part`` and the boxes ``more``."""
        chosen = matte(body, size=size)
        answer = matte(body, part, *more, size=size)
        ask, segment, _asked, _questions = model({question("skier"): [answer]}, names=("skier",))
        result = whole.complete(self.grey(size), chosen, ask, segment)
        missing = ImageChops.subtract(matte(part, size=size), chosen)
        got = ImageChops.darker(result, missing) if result is not None else Image.new("L", size)
        return got.histogram()[255] / max(1, missing.histogram()[255])

    def test_skis_right_under_the_boots_come_back_whole(self):
        size = (1536, 1024)
        for thick in (6, 7, 8):
            with self.subTest(thick=thick):
                share = self.run_complete(size, (700, 300, 780, 540), (660, 541, 820, 540 + thick))
                self.assertGreater(share, 0.95)

    def test_skis_under_a_skier_cut_off_by_the_pictures_edge_come_back_whole(self):
        # With the edge standing in for the top, the skis' box passed the other three sides by itself, the answer
        # was taken for a loose ring and cut at the slack band: 57% of the skis, 38% in the corner, 37% at the left
        # edge with a pole raised above the skier.
        cases = [
            ("a skier cut off at the top", (1536, 1024), (700, 0, 780, 540), (660, 541, 820, 547), []),
            ("a skier in the top left corner", (1536, 1024), (0, 0, 80, 540), (0, 541, 120, 547), []),
            ("a person cut off at the top, on a board", (800, 600), (350, 0, 430, 400), (320, 401, 460, 406), []),
            ("a skier at the left edge, a pole raised above", (800, 600), (0, 300, 80, 540), (0, 541, 120, 547), [(40, 240, 42, 299)]),
        ]
        for name, size, body, skis, more in cases:
            with self.subTest(name):
                self.assertGreater(self.run_complete(size, body, skis, *more), 0.95)

    def test_a_loose_ring_held_off_centre_at_the_pictures_edge_still_adds_nothing(self):
        # A ring 6 px looser, shifted 2 px toward a side it is then tight on, passes that side only at its corners,
        # inside the slack band: still a ring, unlike the skis above. Left in, it would add 300 to 400 px.
        width, height = size = (800, 600)
        cases = [
            ("bottom edge, shifted right", (350, height - 100, 449, height - 1), 6, (2, 0)),
            ("bottom edge, shifted up and right", (350, height - 100, 449, height - 1), 6, (2, -2)),
            ("top left corner, shifted up and right", (0, 0, 99, 99), 6, (2, -2)),
            ("bottom right corner, shifted down and left", (width - 100, height - 100, width - 1, height - 1), 6, (-2, 2)),
        ]
        for name, box, loose, (dx, dy) in cases:
            with self.subTest(name):
                chosen = matte(box, size=size)
                answer = matte((box[0] - loose + dx, box[1] - loose + dy, box[2] + loose + dx, box[3] + loose + dy), size=size)
                self.assertEqual(whole.parts(chosen, [answer]), (True, None))

    def test_a_knob_on_a_small_box_comes_back(self):
        share = self.run_complete(SIZE, (170, 120, 230, 180), (190, 110, 210, 119))
        self.assertGreater(share, 0.95)

    def test_skis_at_the_pictures_edge_come_back_whole_with_a_bag_held_beside_the_body(self):
        # A bag held on a free side lies along a fifth of the line beside it, which counted as a ring lying along that
        # side, and the skis were cut at the slack band: 0.39 of them in the corner, 0.58 of a board under a person cut
        # off at the top, none of skis no wider than the skier.
        size = (1536, 1024)
        cases = [
            ("a skier in the top left corner, a bag on the right", (0, 0, 79, 539), (0, 540, 120, 546), [(80, 300, 129, 420)]),
            ("a person cut off at the top on a board, a bag in each hand", (700, 0, 779, 539), (660, 540, 820, 546),
             [(650, 300, 699, 420), (780, 300, 829, 420)]),
            ("skis no wider than a skier in the corner, a bag on the right", (0, 0, 79, 539), (0, 540, 79, 546), [(80, 300, 129, 420)]),
        ]
        for name, body, skis, bags in cases:
            with self.subTest(name):
                self.assertGreater(self.run_complete(size, body, skis, *bags), 0.95)
                self.assertGreater(self.run_complete(size, body, bags[0], skis, *bags[1:]), 0.95)   # and the bag

    def test_a_loose_ring_with_a_handle_still_brings_only_the_handle(self):
        from openfill_photocraft.local_removal import grow_support

        size = (800, 600)
        square = matte((385, 285, 415, 315), size=size)
        handle = (416, 296, 440, 304)
        for loose in (6, 7):
            with self.subTest(loose=loose):
                answer = ImageChops.lighter(grow_support(square, loose), matte(handle, size=size))
                ask, segment, _asked, _questions = model({question("mug"): [answer]}, names=("mug",))
                result = whole.complete(Image.new("RGB", size, (200, 200, 200)), square, ask, segment)
                self.assertIsNotNone(result)
                self.assertEqual(result.getpixel((435, 300)), 255)       # the handle
                self.assertEqual(result.getpixel((380, 300)), 0)         # the ring's far side stays out


class HaloTests(unittest.TestCase):
    """A strings answer that goes round the object is the object held loosely, not its string; and an object at
    the picture's edge has no ring past that edge, so an answer looser on its other sides adds nothing."""

    size = (800, 600)

    def added(self, chosen, parts_answer, strings_answer):
        """What ``complete`` adds to ``chosen`` with these two answers."""
        ask, segment, _asked, _questions = model({question("thing"): [parts_answer], strings("thing"): [strings_answer]}, names=("thing",))
        result = whole.complete(Image.new("RGB", chosen.size, (120, 130, 120)), chosen, ask, segment, described="thing")
        return Image.new("L", chosen.size) if result is None else ImageChops.subtract(whole._solid(result), chosen)

    def shape(self, shape, box, loose):
        """The object and an answer ``loose`` px looser all round (cut off where it meets the picture's edge)."""
        if shape == "disc":
            return disc(box, self.size), disc((box[0] - loose, box[1] - loose, box[2] + loose, box[3] + loose), self.size)
        chosen = matte(box, size=self.size)
        return chosen, grow_support(chosen, loose)

    def test_a_strings_answer_that_holds_the_object_loosely_is_no_string(self):
        # The model may answer with the object itself, a little looser, when there is no string. Past the slack band
        # that leaves a ring 1 to 4 px wide, long and thin enough for a line, and _kept took the whole loose ring
        # along (+39% of a 100 px square 9 px looser).
        for shape, side, loose in (("square", 100, 9), ("disc", 100, 10), ("square", 150, 9), ("square", 150, 10), ("square", 200, 9),
                                   ("square", 200, 10), ("square", 300, 10), ("square", 300, 12), ("disc", 300, 12), ("square", 400, 11)):
            chosen, looser = self.shape(shape, (200, 100, 199 + side, 99 + side), loose)
            with self.subTest(shape=shape, side=side, loose=loose):
                self.assertIsNone(whole.strand(chosen, [looser]))
                self.assertEqual(whole._count(self.added(chosen, chosen, looser)), 0)

    def test_a_strings_answer_that_holds_an_object_at_the_edge_loosely_is_no_string(self):
        height = self.size[1]
        for where, box in (("bottom", (350, height - 100, 449, height - 1)), ("top", (350, 0, 449, 99)),
                           ("left", (0, 250, 99, 349)), ("corner", (0, height - 100, 99, height - 1))):
            chosen, looser = self.shape("square", box, 9)
            with self.subTest(where=where):
                self.assertIsNone(whole.strand(chosen, [looser]))
                self.assertEqual(whole._count(self.added(chosen, chosen, looser)), 0)

    def test_a_string_that_runs_off_one_side_is_still_found_at_the_edge_too(self):
        # Where the picture's edge stands in for two or three sides, a lone string's box passes the rest; it still
        # only crosses the line round the object where it leaves, and a ring lies along that line.
        width = self.size[0]
        corner = matte((0, 0, 99, 99), size=self.size)
        diagonal = Image.new("L", self.size)
        ImageDraw.Draw(diagonal).line((99, 99, 450, 450), fill=255, width=2)
        cases = [
            ("a dog, its leash to the right", matte((300, 250, 399, 349), size=self.size), matte((400, 300, 700, 301), size=self.size)),
            ("a kite at the top edge", matte((300, 0, 399, 99), size=self.size), matte((350, 100, 351, 590), size=self.size)),
            ("a dog at the bottom edge, its leash to the right", matte((300, 500, 399, 599), size=self.size), matte((400, 540, 700, 541), size=self.size)),
            ("an object in a corner, the string straight down", corner, matte((50, 100, 51, 590), size=self.size)),
            ("an object in a corner, the string from its free corner", corner, ImageChops.subtract(diagonal, corner)),
            ("a banner on three edges, the string down", matte((0, 0, width - 1, 60), size=self.size), matte((400, 61, 401, 400), size=self.size)),
        ]
        for name, chosen, string in cases:
            with self.subTest(name):
                answer = ImageChops.lighter(chosen, string)
                self.assertIsNotNone(whole.strand(chosen, [answer]))
                added = self.added(chosen, chosen, answer)
                self.assertEqual(whole._count(ImageChops.darker(added, string)), whole._count(string))   # all of it

    def test_a_strings_answer_held_off_centre_is_no_string(self):
        # Shifted a few pixels, a disc's loose answer leaves nothing past the slack band on the side it is tight on,
        # and the crescent left does not go round the object; it was added as a string (+2410 px, 31% of a 100 px
        # disc). These are the 17 of 192 such answers that still passed for a string.
        for side, loose, dx, dy in ((100, 9, 3, 0), (100, 9, 2, 2), (100, 10, 2, 0), (100, 10, 3, 0), (100, 10, 0, 2), (100, 10, -2, 0),
                                    (100, 10, 2, 2), (150, 10, 2, 0), (150, 10, 3, 0), (150, 10, 0, 2), (150, 10, -2, 0), (150, 10, 2, 2),
                                    (150, 11, 3, 0), (200, 10, 3, 0), (200, 10, 2, 2), (200, 11, 3, 0), (300, 11, 3, 0)):
            box = (200, 100, 199 + side, 99 + side)
            chosen = disc(box, self.size)
            looser = disc((box[0] - loose + dx, box[1] - loose + dy, box[2] + loose + dx, box[3] + loose + dy), self.size)
            with self.subTest(side=side, loose=loose, shift=(dx, dy)):
                self.assertIsNone(whole.strand(chosen, [looser]))
        chosen = disc((200, 100, 299, 199), self.size)
        self.assertEqual(whole._count(self.added(chosen, chosen, disc((192, 90, 311, 209), self.size))), 0)

    def test_several_strings_in_one_answer_all_come(self):
        # Together they reach past every side of the object and lie beside two or more of them, as a ring does, and
        # were turned down whole; each crosses the line round the object once, where it leaves.
        def rope(*points):
            mask = Image.new("L", self.size)
            ImageDraw.Draw(mask).line(points, fill=255, width=2)
            return mask

        hull, moored, pole = matte((250, 300, 450, 350), size=self.size), matte((0, 300, 200, 360), size=self.size), matte((390, 0, 409, 599), size=self.size)
        cases = [
            ("a hull's bow and stern lines and its stay", hull, [rope(249, 330, 120, 520), rope(451, 330, 600, 520), rope(350, 299, 350, 80)]),
            ("a hull at the left edge, its rigging and its mooring rope", moored, [rope(100, 299, 100, 60), rope(201, 340, 400, 560)]),
            ("a pole across the picture, cables off both sides", pole, [matte((100, y, 389, y + 1), size=self.size) for y in (60, 90)]
             + [matte((410, y, 700, y + 1), size=self.size) for y in (60, 90)]),
        ]
        for name, chosen, lines in cases:
            with self.subTest(name):
                wanted = ImageChops.subtract(whole._union(self.size, lines), chosen)
                answer = ImageChops.lighter(chosen, wanted)
                self.assertIsNotNone(whole.strand(chosen, [answer]))
                added = self.added(chosen, chosen, answer)
                self.assertEqual(whole._count(ImageChops.darker(added, wanted)), whole._count(wanted))   # all of them

    def test_a_loose_answer_round_an_object_at_the_pictures_edge_adds_nothing(self):
        # Only what went past the selection's box on all four sides was taken for a loose ring, which an object at
        # the edge never has: a 100 px one at the bottom got 1872 px of ring from an answer 6 px looser.
        width, height = self.size
        for side in (30, 100):
            places = {"bottom": (350, height - side), "top": (350, 0), "left": (0, 250), "right": (width - side, 250),
                      "top left": (0, 0), "bottom right": (width - side, height - side)}
            for where, (x, y) in places.items():
                for loose in (6, 8):
                    for shape in ("square", "disc"):
                        chosen, answer = self.shape(shape, (x, y, x + side - 1, y + side - 1), loose)
                        with self.subTest(side=side, where=where, loose=loose, shape=shape):
                            self.assertEqual(whole.parts(chosen, [answer]), (True, None))
        chosen = matte((350, height - 100, 449, height - 1), size=self.size)
        self.assertEqual(whole._count(self.added(chosen, grow_support(chosen, 6), chosen)), 0)

    def test_a_loose_answer_round_an_object_just_off_the_pictures_edge_adds_nothing(self):
        # 1 to 5 px from the edge the object touches no edge, so the box decided; the answer, cut off by the edge, never
        # passes the near side past the rim, and its ring was kept whole: 570 px added to a 30 px disc 1 px off the left
        # edge, 718 px with an answer 7 px looser 3 px off it, 1650 px to a 100 px disc.
        _width, height = self.size
        for box, looser in (((1, 250, 30, 279), (-5, 244, 36, 285)), ((3, 250, 32, 279), (-4, 243, 39, 286)),
                            ((1, 250, 100, 349), (-6, 243, 107, 356)), ((350, height - 31, 379, height - 2), (344, height - 37, 385, height + 4)),
                            ((2, height - 32, 31, height - 3), (-4, height - 38, 37, height + 3))):
            chosen, answer = disc(box, self.size), disc(looser, self.size)
            with self.subTest(box=box):
                self.assertEqual(whole.parts(chosen, [answer]), (True, None))
                self.assertEqual(whole._count(self.added(chosen, answer, chosen)), 0)

    def test_a_thin_part_off_the_tight_side_of_a_ring_held_off_centre_at_the_edge_comes_alone(self):
        # Held a pixel or two off centre, the ring is tight on the side the rod leaves from and lies nowhere along it; the
        # rod passing that side far out made the answer no ring, and the whole ring came with the rod (+678 px for the
        # mug, 342 of them on its far side), where the same object away from the edge got only the bit at the root.
        _width, height = self.size
        mug, rod = (385, height - 30, 415, height - 1), (416, height - 16, 440, height - 14)
        big = (1536, 1024)
        cases = [
            ("a mug at the bottom edge, ring shifted left", self.size, mug, (378, height - 36, 420, height - 1), rod),
            ("a mug at the bottom edge, ring shifted 2 px left", self.size, mug, (377, height - 36, 419, height - 1), rod),
            ("a box at the bottom edge of a large frame", big, (700, 724, 779, 1023), (693, 718, 784, 1023), (780, 824, 840, 826)),
            ("an object in the bottom left corner, the rod to the right", self.size, (0, height - 100, 99, height - 1),
             (0, height - 106, 104, height - 1), (100, height - 52, 160, height - 50)),
            ("an object in the bottom left corner, the rod up", self.size, (0, height - 100, 99, height - 1),
             (0, height - 105, 105, height - 1), (48, height - 160, 50, height - 101)),
            ("an object at the left edge, the rod down", self.size, (0, 250, 99, 349), (0, 243, 105, 354), (48, 350, 50, 410)),
        ]
        for name, size, body, ring, part in cases:
            with self.subTest(name):
                chosen, part = matte(body, size=size), matte(part, size=size)
                added = self.added(chosen, ImageChops.lighter(matte(ring, size=size), part), chosen)
                self.assertEqual(whole._count(ImageChops.darker(added, part)), whole._count(part))   # the part, whole
                self.assertEqual(whole._count(ImageChops.subtract(added, grow_support(part, 14))), 0)   # and of the ring only its root
        kite, string = matte((300, 0, 399, 99), size=self.size), matte((350, 100, 351, 190), size=self.size)
        for bottom in (104, 103):   # a strings answer 6 px looser, held 1 or 2 px up: tight on the side the string leaves
            with self.subTest(kite=bottom):
                added = self.added(kite, kite, ImageChops.lighter(matte((294, 0, 405, bottom), size=self.size), string))
                self.assertEqual(whole._count(ImageChops.darker(added, string)), whole._count(string))
                self.assertEqual(whole._count(ImageChops.subtract(added, grow_support(string, 14))), 0)

    def test_a_part_of_an_object_at_the_pictures_edge_is_still_added(self):
        width, height = self.size
        mug, handle = matte((385, height - 30, 415, height - 1), size=self.size), matte((416, height - 20, 440, height - 10), size=self.size)
        for loose in (0, 6, 7):
            ring = grow_support(mug, loose) if loose else mug
            with self.subTest(loose=loose):
                added = self.added(mug, ImageChops.lighter(ring, handle), mug)
                self.assertEqual(whole._count(ImageChops.darker(added, handle)), whole._count(handle))   # the handle, whole
                self.assertEqual(whole._count(added.crop((0, 0, 400, height))), 0)   # and nothing of the ring's far side
        corner = matte((0, 0, 99, 99), size=self.size)
        oar = Image.new("L", self.size)
        ImageDraw.Draw(oar).line((99, 99, 300, 300), fill=255, width=6)
        cases = [
            ("an oar from the free corner of an object in a corner", corner, ImageChops.subtract(oar, corner)),
            ("skis under the boots of a skier at the left edge", matte((0, 300, 80, 540), size=self.size), matte((0, 541, 120, 547), size=self.size)),
            ("a post under a banner on three edges", matte((0, 0, width - 1, 60), size=self.size), matte((390, 61, 410, 300), size=self.size)),
        ]
        for name, chosen, part in cases:
            with self.subTest(name):
                added = self.added(chosen, ImageChops.lighter(chosen, part), chosen)
                self.assertGreaterEqual(whole._count(ImageChops.darker(added, part)), 0.95 * whole._count(part))


class AlongsideTests(unittest.TestCase):
    """A string can lie along the object a few pixels off it before it runs away, and several can leave it at a shallow
    angle. Neither is the object held loosely, which is filled from the object out (fifth round of review, 2026-11-26)."""

    size = (1536, 1024)

    def added(self, chosen, strings_answer):
        ask, segment, _asked, _questions = model({question("thing"): [chosen], strings("thing"): [strings_answer]}, names=("thing",))
        result = whole.complete(Image.new("RGB", self.size, (120, 130, 120)), chosen, ask, segment, described="thing")
        return Image.new("L", self.size) if result is None else ImageChops.subtract(whole._solid(result), chosen)

    def line(self, points, width):
        mask = Image.new("L", self.size)
        ImageDraw.Draw(mask).line(points, fill=255, width=width)
        return mask

    def test_a_cord_lying_along_the_object_before_it_runs_away_comes_whole(self):
        # Each lies on a quarter to two thirds of the line just past the slack band round the object, and the whole
        # string was turned down as the object held loosely: 3177, 1919, 8875 and 1582 px lost.
        size = self.size
        strap = ImageChops.lighter(self.line([(600, 420), (590, 420), (590, 610), (910, 610), (910, 420), (900, 420)], 8),
                                   self.line([(590, 610), (300, 900)], 8))
        cases = [
            ("a kettle's cord along its base, 10 px below it", matte((600, 400, 900, 700), (900, 450, 960, 600), size=size),
             self.line([(905, 690), (910, 710), (600, 710), (0, 770)], 4)),
            ("a leash along a lying dog's belly", matte((400, 500, 800, 600), (780, 440, 870, 540), size=size),
             self.line([(790, 535), (790, 610), (450, 610), (300, 900)], 3)),
            ("a strap curled round a camera, 10 px off", matte((600, 400, 900, 600), size=size), strap),
            ("a halyard 13 px off its pole, tied at the top", matte((700, 100, 711, 900), size=size), matte((725, 105, 726, 880), (712, 103, 726, 104), size=size)),
        ]
        for name, chosen, cord in cases:
            cord = ImageChops.subtract(cord, chosen)
            for answer in (ImageChops.lighter(chosen, cord), cord):
                with self.subTest(name, with_object=answer is not cord):
                    self.assertIsNotNone(whole.strand(chosen, [answer]))
                    self.assertGreaterEqual(whole._count(ImageChops.darker(self.added(chosen, answer), cord)), 0.99 * whole._count(cord))

    def test_guy_lines_leaving_a_tent_at_a_shallow_angle_all_come(self):
        # Each line covers its width over the sine of its angle of the line round the tent, and four together passed HUGS.
        tent = Image.new("L", self.size)
        ImageDraw.Draw(tent).polygon([(700, 300), (500, 600), (900, 600)], fill=255)
        wall = math.atan2(300, 200)
        angled = Image.new("L", self.size)
        for off in (10, 20):
            for sign in (-1, 1):
                end = (700 + sign * 420 * math.cos(wall - math.radians(off)), 300 + 420 * math.sin(wall - math.radians(off)))
                ImageDraw.Draw(angled).line([(700, 300), end], fill=255, width=3)
        staked = Image.new("L", self.size)
        for stake in ((380, 640), (1020, 640), (430, 650), (970, 650)):
            ImageDraw.Draw(staked).line([(700, 300), stake], fill=255, width=2)
        # Within the slack band a line 4 degrees off a wall cannot be told from a loose ring, and loses what of it runs
        # there more than the reach back from where it leaves the band (see _kept).
        for name, lines, least in (("10 and 20 degrees off the walls, 3 px", angled, 0.95), ("to four stakes, 4 to 10 degrees off, 2 px", staked, 0.85)):
            lines = ImageChops.subtract(lines, tent)
            with self.subTest(name):
                self.assertGreaterEqual(whole._count(ImageChops.darker(self.added(tent, ImageChops.lighter(tent, lines)), lines)),
                                        least * whole._count(lines))

    def test_a_strings_answer_that_holds_the_object_loosely_with_a_cord_is_still_no_string(self):
        # Filled from the object out, a loose answer still lies along the line round it, a cord beside it or not.
        chosen = matte((600, 400, 900, 700), size=self.size)
        cord = ImageChops.subtract(self.line([(905, 690), (910, 710), (600, 710), (0, 770)], 4), chosen)
        for loose in (9, 10):
            with self.subTest(loose=loose):
                answer = ImageChops.lighter(grow_support(chosen, loose), cord)
                self.assertIsNone(whole.strand(chosen, [answer]))


if __name__ == "__main__":
    unittest.main()
