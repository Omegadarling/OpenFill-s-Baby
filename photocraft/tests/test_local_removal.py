# SPDX-License-Identifier: Apache-2.0
"""Regressions for large removals on smooth backgrounds and object-selection fringes, and Local interpolating first
in the direction its surroundings are layered."""

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import local_removal  # noqa: E402
from openfill_photocraft.bridge import PhotoCraftBridge  # noqa: E402
from openfill_photocraft.local_removal import (  # noqa: E402
    SMOOTH_DIRECTIONS,
    grow_support,
    interpolation_direction,
    removal_mask,
    ring_direction,
    smooth_background,
    smooth_directions,
    texture_donors,
)
from openfill_photocraft.removal_blend import match_h3_lighting  # noqa: E402
from openfill_photocraft.removal_structure import structured_background  # noqa: E402

from openfill.backend import GenerationCancelled  # noqa: E402

SIZE = (320, 240)
HOLE = (130, 90, 190, 150)


def hole(size=SIZE, box=HOLE):
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


def sweep(size=SIZE):
    """A studio sweep: the tone changes from top to bottom, every row one colour."""
    image = Image.new("RGB", size)
    image.putdata([(120 + y // 2, 70 + y // 3, 40) for y in range(size[1]) for _ in range(size[0])])
    return image


def horizon(size=SIZE):
    """A sweep with a bright band across it where floor meets wall, through the hole."""
    image = Image.new("RGB", size)
    rows = [round(70 * math.exp(-(((y - 120) / 10) ** 2))) for y in range(size[1])]
    image.putdata([(150 + rows[y], 80 + rows[y] // 2, 40) for y in range(size[1]) for _ in range(size[0])])
    return image


def side_lit(size=SIZE):
    """A wall lit from the left: the tone changes from left to right, every column one colour."""
    image = Image.new("RGB", size)
    image.putdata([(220 - x // 3, 225 - x // 3, 210 - x // 3) for _ in range(size[1]) for x in range(size[0])])
    return image


class LocalRemovalTests(unittest.TestCase):
    def test_removal_keeps_the_opacity_of_a_wide_feather_and_low_density_selection(self):
        mask = Image.new("L", (240, 140))
        ImageDraw.Draw(mask).rectangle((60, 25, 180, 115), fill=255)
        soft = mask.filter(ImageFilter.GaussianBlur(12))
        prepared = removal_mask(soft)
        self.assertEqual(prepared.getpixel((0, 70)), 0)
        self.assertEqual(prepared.getpixel((120, 70)), 255)
        ramp = [prepared.getpixel((x, 70)) for x in range(15, 90)]
        self.assertGreater(len(set(v for v in ramp if 8 < v < 247)), 24)
        self.assertEqual(ramp, sorted(ramp))
        self.assertLessEqual(removal_mask(soft.point(lambda v: v // 2)).getextrema()[1], 127)

    def test_reconstructs_one_dark_seam_with_matching_ends(self):
        size = (360, 260)
        sky = Image.new("RGBA", size, (190, 192, 197, 255))
        mask = Image.new("L", size)
        ImageDraw.Draw(mask).ellipse((120, 69, 240, 196), fill=255)
        for both_ends in (False, True):
            scene = sky.copy()
            ImageDraw.Draw(scene).line(((0, 130), (359 if both_ends else 175, 130)), fill=(30, 35, 40), width=3)
            ImageDraw.Draw(scene).ellipse((124, 73, 236, 192), fill=(10, 45, 58))
            repair = structured_background(scene, removal_mask(mask), Mock(), lambda: False)
            if not both_ends:
                self.assertIsNone(repair)
                continue
            self.assertIsNotNone(repair)
            self.assertLess(repair.getpixel((180, 130))[0], 65)
            self.assertGreater(repair.getpixel((180, 117))[0], 170)
            self.assertEqual(repair.getpixel((40, 130)), scene.getpixel((40, 130))[:3])

    def test_h3_lighting_retains_rendered_detail_and_exact_exterior(self):
        plate = Image.new("RGB", (160, 120), (170, 176, 182))
        mask = Image.new("L", plate.size)
        ImageDraw.Draw(mask).rectangle((25, 20, 135, 100), fill=255)
        proposal = plate.copy()
        proposal.paste((185, 191, 197), (25, 20, 136, 101))
        ImageDraw.Draw(proposal).line((78, 40, 78, 80), fill=(112, 118, 124), width=2)
        result = match_h3_lighting(plate, proposal, mask)
        self.assertEqual(result.getpixel((10, 60)), plate.getpixel((10, 60)))
        self.assertLessEqual(abs(result.getpixel((50, 60))[0] - 170), 1)
        self.assertGreater(result.getpixel((73, 60))[0] - result.getpixel((78, 60))[0], 45)

    def test_sampling_excludes_unrelated_colors_patch_edges_and_transparency(self):
        sky = (23, 70, 84, 255)
        for color, intruder in ((sky, (240, 243, 244, 255)), ((209, 216, 204, 255), (37, 47, 59, 255))):
            with self.subTest(color=color):
                scene = Image.new("RGBA", (140, 110), color)
                mask = Image.new("L", scene.size)
                ImageDraw.Draw(mask).polygon([(35, 30), (96, 20), (89, 46), (50, 77)], fill=255)
                draw = ImageDraw.Draw(scene)
                draw.rectangle((103, 73, 128, 95), fill=intruder)
                draw.rectangle((4, 80, 30, 97), fill=(*color[:3], 0))
                scene.paste(intruder, mask=mask)
                donors = texture_donors(scene, mask)
                self.assertIsNotNone(donors)
                for point in ((69, 39), (110, 78), (102, 73), (6, 81)):
                    self.assertEqual(donors.getpixel(point), 0)
                self.assertEqual(donors.getpixel((20, 22)), 255)

    def test_multicolored_rim_keeps_both_sides_available(self):
        scene = Image.new("RGBA", (130, 110), (20, 22, 25, 255))
        ImageDraw.Draw(scene).rectangle((65, 0, 130, 110), fill=(225, 177, 145, 255))
        mask = Image.new("L", scene.size)
        ImageDraw.Draw(mask).ellipse((45, 20, 85, 80), fill=255)
        self.assertIsNone(texture_donors(scene, mask))
        self.assertIsNone(texture_donors(scene, Image.new("L", scene.size, 255)))

    def test_expansion_matches_a_maximum_filter_and_keeps_opposite_edges_empty(self):
        mask = Image.new("L", (37, 29))
        ImageDraw.Draw(mask).rectangle((0, 4, 9, 14), fill=255)
        for radius in (1, 2, 5, 8):
            with self.subTest(radius=radius):
                actual = grow_support(mask, radius)
                self.assertEqual(actual.tobytes(), mask.filter(ImageFilter.MaxFilter(2 * radius + 1)).tobytes())
                self.assertEqual(actual.getpixel((36, 4)), 0)
        prepared = removal_mask(mask)
        self.assertEqual(ImageChops.subtract(mask, prepared).getbbox(), None)
        self.assertEqual(prepared.getpixel((36, 4)), 0)
        self.assertTrue(any(0 < value < 255 for value in prepared.getdata()))

    def test_large_foreground_on_a_gradient_has_no_ghost_or_blocky_patch(self):
        size = (128, 128)
        plate = Image.new("RGB", size)
        plate.putdata([(50 + x, 80 + y // 2, 35 + (x + y) // 3) for y in range(128) for x in range(128)])
        source = plate.copy()
        ImageDraw.Draw(source).rounded_rectangle((35, 17, 88, 111), radius=12, fill=(12, 32, 218))
        selection = Image.new("L", size)
        ImageDraw.Draw(selection).rounded_rectangle((35, 17, 88, 111), radius=12, fill=255)
        selected = removal_mask(selection)
        repaired = smooth_background(source.convert("RGBA"), selected, Mock(), lambda: False)
        self.assertIsNotNone(repaired)
        error = ImageStat.Stat(ImageChops.difference(repaired, plate), selection).mean
        self.assertLess(max(error), 2.5)
        output = Image.composite(repaired, source, selected)
        exterior = ImageOps.invert(selected.point(lambda value: 255 if value else 0))
        self.assertEqual(
            Image.composite(ImageChops.difference(output, source), Image.new("RGB", size), exterior).getbbox(), None
        )

    def test_visible_texture_uses_the_texture_path(self):
        source = Image.new("RGBA", (128, 128), "white")
        draw = ImageDraw.Draw(source)
        for x in range(0, 128, 8):
            draw.rectangle((x, 0, x + 3, 127), fill=(50, 80, 130))
        mask = Image.new("L", source.size)
        ImageDraw.Draw(mask).rectangle((40, 35, 85, 95), fill=255)
        self.assertIsNone(smooth_background(source, mask, Mock(), lambda: False))

    def test_cancellation_and_no_background(self):
        source = Image.new("RGBA", (64, 64), (180, 130, 40))
        mask = Image.new("L", source.size)
        ImageDraw.Draw(mask).rectangle((20, 20, 42, 42), fill=255)
        with self.assertRaises(GenerationCancelled):
            smooth_background(source, mask, Mock(), lambda: True)
        self.assertIsNone(smooth_background(source, Image.new("L", source.size, 255), Mock(), lambda: False))


class DirectionTests(unittest.TestCase):
    def test_horizontal_layers_interpolate_along_rows(self):
        self.assertGreater(ring_direction(sweep(), hole()), local_removal.LAYERED)
        self.assertEqual(interpolation_direction(sweep(), hole()), local_removal.LAYERED_WEIGHT)

    def test_a_side_lit_wall_interpolates_along_columns(self):
        self.assertLess(ring_direction(side_lit(), hole()), 1 - local_removal.LAYERED)
        self.assertAlmostEqual(interpolation_direction(side_lit(), hole()), 1 - local_removal.LAYERED_WEIGHT)

    def test_a_flat_or_undecided_surface_keeps_the_even_weight(self):
        flat = Image.new("RGB", SIZE, (180, 180, 180))
        self.assertIsNone(ring_direction(flat, hole()))
        self.assertEqual(interpolation_direction(flat, hole()), 0.5)
        diagonal = Image.new("RGB", SIZE)
        diagonal.putdata([((x + y) // 3,) * 3 for y in range(SIZE[1]) for x in range(SIZE[0])])
        self.assertEqual(interpolation_direction(diagonal, hole()), 0.5)
        self.assertEqual(smooth_directions(flat, hole()), SMOOTH_DIRECTIONS)

    def test_the_suggested_direction_comes_first_without_a_near_twin(self):
        order = smooth_directions(sweep(), hole())
        self.assertEqual(order[0], local_removal.LAYERED_WEIGHT)
        self.assertNotIn(0.85, order)  # nearly the same repair as 0.9
        self.assertEqual(len(order), len(SMOOTH_DIRECTIONS))
        order = smooth_directions(side_lit(), hole())
        self.assertAlmostEqual(order[0], 0.1)
        self.assertNotIn(0.15, order)

    def test_only_the_ring_counts(self):
        # Stripes far from the hole, past the ring, do not turn a side-lit wall into layers.
        image = side_lit()
        draw = ImageDraw.Draw(image)
        for y in range(0, 40, 8):
            draw.rectangle((0, y, SIZE[0], y + 3), fill=(30, 30, 30))
        self.assertAlmostEqual(interpolation_direction(image, hole()), 0.1)

    def test_transparent_pixels_are_not_read(self):
        image = sweep().convert("RGBA")
        clear = Image.new("L", SIZE, 255)
        ImageDraw.Draw(clear).rectangle((0, 0, SIZE[0], SIZE[1] // 2), fill=0)
        image.putalpha(clear)
        self.assertGreater(ring_direction(image, hole()), local_removal.LAYERED)

    def test_the_chosen_direction_keeps_the_sweeps_layers(self):
        # Across a sweep's bright band, interpolating along the rows rebuilds each row's own tone;
        # the even weight pulls tone in from above and below and breaks the band (the patch the
        # artist rated 1 star on the chef's sweep).
        source, selection = horizon().convert("RGBA"), hole()
        quiet = (lambda *_: None), (lambda: False)
        along = smooth_background(source, selection, *quiet, horizontal=interpolation_direction(source, selection))
        even = smooth_background(source, selection, *quiet, horizontal=0.5)
        truth = horizon()

        def error(result):
            return sum(
                abs(a - b)
                for x in range(HOLE[0] + 5, HOLE[2] - 5, 4)
                for y in range(HOLE[1] + 5, HOLE[3] - 5, 4)
                for a, b in zip(result.getpixel((x, y)), truth.getpixel((x, y)), strict=True)
            )

        self.assertEqual(interpolation_direction(source, selection), local_removal.LAYERED_WEIGHT)
        self.assertLess(error(along), error(even) * 0.5)


class BridgeTests(unittest.TestCase):
    """Local's own repair follows the surroundings; the clean plate for an AI method keeps its order."""

    def sample(self, **options):
        from openfill_photocraft import bridge as module

        smooth_calls, structured_calls = [], []

        def smooth(source, alpha, progress, cancelled, *, horizontal=0.5, boundary_only=False):
            smooth_calls.append(horizontal)
            return Image.new("RGB", source.size, (128, 128, 128))

        def structured(source, alpha, progress, cancelled, *, horizontal=0.5):
            structured_calls.append(horizontal)
            return None

        bridge = PhotoCraftBridge.__new__(PhotoCraftBridge)
        bridge._sample_patches = Mock(return_value=[b"patches"])
        with (
            patch.object(module, "smooth_background", smooth),
            patch.object(module, "structured_background", structured),
        ):
            source = sweep().convert("RGBA")
            bridge._sample_texture(Mock(), source, hole(), 2, 7, lambda *a: None, lambda: False, **options)
        return smooth_calls, structured_calls

    def test_locals_repair_interpolates_along_the_sweep_first(self):
        smooth, structured = self.sample(follow_surroundings=True)
        self.assertEqual(smooth, [local_removal.LAYERED_WEIGHT, 0.5])
        self.assertEqual(structured, list(SMOOTH_DIRECTIONS[:2]))  # the line-continuing repair is unchanged

    def test_the_clean_plate_keeps_the_fixed_order(self):
        smooth, structured = self.sample()
        self.assertEqual(smooth, list(SMOOTH_DIRECTIONS[:2]))
        self.assertEqual(structured, list(SMOOTH_DIRECTIONS[:2]))

    def test_remove_with_texture_follows_the_surroundings(self):
        bridge = PhotoCraftBridge.__new__(PhotoCraftBridge)
        selection = Image.new("L", SIZE)
        ImageDraw.Draw(selection).ellipse(HOLE, fill=255)
        bridge._snapshot = Mock(return_value=(sweep().convert("RGBA"), selection))
        bridge._sample_texture = Mock(return_value=[])
        bridge.remove_with_texture(Mock(), 1, 7, lambda *a: None, lambda: False, smooth_only=True)
        self.assertTrue(bridge._sample_texture.call_args.kwargs["follow_surroundings"])
