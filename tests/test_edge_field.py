# SPDX-License-Identifier: Apache-2.0
"""Edge matching for removals: the fill is matched to the original across the selection edge."""

import unittest
from types import SimpleNamespace

from openfill import quality
from openfill.app import App
from openfill.geometry import Rect

W, H = 200, 160
HOLE = (60, 40, 140, 120)


def inside(x, y):
    return HOLE[0] <= x < HOLE[2] and HOLE[1] <= y < HOLE[3]


def picture(colour_at):
    data = bytearray()
    for y in range(H):
        for x in range(W):
            data += bytes((*colour_at(x, y), 255))
    return bytes(data)


def selection():
    return bytes(255 if inside(x, y) else 0 for y in range(H) for x in range(W))


def bricks(x, y):
    mortar = y % 16 < 2 or (x + (y // 16 % 2) * 24) % 48 < 2
    return (117, 112, 105) if mortar else (151, 84, 65)


def mean_inside(data):
    sums, n = [0, 0, 0], 0
    for y in range(HOLE[1] + 8, HOLE[3] - 8):
        for x in range(HOLE[0] + 8, HOLE[2] - 8):
            i = 4 * (y * W + x)
            for c in range(3):
                sums[c] += data[i + c]
            n += 1
    return [s / n for s in sums]


def corrected(original, layer):
    field = quality.edge_field(W, H, original, layer, selection(), 4)
    return field, (layer if field is None else quality.apply_seam_field(layer, W, H, 4, field))


class EdgeFieldTests(unittest.TestCase):
    def test_a_lighter_fill_on_a_flat_wall_is_matched_to_the_wall(self):
        wall = (120, 130, 140)
        original = picture(lambda x, y: (0, 0, 0) if inside(x, y) else wall)  # the object in the hole
        layer = picture(lambda x, y: (132, 136, 141) if inside(x, y) else wall)
        field, fixed = corrected(original, layer)
        self.assertIsNotNone(field)
        for got, want in zip(mean_inside(fixed), wall, strict=True):
            self.assertAlmostEqual(got, want, delta=1.5)

    def test_a_tinted_fill_on_bricks_is_matched_through_the_texture(self):
        original = picture(lambda x, y: (20, 20, 200) if inside(x, y) else bricks(x, y))
        layer = picture(
            lambda x, y: (
                tuple(v + d for v, d in zip(bricks(x, y), (10, 6, -4), strict=True)) if inside(x, y) else bricks(x, y)
            )
        )
        before = mean_inside(layer)
        _field, fixed = corrected(original, layer)
        after = mean_inside(fixed)
        expected = mean_inside(picture(bricks))
        for b, a, e in zip(before, after, expected, strict=True):
            self.assertLess(abs(a - e), abs(b - e) / 3)

    def test_a_fill_that_already_matches_is_left_alone(self):
        wall = (90, 100, 110)
        original = picture(lambda x, y: (250, 0, 0) if inside(x, y) else wall)
        layer = picture(lambda x, y: wall)
        self.assertIsNone(quality.edge_field(W, H, original, layer, selection(), 4))

    def test_the_centre_of_a_large_selection_is_left_alone(self):
        # 400 x 400 hole: the fill is lighter only near the edge; deep inside it already matches.
        size, hole = 600, (100, 100, 500, 500)
        wall = (100, 110, 120)
        within = lambda x, y: hole[0] <= x < hole[2] and hole[1] <= y < hole[3]  # noqa: E731
        near_edge = lambda x, y: min(x - hole[0], hole[2] - 1 - x, y - hole[1], hole[3] - 1 - y) < 40  # noqa: E731

        def make(colour_at):
            return bytes(v for y in range(size) for x in range(size) for v in (*colour_at(x, y), 255))

        original = make(lambda x, y: (0, 0, 0) if within(x, y) else wall)
        layer = make(lambda x, y: (112, 118, 124) if within(x, y) and near_edge(x, y) else wall)
        mask = bytes(255 if within(x, y) else 0 for y in range(size) for x in range(size))
        field = quality.edge_field(size, size, original, layer, mask, 4)
        self.assertIsNotNone(field)
        fixed = quality.apply_seam_field(layer, size, size, 4, field)
        centre = 4 * (300 * size + 300)
        self.assertEqual(tuple(fixed[centre : centre + 3]), wall)
        edge = 4 * (300 * size + 110)
        for got, want in zip(fixed[edge : edge + 3], wall, strict=True):
            self.assertAlmostEqual(got, want, delta=2)

    def test_a_large_difference_is_content_and_not_corrected(self):
        wall = (40, 40, 40)
        original = picture(lambda x, y: (0, 0, 0) if inside(x, y) else wall)
        layer = picture(lambda x, y: (200, 200, 200) if inside(x, y) else wall)
        self.assertIsNone(quality.edge_field(W, H, original, layer, selection(), 4))


class FakeLayer:
    def __init__(self, rgba):
        self.rgba = rgba

    def get_width(self):
        return W

    def get_height(self):
        return H


class MatchRemovalEdgeTests(unittest.TestCase):
    def test_the_app_corrects_each_full_size_layer_once(self):
        wall = (120, 130, 140)
        original = picture(lambda x, y: (0, 0, 0) if inside(x, y) else wall)
        layer = FakeLayer(picture(lambda x, y: (132, 136, 141) if inside(x, y) else wall))
        events = []
        io = SimpleNamespace(
            layer_rgba=lambda target: target.rgba, set_layer_rgba=lambda target, data: setattr(target, "rgba", data)
        )
        image = SimpleNamespace(
            undo_group_start=lambda: events.append("start"), undo_group_end=lambda: events.append("end")
        )
        app_like = SimpleNamespace(
            io=io, log=SimpleNamespace(info=lambda *a: None, warning=lambda *a: events.append("warning"))
        )
        App._match_removal_edge(app_like, image, [layer], Rect(0, 0, W, H), original, selection())
        self.assertEqual(events, ["start", "end"])
        for got, want in zip(mean_inside(layer.rgba), wall, strict=True):
            self.assertAlmostEqual(got, want, delta=1.5)


if __name__ == "__main__":
    unittest.main()
