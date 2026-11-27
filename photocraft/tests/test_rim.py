# SPDX-License-Identifier: Apache-2.0
"""Remove takes the object's own rim and contact shadow that lie just outside the selection, and nothing else."""

import math
import random
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import rim  # noqa: E402

SIZE = (400, 300)
WALL = (196, 202, 188)
PLATE = (150, 100, 210, 190)            # a 60 x 90 switch plate
SHADOW_LEFT = (139, 104, 149, 196)      # its shaded edge and shadow, 10 px wide, outside the selection
SHADOW_BELOW = (145, 191, 206, 199)     # and along the bottom
STICKER = (160, 110, 240, 190)          # an 80 x 80 sticker with a dark border
RIM_TOLD = 0.03                         # service.py tells the artist the rim went from this share of the selection


def count(mask, box=None):
    """Pixels set in ``mask`` (inside ``box`` when given, edges included as ImageDraw draws them)."""
    part = mask.crop((box[0], box[1], box[2] + 1, box[3] + 1)) if box else mask
    return part.point(lambda v: 255 if v >= 128 else 0).histogram()[255]


def area(box):
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def matte(*boxes, size=SIZE):
    mask = Image.new("L", size)
    for box in boxes:
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def wall(size=SIZE, grain=3, seed=7):
    """A painted wall: one colour with a little grain, as photographed."""
    rng = random.Random(seed)
    noise = Image.frombytes("L", size, bytes(128 + rng.randint(-grain, grain) for _ in range(size[0] * size[1])))
    base = Image.new("RGB", size, WALL)
    return ImageChops.add(base, Image.merge("RGB", [noise] * 3), offset=-128)


def plate_on_wall(shadows=(SHADOW_LEFT, SHADOW_BELOW)):
    image = wall()
    draw = ImageDraw.Draw(image)
    for box in shadows:
        draw.rectangle(box, fill=(118, 122, 112))
    draw.rectangle(PLATE, fill=(244, 244, 240))
    return image


