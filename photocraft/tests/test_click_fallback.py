# SPDX-License-Identifier: Apache-2.0
"""A click on an object the model's list of objects left out still selects it: a close-up, then a question."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import wevisone  # noqa: E402
from openfill_photocraft.wevisone import DEFAULT_QUERY, POINT_QUESTION, WeVisOne, close_up, cut_off  # noqa: E402

from openfill.backend import GenerationCancelled  # noqa: E402

SIZE = (2000, 1000)
SWAN = (900, 400, 1100, 600)   # around the click at the picture's centre


def matte(size, *boxes):
    mask = Image.new("L", size)
    for box in boxes:
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def at(result, x, y):
    """The matte's value at a point given in the picture's pixels (a matte may come back at the model's size)."""
    return result.getpixel((x * result.width // SIZE[0], y * result.height // SIZE[1]))


def helper(instances, answers=None, named=None):
    """A WeVisOne whose model returns ``instances[n]`` for its n-th list of objects, answers a point
    question with ``answers`` and selects ``named`` for the name it gave."""
    model = WeVisOne.__new__(WeVisOne)
    model._client = Mock(return_value=Mock())
    calls = []
    replies = iter(instances)

    def listed(client, name, source, query, progress, cancel, *, cache=True):
        calls.append((source.size, query, cache))
        return next(replies)

    model._instances = listed
    asked = []

    def fake_questions(client, name, cancel, seconds):
        if isinstance(answers, BaseException):
            raise answers

        def ask(picture, question):
            asked.append(question)
            return answers

        def segment(picture, description):
            asked.append(description)
            return [m.resize(picture.size) for m in (named or [])]

        return ask, segment

    patches = [patch.object(wevisone, "questions", fake_questions), patch.object(wevisone, "encoder", lambda client: "enc")]
    return model, calls, asked, patches


class GeometryTests(unittest.TestCase):
    def test_the_close_up_is_a_square_around_the_click_inside_the_picture(self):
        self.assertEqual(close_up(SIZE, (0.5, 0.5)), (600, 100, 1400, 900))
        self.assertEqual(close_up(SIZE, (0.0, 0.0)), (0, 0, 800, 800))
        self.assertEqual(close_up(SIZE, (0.99, 0.99)), (1200, 200, 2000, 1000))
        self.assertEqual(close_up((100, 80), (0.5, 0.5)), (18, 8, 82, 72))

    def test_a_matte_that_runs_into_the_close_ups_edge_is_cut_off(self):
        inside = matte((800, 800), (300, 300, 500, 500))
        at_left = matte((800, 800), (0, 300, 200, 500))
        self.assertFalse(cut_off(inside, (True, True, True, True)))
        self.assertTrue(cut_off(at_left, (True, False, False, False)))
        self.assertFalse(cut_off(at_left, (False, True, True, True)))   # there the picture itself ends
        self.assertFalse(cut_off(Image.new("L", (8, 8)), (True, True, True, True)))


class ClickTests(unittest.TestCase):
    def select(self, model, patches, point=(0.5, 0.5), query=DEFAULT_QUERY):
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        said = []
        result = model.mask(Mock(), Image.new("RGB", SIZE), query, point, lambda fraction, text: said.append(text), lambda: False)
        return result, said

    def test_an_object_in_the_list_is_selected_as_before(self):
        model, calls, asked, patches = helper([[matte(SIZE, SWAN)]])
        result, said = self.select(model, patches)
        self.assertEqual(result.getpixel((1000, 500)), 255)
        self.assertEqual((len(calls), asked, said), (1, [], []))

    def test_a_click_the_list_missed_is_found_in_the_close_up(self):
        # In the close-up (800 px square from x=600, y=100) the swan sits at 300..500.
        model, calls, asked, patches = helper([[], [matte((800, 800), (300, 300, 500, 500))]])
        result, said = self.select(model, patches)
        self.assertEqual(result.size, SIZE)
        self.assertEqual(result.getpixel((1000, 500)), 255)
        self.assertEqual(result.getpixel((620, 120)), 0)
        self.assertEqual(result.getpixel((100, 500)), 0)
        self.assertEqual([c[0] for c in calls], [(1536, 768), (800, 800)])
        self.assertIs(calls[1][2], False)   # a close-up is not the cached full picture
        self.assertEqual((asked, said), ([], ["Looking closer at that point…"]))

    def test_an_object_the_close_up_cuts_off_is_looked_for_in_a_wider_one(self):
        # The first close-up is 800 px square from (600, 100): the man runs out of it at the top. The wider one is
        # the picture's full height, 1400 px wide from x=300: he fits.
        cut = matte((800, 800), (300, 0, 500, 700))
        fits = matte((1400, 1000), (600, 50, 800, 900))
        model, calls, asked, patches = helper([[], [cut], [fits]])
        result, said = self.select(model, patches)
        self.assertEqual(result.size, SIZE)
        self.assertEqual(result.getpixel((1000, 60)), 255)    # his head, above the first close-up
        self.assertEqual(result.getpixel((1000, 950)), 0)
        self.assertEqual([c[0] for c in calls], [(1536, 768), (800, 800), (1400, 1000)])
        self.assertEqual((asked, said), ([], ["Looking closer at that point…"] * 2))

    def test_an_object_both_close_ups_cut_off_is_asked_for_by_name(self):
        cut = matte((800, 800), (0, 300, 500, 500))        # runs out of the close-up on the left
        wider = matte((1400, 1000), (0, 400, 800, 600))    # and out of the wider one
        whole = matte(SIZE, (100, 400, 1100, 600))
        model, _calls, asked, patches = helper([[], [cut], [wider]], answers="<ref>A golden dog</ref><box><1><2><3><4></box>", named=[whole])
        result, said = self.select(model, patches)
        self.assertEqual(at(result, 150, 500), 255)   # the part no close-up could show
        self.assertEqual(asked, [POINT_QUESTION.format(x=500, y=500), "the golden dog"])
        self.assertEqual(said, ["Looking closer at that point…"] * 2 + ["Asking what is at that point…"])

    def test_a_name_that_is_only_a_piece_of_the_object_gives_way_to_what_the_close_up_showed(self):
        cut = matte((800, 800), (0, 200, 500, 700))        # a large thing, cut on the left by both close-ups
        wider = matte((1400, 1000), (0, 300, 800, 800))
        piece = matte(SIZE, (920, 420, 1080, 560))
        model, _calls, asked, patches = helper([[], [cut], [wider]], answers="<ref>A blue shirt</ref>", named=[piece])
        result, _said = self.select(model, patches)
        self.assertEqual(asked[1], "the blue shirt")
        self.assertEqual(result.size, SIZE)
        self.assertEqual(result.getpixel((400, 700)), 255)   # more than the piece: the thing as far as it was seen

    def test_nothing_in_the_close_up_is_asked_for_by_name(self):
        model, _calls, asked, patches = helper([[], []], answers="<ref>The white swan</ref>", named=[matte(SIZE, SWAN)])
        result, _said = self.select(model, patches)
        self.assertEqual(at(result, 1000, 500), 255)
        self.assertEqual(asked[1], "the white swan")

    def test_a_cut_off_part_is_better_than_nothing(self):
        cut = matte((800, 800), (0, 300, 500, 500))
        wider = matte((1400, 1000), (0, 400, 800, 600))
        for answers, selected in (("<ref>A dog</ref>", []), (ValueError("This ComfyUI cannot return the selection model's answers in words."), None),
                                  ("", None)):
            model, _calls, _asked, patches = helper([[], [cut], [wider]], answers=answers, named=selected)
            result, _said = self.select(model, patches)
            self.assertEqual(result.getpixel((1000, 500)), 255)
            self.assertEqual(result.getpixel((250, 500)), 0)   # beyond the wider close-up nothing is known

    def test_nothing_anywhere_says_so_as_before(self):
        model, _calls, _asked, patches = helper([[], []], answers="<ref>A cloud</ref>", named=[matte(SIZE, (0, 0, 50, 50))])
        with self.assertRaisesRegex(ValueError, "No object found at that point"):
            self.select(model, patches)

    def test_a_click_with_a_description_typed_keeps_to_the_description(self):
        model, calls, asked, patches = helper([[]])
        with self.assertRaisesRegex(wevisone.NothingFound, "No object found at that point"):   # Selection.run then tries without it
            self.select(model, patches, query="the cup")
        self.assertEqual((len(calls), asked), (1, []))

    # The listed matte comes back at the model's frame, as the named one does.
    FRAME = (1536, 768)
    SUBJECT = (150, 0, 1385, 767)   # a woman filling 80% of the picture, the click on her

    def test_a_small_thing_on_the_pictures_main_subject_is_selected(self):
        earring = matte(SIZE, (990, 490, 1010, 510))
        model, calls, asked, patches = helper([[matte(self.FRAME, self.SUBJECT)]], answers="<ref>A gold hoop earring</ref>", named=[earring])
        result, said = self.select(model, patches)
        self.assertEqual(at(result, 1000, 500), 255)
        self.assertEqual(at(result, 400, 500), 0)   # the rest of her
        self.assertEqual(asked, [POINT_QUESTION.format(x=500, y=500), "the gold hoop earring"])
        self.assertEqual((len(calls), said), (1, ["Asking what is at that point…"]))

    def test_a_name_that_is_a_piece_of_the_subject_keeps_the_subject(self):
        shirt = matte(SIZE, (500, 300, 1500, 900))   # 0.4 of her: a piece, not a thing on her
        model, _calls, _asked, patches = helper([[matte(self.FRAME, self.SUBJECT)]], answers="<ref>A blue shirt</ref>", named=[shirt])
        result, _said = self.select(model, patches)
        self.assertEqual(at(result, 400, 500), 255)

    def test_no_answer_about_the_point_keeps_the_subject(self):
        for answers, named in (("", None), (ValueError("This ComfyUI cannot return the selection model's answers in words."), None),
                               ("<ref>A cloud</ref>", [matte(SIZE, (0, 0, 50, 50))])):
            with self.subTest(answers=answers):
                model, _calls, _asked, patches = helper([[matte(self.FRAME, self.SUBJECT)]], answers=answers, named=named)
                result, _said = self.select(model, patches)
                self.assertEqual(at(result, 400, 500), 255)
        model, _calls, _asked, patches = helper([[matte(self.FRAME, self.SUBJECT)]], answers=GenerationCancelled("Selection cancelled."))
        with self.assertRaises(GenerationCancelled):
            self.select(model, patches)

    def test_a_listed_object_under_a_third_of_the_picture_is_not_asked_about(self):
        quarter = matte(self.FRAME, (384, 0, 768, 767))   # 25% of the picture, the click on its edge
        model, _calls, asked, patches = helper([[quarter]], answers="<ref>A button</ref>", named=[matte(SIZE, (990, 490, 1010, 510))])
        result, said = self.select(model, patches)
        self.assertEqual(at(result, 700, 500), 255)
        self.assertEqual((asked, said), ([], []))

    def test_a_description_alone_never_looks_closer(self):
        model, calls, _asked, patches = helper([[]])
        with self.assertRaisesRegex(ValueError, "No matching object found"):
            self.select(model, patches, point=None, query="the cup")
        self.assertEqual(len(calls), 1)

    def test_a_small_picture_has_no_close_up_and_goes_straight_to_the_question(self):
        model = WeVisOne.__new__(WeVisOne)
        model._client = Mock(return_value=Mock())
        sizes = []
        model._instances = lambda client, name, source, query, progress, cancel, *, cache=True: (sizes.append(source.size), [])[1]
        small = (70, 70)   # the 64-pixel close-up would be nearly all of it

        def fake_questions(client, name, cancel, seconds):
            return (lambda picture, question: "<ref>A coin</ref>"), (lambda picture, description: [matte(small, (25, 25, 45, 45))])

        with patch.object(wevisone, "questions", fake_questions), patch.object(wevisone, "encoder", lambda client: "enc"):
            result = model.mask(Mock(), Image.new("RGB", small), DEFAULT_QUERY, (0.5, 0.5), lambda *a: None, lambda: False)
        self.assertEqual(result.getpixel((35, 35)), 255)
        self.assertEqual(sizes, [small])

    def test_a_cancel_while_asking_is_a_cancel(self):
        model, _calls, _asked, patches = helper([[], []], answers=GenerationCancelled("Selection cancelled."))
        with self.assertRaises(GenerationCancelled):
            self.select(model, patches)


if __name__ == "__main__":
    unittest.main()