def graded(direction, size=SIZE, top=240, bottom=90, grain=3, seed=7):
    """A studio sweep from ``top`` to ``bottom`` levels, down the picture, across it or corner
    to corner, with a little grain."""
    ramp = Image.linear_gradient("L")
    across = ramp.rotate(90)
    shade = {"down": ramp, "across": across, "diagonal": ImageChops.add(ramp, across, scale=2)}[direction]
    shade = shade.resize(size, Image.Resampling.BILINEAR).point(lambda v: round(top + (bottom - top) * v / 255))
    noise = Image.frombytes("L", size, random.Random(seed).randbytes(size[0] * size[1]))
    noise = noise.point(lambda v: 128 - grain + v * (2 * grain + 1) // 256)
    return ImageChops.add(Image.merge("RGB", [shade] * 3), Image.merge("RGB", [noise] * 3), offset=-128)


def on_graded(direction, size, box, fill):
    """An oval object exactly selected on a graded backdrop: the picture and the selection."""
    picture, selection = graded(direction, size), Image.new("L", size)
    ImageDraw.Draw(picture).ellipse(box, fill=fill)
    ImageDraw.Draw(selection).ellipse(box, fill=255)
    return picture, selection


def dome(centre, radius, size=SIZE, peak=235, grey=130, grain=3, seed=5):
    """A white dome (a clock's face) on a grey wall: its shading falls from ``peak`` at the centre
    to the grey at its rim as a squared cosine, with a little grain."""
    rng = random.Random(seed)
    levels = []
    for y in range(size[1]):
        for x in range(size[0]):
            fall = math.cos(min(1.0, math.hypot(x - centre[0], y - centre[1]) / radius) * math.pi / 2) ** 2
            levels.append(max(0, min(255, round(grey + (peak - grey) * fall + rng.randint(-grain, grain)))))
    shade = Image.new("L", size)
    shade.putdata(levels)
    return Image.merge("RGB", [shade] * 3)


def distance(selection, upto=80):
    """How many px (counted 8 ways) each pixel lies outside ``selection``, up to ``upto``; 0 inside."""
    away = Image.new("L", selection.size, 255)
    reached = selection.point(lambda v: 255 if v >= 128 else 0)
    away.paste(0, mask=reached)
    for step in range(1, upto + 1):
        grown = reached.filter(ImageFilter.MaxFilter(3))
        away.paste(step, mask=ImageChops.subtract(grown, reached))
        reached = grown
    return away


def soft_shadow(picture, selection, depth, reach, where=None):
    """``picture`` with a soft contact shadow round ``selection``: ``depth`` levels darker beside
    it, fading evenly to nothing ``reach`` px out (only where ``where`` is set, when given)."""
    amount = distance(selection).point(lambda v: 0 if v == 0 or v >= reach else round(depth * (1 - v / reach)))
    if where is not None:
        amount = ImageChops.darker(amount, where)
    return ImageChops.subtract(picture, Image.merge("RGB", [amount] * 3))


def taken(added, region):
    """The share of ``region`` that ``added`` covers."""
    if added is None:
        return 0.0
    return count(ImageChops.darker(added, region)) / count(region)


def plate_beyond(plate, blur, size=SIZE):
    """A white plate on the wall, its photographed outline blurred ``blur`` px."""
    cover = matte(plate, size=size)
    if blur:
        cover = cover.filter(ImageFilter.GaussianBlur(blur))
    return Image.composite(Image.new("RGB", size, (244, 244, 240)), wall(size), cover)


def busy(size=SIZE, seed=3):
    """Gravel: strong, fine-grained texture in many colours."""
    rng = random.Random(seed)
    small = Image.new("RGB", (size[0] // 4, size[1] // 4))
    small.putdata([(rng.randint(40, 220), rng.randint(40, 220), rng.randint(40, 220)) for _ in range(small.width * small.height)])
    return small.resize(size, Image.Resampling.NEAREST)


class RimTests(unittest.TestCase):
    def test_the_plates_shadow_just_outside_the_selection_is_taken(self):
        picture, selection = plate_on_wall(), matte(PLATE)
        added, stats = rim.find(picture, selection)
        self.assertIsNotNone(added)
        shadow = count(added, SHADOW_LEFT) + count(added, SHADOW_BELOW)
        self.assertGreater(shadow, 0.8 * (area(SHADOW_LEFT) + area(SHADOW_BELOW) - 6 * 9))
        # Nothing of the plain wall beyond it: everything taken lies within 3 px of the shadow.
        near = matte((SHADOW_LEFT[0] - 3, SHADOW_LEFT[1] - 3, SHADOW_BELOW[2] + 3, SHADOW_BELOW[3] + 3))
        self.assertLessEqual(count(ImageChops.subtract(added, near)), 0)
        self.assertLessEqual(stats["share"], rim.CAP)

    def test_widen_returns_the_selection_with_the_shadow(self):
        picture, selection = plate_on_wall(), matte(PLATE)
        wider = rim.widen(picture.convert("RGBA"), selection)
        self.assertEqual(wider.size, SIZE)
        self.assertEqual(ImageChops.subtract(selection, wider).getbbox(), None)   # nothing of the selection is lost
        self.assertGreater(count(wider), count(selection) * 1.15)

    def test_a_clean_selection_on_a_plain_wall_takes_nothing(self):
        picture = plate_on_wall(shadows=())
        self.assertIsNone(rim.widen(picture, matte(PLATE)))

    def test_a_clean_selection_on_busy_surroundings_takes_nothing(self):
        picture = busy()
        ImageDraw.Draw(picture).rectangle(PLATE, fill=(244, 244, 240))
        self.assertIsNone(rim.widen(picture, matte(PLATE)))

    def test_a_dark_fringe_on_busy_surroundings_is_left_alone(self):
        # Gravel is no surface to compare with: a dark edge there could be the next stone.
        picture = busy()
        draw = ImageDraw.Draw(picture)
        draw.rectangle(SHADOW_LEFT, fill=(30, 30, 30))
        draw.rectangle(PLATE, fill=(244, 244, 240))
        self.assertIsNone(rim.widen(picture, matte(PLATE)))

    def test_the_addition_is_capped(self):
        # A small object in a dark frame as wide as the band: far more than a rim, so only the
        # part nearest the object is taken, up to the cap.
        picture = wall()
        draw = ImageDraw.Draw(picture)
        draw.rectangle((172, 122, 228, 178), fill=(110, 114, 106))
        draw.rectangle((180, 130, 220, 170), fill=(244, 244, 240))
        selection = matte((180, 130, 220, 170))
        added, stats = rim.find(picture, selection)
        self.assertTrue(stats["capped"])
        self.assertGreater(count(added), 0)
        self.assertLessEqual(count(added), count(selection) * (rim.CAP + 0.05))

    def test_a_neighbour_beside_the_selection_is_not_taken(self):
        # A dark bar touching the plate and running on to the picture's edge is another object,
        # not the plate's shadow.
        picture = plate_on_wall(shadows=())
        ImageDraw.Draw(picture).rectangle((211, 130, 399, 160), fill=(70, 60, 50))
        added, _ = rim.find(picture, matte(PLATE))
        self.assertEqual(count(added, (211, 125, 399, 165)) if added else 0, 0)

    def test_a_stripe_of_the_rug_is_not_taken(self):
        # Many-coloured surroundings: stripes of a rug run under the object and out past it.
        picture = Image.new("RGB", SIZE)
        draw = ImageDraw.Draw(picture)
        for index, x in enumerate(range(0, SIZE[0], 24)):
            draw.rectangle((x, 0, x + 23, SIZE[1]), fill=((200, 40, 40), (40, 90, 200), (230, 220, 200))[index % 3])
        draw.rectangle(PLATE, fill=(120, 80, 40))
        self.assertIsNone(rim.widen(picture, matte(PLATE)))

    def test_a_light_patch_that_is_not_the_object_is_not_taken(self):
        # A cloud beside a kite, a ledge's highlight under a cat: lighter than the surface and
        # unlike the object, so neither a shadow nor a rim.
        picture = plate_on_wall(shadows=())
        draw = ImageDraw.Draw(picture)
        draw.rectangle(PLATE, fill=(40, 40, 40))
        draw.rectangle(SHADOW_LEFT, fill=(250, 252, 245))
        self.assertIsNone(rim.widen(picture, matte(PLATE)))

    def test_the_objects_own_edge_left_outside_is_taken(self):
        # The selection stops 3 px inside a white plate: the strip left outside is lighter than
        # the wall, so no shadow, but it looks like the plate's own edge, so it is the rim.
        picture = wall()
        ImageDraw.Draw(picture).rectangle((147, 100, 210, 190), fill=(244, 244, 240))
        added, _ = rim.find(picture, matte(PLATE))
        self.assertGreater(count(added, (147, 100, 149, 190)), 0.8 * area((147, 100, 149, 190)))
        self.assertEqual(count(added, (120, 100, 146, 190)), 0)   # and the wall beside it stays

    def test_an_exact_selection_on_a_graded_backdrop_takes_nothing(self):
        # A sweep from light to dark: the ring lies further out than the band, so its flat
        # average is lighter than the band on one side (a "shadow") and darker on the other.
        for direction in ("down", "across", "diagonal"):
            for fill in ((150, 100, 60), (244, 244, 240), (40, 40, 40)):
                with self.subTest(direction=direction, fill=fill):
                    picture, selection = on_graded(direction, (600, 450), (180, 135, 420, 315), fill)
                    self.assertIsNone(rim.widen(picture, selection))

    def test_the_studio_sweep_of_the_report_takes_nothing(self):
        # 240 at the top to 90 at the bottom over 3000 px, about 0.05 levels a pixel, round a
        # large object exactly selected: worked small, the sweep is several levels across the ring.
        picture, selection = on_graded("down", (2000, 3000), (500, 1000, 1500, 2000), (150, 100, 60))
        self.assertIsNone(rim.widen(picture, selection))

    def test_the_sloped_surface_follows_a_graded_backdrop(self):
        # Where the flat average is several levels off the backdrop beside the object, the plane
        # fitted to the ring reads it to within the grain.
        picture, selection = on_graded("down", (600, 450), (180, 135, 420, 315), (150, 100, 60))
        trace = {}
        rim.find(picture, selection, trace)
        band, image = trace["band"], trace["image"].convert("L")

        def mean_off(surface):
            diff = ImageChops.difference(image, surface.convert("L"))
            values = [d for d, b in zip(diff.getdata(), band.getdata(), strict=False) if b]
            return sum(values) / len(values)

        self.assertGreater(mean_off(trace["surface"]), 5)
        self.assertLess(mean_off(trace["sloped"]), 3)

    def test_a_contact_shadow_on_a_graded_backdrop_is_still_taken(self):
        picture = graded("down")
        for box in (SHADOW_LEFT, SHADOW_BELOW):
            region = picture.crop((box[0], box[1], box[2] + 1, box[3] + 1))
            picture.paste(ImageChops.subtract(region, Image.new("RGB", region.size, (60, 60, 60))), box[:2])
        ImageDraw.Draw(picture).rectangle(PLATE, fill=(244, 244, 240))
        added, _ = rim.find(picture, matte(PLATE))
        self.assertIsNotNone(added)
        shadow = count(added, SHADOW_LEFT) + count(added, SHADOW_BELOW)
        self.assertGreater(shadow, 0.8 * (area(SHADOW_LEFT) + area(SHADOW_BELOW) - 6 * 9))
        near = matte((SHADOW_LEFT[0] - 3, SHADOW_LEFT[1] - 3, SHADOW_BELOW[2] + 3, SHADOW_BELOW[3] + 3))
        self.assertLessEqual(count(ImageChops.subtract(added, near)), 0)

    def test_a_soft_shadow_with_a_long_tail_on_a_plain_wall_is_taken(self):
        # 60 levels beside the plate, fading to nothing 50 px out: the tail reaches into the ring,
        # which so grows lighter outward on every side. That is the shadow, not a graded wall.
        selection = matte(PLATE)
        picture = soft_shadow(wall(), selection, 60, 50)
        ImageDraw.Draw(picture).rectangle(PLATE, fill=(244, 244, 240))
        added, _ = rim.find(picture, selection)
        away = distance(selection)
        self.assertGreater(taken(added, away.point(lambda v: 255 if 0 < v <= 3 else 0)), 0.8)
        self.assertEqual(count(ImageChops.subtract(added, away.point(lambda v: 255 if 0 < v < 50 else 0))), 0)

    def test_a_soft_shadow_on_one_side_is_taken_on_a_wall_and_on_a_sweep(self):
        # Lit from the top right: the shadow lies left of the plate and below it, fading out over 30 px.
        selection = matte(PLATE)
        where = matte((0, PLATE[1] + 4, PLATE[0] - 1, SIZE[1] - 1), (PLATE[0] - 60, PLATE[3] + 1, PLATE[2] - 4, SIZE[1] - 1))
        beside = ImageChops.darker(distance(selection).point(lambda v: 255 if 0 < v <= 3 else 0), where)
        for name, backdrop in (("wall", wall()), ("sweep", graded("across"))):
            with self.subTest(backdrop=name):
                picture = soft_shadow(backdrop, selection, 40, 30, where)
                ImageDraw.Draw(picture).rectangle(PLATE, fill=(244, 244, 240))
                added, _ = rim.find(picture, selection)
                self.assertGreater(taken(added, beside), 0.9)

    def test_a_loose_selection_on_a_dome_takes_next_to_nothing(self):
        # A sticker on a clock's white dome, lassoed 4 px loose: the selection's edge lies on the
        # dome, whose shading falls away towards the grey beyond it, so the band beside the
        # selection looks like the selection's edge and unlike the ring. It is neither the
        # sticker's edge nor its shadow: a stray pixel or two may go, never enough to tell the artist.
        # Lassoed 8 px loose, the dome lies a little way inside the selection's edge too, but the
        # sticker's border and colour further in are nothing like it.
        for radius, below in ((90, 0), (90, 30), (130, 0), (130, 30), (200, 0)):
            picture = dome((200, 170 + below), radius)
            ImageDraw.Draw(picture).rectangle(STICKER, fill=(240, 175, 50), outline=(30, 20, 10), width=2)
            for loose in (4, 8):
                with self.subTest(radius=radius, below=below, loose=loose):
                    _, stats = rim.find(picture, matte((STICKER[0] - loose, STICKER[1] - loose, STICKER[2] + loose, STICKER[3] + loose)))
                    self.assertLess(stats["share"], RIM_TOLD)
            with self.subTest(radius=radius, below=below, loose=0):
                self.assertIsNone(rim.widen(picture, matte(STICKER)))     # selected exactly, nothing at all

    def test_the_objects_own_edge_and_its_shadow_beyond_are_taken(self):
        # The selection stops 3 px inside a white plate whose shadow lies beyond its edge: the
        # strip of plate gives way to the shadow, not the wall, and both are taken.
        picture = wall()
        draw = ImageDraw.Draw(picture)
        draw.rectangle((139, 100, 146, 196), fill=(118, 122, 112))
        draw.rectangle((147, 100, 210, 190), fill=(244, 244, 240))
        added, _ = rim.find(picture, matte(PLATE))
        self.assertGreater(count(added, (147, 100, 149, 190)), 0.8 * area((147, 100, 149, 190)))
        self.assertGreater(count(added, (139, 104, 146, 190)), 0.8 * area((139, 104, 146, 190)))
        self.assertEqual(count(added, (100, 100, 135, 190)), 0)

    def test_the_objects_own_edge_is_taken_when_its_outline_is_soft(self):
        # A photographed outline fades over a pixel or two: between the strip of plate left
        # outside the selection and the wall lie pixels that are neither, so the wall comes back
        # a little further out than the strip is wide. The strip is still the plate's own edge.
        for left, blur in ((146, 1.0), (147, 1.5), (146, 1.5)):
            with self.subTest(left=left, blur=blur):
                added, _ = rim.find(plate_beyond((left, 100, 210, 190), blur), matte(PLATE))
                strip = (left, 100, PLATE[0] - 1, PLATE[3])
                self.assertGreater(taken(added, matte(strip)), 0.9)
                self.assertEqual(taken(added, matte((100, 90, left - 3, 200))), 0)     # and the wall beyond stays
        # All round, the selection 2 to 4 px short of a soft outline on every side.
        selection = matte(PLATE)
        away = distance(selection)
        for short, blur in ((2, 2.0), (3, 1.5), (4, 1.0)):
            with self.subTest(short=short, blur=blur):
                plate = (PLATE[0] - short, PLATE[1] - short, PLATE[2] + short, PLATE[3] + short)
                added, _ = rim.find(plate_beyond(plate, blur), selection)
                self.assertGreater(taken(added, away.point(lambda v, short=short: 255 if 0 < v <= short else 0)), 0.9)
                self.assertEqual(taken(added, away.point(lambda v, short=short: 255 if v > short + 3 else 0)), 0)

    def test_a_small_plates_soft_edge_left_outside_is_taken(self):
        # A small switch plate (60 to 80 px) whose selection stops 2 to 4 px short of its soft
        # photographed edge: the reach is only 3 or 4 px there, the strip and its fade wider.
        for side, short, blur in ((80, 3, 1.5), (80, 4, 1.0), (60, 2, 2.0)):
            with self.subTest(side=side, short=short, blur=blur):
                size = (side * 4, side * 3)
                left, top = side * 2 - side // 2, side * 3 // 2 - side // 2
                box = (left, top, left + side, top + side)
                picture = plate_beyond((left - short, top - short, left + side + short, top + side + short), blur, size)
                selection = matte(box, size=size)
                away = distance(selection)
                wider = rim.widen(picture, selection)
                self.assertIsNotNone(wider)
                self.assertGreater(taken(wider, away.point(lambda v, short=short: 255 if 0 < v <= short else 0)), 0.9)
                self.assertEqual(taken(wider, away.point(lambda v, short=short: 255 if v > short + 3 else 0)), 0)

    def test_an_edge_wider_than_the_reach_is_taken_nearest_first(self):
        # The selection 5 to 7 px short of the plate on every side, wider than the 3 or 4 px the
        # object's own edge counts from the selection: the 3 px nearest are taken (removal_mask
        # clears the rest) on each side, and nothing of the wall. Either the plate is larger than
        # the selection, or the selection smaller than the plate (a smaller working crop).
        for short, blur in ((5, 0), (6, 0), (7, 0), (6, 1.0)):
            plate = (PLATE[0] - short, PLATE[1] - short, PLATE[2] + short, PLATE[3] + short)
            for box, picture in ((PLATE, plate_beyond(plate, blur)), ((PLATE[0] + short, PLATE[1] + short, PLATE[2] - short, PLATE[3] - short),
                                                                    plate_beyond(PLATE, blur))):
                selection = matte(box)
                away = distance(selection)
                added, _ = rim.find(picture, selection)
                nearest = away.point(lambda v: 255 if 0 < v <= 3 else 0)
                for side, part in (("left", (0, 0, box[0] - 1, 299)), ("right", (box[2] + 1, 0, 399, 299)),
                                   ("top", (box[0], 0, box[2], box[1] - 1)), ("bottom", (box[0], box[3] + 1, box[2], 299))):
                    with self.subTest(short=short, blur=blur, box=box, side=side):
                        self.assertGreater(taken(added, ImageChops.darker(nearest, matte(part))), 0.9)
                with self.subTest(short=short, blur=blur, box=box):
                    self.assertEqual(taken(added, away.point(lambda v, short=short: 255 if v > short + 3 else 0)), 0)

    def test_a_soft_shadow_beside_an_object_on_the_pictures_edge_is_taken_to_the_edge(self):
        # An object standing on the bottom edge (alone, and in the corner), a soft shadow round
        # it: the shadow beside it is taken right down to the frame, not left as a dark stub there.
        for box in ((170, 210, 230, 299), (0, 210, 60, 299)):
            for depth, reach in ((40, 30), (60, 50)):
                with self.subTest(box=box, depth=depth, reach=reach):
                    selection = matte(box)
                    picture = soft_shadow(wall(), selection, depth, reach)
                    picture.paste((244, 244, 240), mask=selection)
                    added, _ = rim.find(picture, selection)
                    beside = ImageChops.darker(distance(selection).point(lambda v: 255 if 0 < v <= 3 else 0), matte((0, 285, 399, 299)))
                    self.assertGreater(taken(added, beside), 0.8)

    def test_an_exact_selection_near_the_pictures_edge_on_a_graded_backdrop_takes_nothing(self):
        # 42 to 44 px from the edge on the sweep's light side, the ring there is cut to a row or
        # two; the backdrop's slope still holds there, as the other sides see it, and the strip
        # itself says next to nothing of the slope across it (a steep sweep, 20 to 250).
        for top, bottom, box, shape in ((240, 90, (150, 44, 450, 164), "rectangle"), (90, 240, (150, 285, 450, 405), "rectangle"),
                                        (20, 250, (150, 287, 450, 407), "ellipse")):
            with self.subTest(top=top, bottom=bottom, box=box, shape=shape):
                picture, selection = graded("down", (600, 450), top=top, bottom=bottom), Image.new("L", (600, 450))
                getattr(ImageDraw.Draw(picture), shape)(box, fill=(150, 100, 60))
                getattr(ImageDraw.Draw(selection), shape)(box, fill=255)
                self.assertIsNone(rim.widen(picture, selection))

    def test_an_object_spanning_the_frame_on_a_graded_backdrop_takes_nothing(self):
        # A sofa across the whole frame, a door frame from one side: the outline faces one way
        # only, so no side running across it says what the backdrop's slope is there. What is
        # seen across it is the backdrop's.
        for direction, top, bottom, box in (("down", 240, 90, (0, 300, 599, 449)), ("down", 90, 240, (0, 0, 599, 149)),
                                            ("across", 240, 90, (349, 0, 599, 449))):
            with self.subTest(direction=direction, top=top, bottom=bottom, box=box):
                picture, selection = graded(direction, (600, 450), top=top, bottom=bottom), matte(box, size=(600, 450))
                picture.paste((150, 100, 60), mask=selection)
                self.assertIsNone(rim.widen(picture, selection))

    def test_nothing_to_look_at(self):
        picture = plate_on_wall()
        self.assertIsNone(rim.widen(picture, Image.new("L", SIZE)))                 # no selection
        self.assertIsNone(rim.widen(picture, matte(PLATE, size=(200, 150))))        # another size
        self.assertIsNone(rim.widen(picture, Image.new("L", SIZE, 255)))            # all of it: no surroundings

    def test_transparent_surroundings_are_not_a_surface(self):
        picture = plate_on_wall().convert("RGBA")
        clear = Image.new("L", SIZE, 255)
        ImageDraw.Draw(clear).rectangle((0, 0, 140, 299), fill=0)     # left of the plate nothing is painted
        picture.putalpha(clear)
        added, _ = rim.find(picture, matte(PLATE))
        self.assertEqual(count(added, (0, 0, 140, 299)) if added else 0, 0)

    def test_a_large_picture_is_worked_small(self):
        # The same scene at 6x: the shadow is still found, from a working copy of about WORK px.
        scale = 6
        picture = plate_on_wall().resize((SIZE[0] * scale, SIZE[1] * scale), Image.Resampling.BILINEAR)
        selection = matte(PLATE).resize(picture.size, Image.Resampling.NEAREST)
        added, stats = rim.find(picture, selection)
        self.assertLess(stats["scale"], 0.6)
        shadow = tuple(v * scale for v in SHADOW_LEFT)
        self.assertGreater(count(added, shadow), 0.7 * (shadow[2] - shadow[0]) * (shadow[3] - shadow[1]))


if __name__ == "__main__":
    unittest.main()
