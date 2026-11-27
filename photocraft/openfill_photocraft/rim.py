# SPDX-License-Identifier: Apache-2.0
"""The object's own rim and contact shadow, just outside the artist's selection.

A selection made by a model, or by hand, often stops a few pixels inside the object's outline:
the plate's shaded edge, the dark crescent on the far side of a thermostat, the soft shadow a
duck's feet leave on the floor. Every removal method then repairs the inside and leaves that
fringe as a dark line or outline. ``widen`` finds the fringe and adds it to the selection
before the removal runs.

How: a band hugs the selection, just past what removal_mask already clears, and a ring beyond
it stands for the surface the object sits on. A band pixel belongs to the object when

- it differs from the surface by far more than the surface differs from itself, the surface
  read two ways that must both agree: flat (the ring's average near it) and sloped (a plane
  fitted to the ring near it, read at the pixel), so a graded backdrop, lighter or darker
  further out, is not taken for a shadow or a rim; the plane's slope across the outline counts
  lighter outward only as far as the backdrop's slope all round says, since a soft shadow's
  tail is lighter outward too,
- the surface there is plain (a wall, a backdrop, flat ground): on busy surroundings a strip of
  the neighbouring object or the next stone looks just as "different", and a leftover rim does
  not show there anyway,
- it is darker than the surface (a shadow), or, within a few pixels of the selection, nearer
  the object's own edge colour than the surface's (the object's edge the selection missed) and
  part of a thin strip that gives way to the surface or a shadow within those few pixels, or a
  few more where the strip also looks like what lies inside the selection's edge (a soft
  photographed outline, a strip wider than the few pixels),
- and it connects to the selection through such pixels.

The addition is capped, nearest pixels first. Pillow only; the work is done on a copy of about
WORK px whatever the picture's size.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter, ImageMath

from .local_removal import grow_support

# Longer side of the working copy: enough to see a 9 px rim on a 2500 px picture, fast at 6000 px.
WORK = 512
# Band searched for the rim, past what removal_mask already clears, as a share of
# sqrt(selection area): the measured rims lay 9 to 13 px outside selections of 70 to 110 px.
NEAR = 0.10
# Ring beyond the band that stands for the surface, as the same share.
FAR = 0.30
# A band pixel differs when it is GAIN times the surface's own variation plus FLOOR levels away
# from the surface (the analyst's tested recipe).
GAIN = 2.5
FLOOR = 8
# Plain surroundings only: mean deviation of the ring from its own local average, in levels.
# Tuned on the corpus: at 10 it took the sea behind a dog and a dark doorway beside a man, at 5
# it lost the earring's shadow on skin.
SPREAD_LIMIT = 6
# A shadow is darker than the surface by more than this many levels.
DARKER = 4
# The object's own edge counts only this share of the band's width from the selection: further
# out, "looks like the object" took clouds by a kite string and a clock's shading round a sticker.
OWN_REACH = 0.25
# How far out, in reaches, the surface may come back after the object's missed edge where the
# strip also looks like what lies inside the selection's edge (see stands_out in find): a 7 px
# strip of plate left outside a 74 px selection (reach 4), its photographed outline blurred 2 px,
# gives way to the wall within 12.
SOFT_REACH = 3
# Thin selections (a cable, a kite string) get a band as thin as they are: a disc's sqrt(area)
# is 1.8 times its radius, so THICK times the thickness stands in for sqrt(area).
THICK = 1.8
# Never add more than this share of the selection: a rim is a fringe, not a second object.
CAP = 0.40
# Less than this share of the selection is noise, not a rim worth widening for.
MIN_SHARE = 0.01
# The sloped surface (see _plane) must also say a pixel stands out, by GAIN times its own
# variation plus this many levels. Lower than FLOOR because the plane is worked in floats, without
# the few levels of 8-bit rounding the flat average carries where the ring is a small part of its
# blur. Tuned on the corpus: at 8 it lost more of a thermostat's crescent shadow (9.7 % of the
# loose selection added against 10.9), at 4 it took back sky round a pin on a portrait that the
# fixture's clean plate shows is backdrop.
PLANE_FLOOR = 6
# Where the ring is less than this share of the plane's window the plane is not trusted and the
# flat average stands alone. Small: a ring cut to a row or two by the picture's edge (about 0.007
# of the window beside an object 44 px from the edge) still gives the plane its level, and the
# ridge gives it the slope the other sides see.
PLANE_COVER = 0.002
# The plane's tilt is held towards the backdrop's slope all round (see _plane) by this share of
# its window's spread squared (a ridge), so a window whose ring pixels lie nearly in a line cannot
# tilt it wildly; across a ring as wide as the measured ones it keeps about 95 % of its own slope.
RIDGE = 0.01
# Smoothing passes of [1 2 1] / 4 along each axis of the float blur's small copy: four make the
# blur about 1.5 times the copy's step wide and round enough that the ring's corners do not show.
BINOMIAL = 4
# The outline's sides (see _plane): a side faces within 22.5 degrees of one of eight ways (cos
# 22.5 degrees = 0.9239), and counts when it holds this share of the band's plain pixels.
FACING = 0.9239
SIDE_SHARE = 0.02
# The backdrop's slope all round is held towards none by this much, against side shares that add
# up to 1, so an outline that faces only two ways (a cable) leaves the slope across it near none.
# Against the same yardstick a side counts as alone, its slope across taken as the backdrop's,
# when the other sides say next to nothing across it (see _shared_slope).
SLOPE_RIDGE = 0.05


def _max_channel(image: Image.Image) -> Image.Image:
    red, green, blue = image.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _ring_mean(image: Image.Image, weight: Image.Image, radius: float) -> tuple[Image.Image, Image.Image]:
    """Blur of ``image`` that counts only pixels where ``weight`` is set, and how much weight was
    near each pixel. Premultiplied alpha (RGBa) does the division."""
    rgba = image.convert("RGB")
    rgba.putalpha(weight)
    blurred = rgba.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
    return blurred.convert("RGB"), blurred.getchannel("A")


def _blur_float(image: Image.Image, scale: int) -> Image.Image:
    """A blur of the float ``image`` about 1.5 * ``scale`` px wide (Pillow's own blurs take 8-bit
    images only): box-average down by ``scale``, smooth the small copy with a binomial, bilinear
    back up. Past the edges counts as nothing, which a ratio of two such blurs takes in its stride."""
    small = image.reduce(scale)
    for dx, dy in ((1, 0), (0, 1)):
        for _ in range(BINOMIAL):
            w, h = small.size
            small = ImageMath.lambda_eval(lambda v: (v["a"] + v["b"] * 2 + v["c"]) / 4, a=small.crop((-dx, -dy, w - dx, h - dy)),
                                          b=small, c=small.crop((dx, dy, w + dx, h + dy)))
    return small.resize((small.width * scale, small.height * scale), Image.Resampling.BILINEAR).crop((0, 0, *image.size))


def _average(image: Image.Image) -> float:
    """The mean of the float ``image``."""
    return image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))


def _times(a: Image.Image, b: Image.Image) -> Image.Image:
    """The product of two float images, pixel by pixel."""
    return ImageMath.lambda_eval(lambda v: v["a"] * v["b"], a=a, b=b)


def _shared_slope(sides: list[tuple[float, float, float, float, float, float, float]]) -> tuple[float, float]:
    """The slope (x, y) the backdrop has all round, from each side's (share, how much of a true
    slope its fit keeps along it and across it, outward x, outward y, slope x, slope y). Along a
    side the slope seen is the backdrop's; across it a shadow's tail can only add slope lighter
    outward, so there the backdrop's is at most what is seen. Least squares on both (the second
    where it is broken), held towards none by SLOPE_RIDGE. A studio sweep is seen on every side
    and comes out whole; a shadow all round, or on one side, comes out as no slope. Only where no
    other side says anything of the slope across a side (no side runs that way, none faces the
    other way: an object spanning the frame from edge to edge) is what is seen across it taken as
    the backdrop's, for nothing tells a sweep from a shadow there and the sweep fills the frame.
    A side's fit counts as far as it keeps a true slope: a ring cut to a strip by the picture's
    edge says next to nothing across that edge."""
    covered = []
    for _, _, _, nx, ny, _, _ in sides:
        covered.append(sum(share * (keep_along * (nx * other_y - ny * other_x) ** 2 + keep_across * max(0.0, -(nx * other_x + ny * other_y)) ** 2)
                           for share, keep_along, keep_across, other_x, other_y, _, _ in sides))
    slope = (0.0, 0.0)
    for _ in range(12):
        rows = []
        for (share, keep_along, keep_across, nx, ny, sx, sy), others in zip(sides, covered, strict=False):
            rows.append((share * keep_along, -ny, nx, -ny * sx + nx * sy))
            # Squared, so that a side another side covers even in part (the two sides of an
            # object in a corner) keeps next to none of it.
            alone = (SLOPE_RIDGE / (SLOPE_RIDGE + others)) ** 2
            broken = slope[0] * nx + slope[1] * ny > nx * sx + ny * sy
            rows.append((share * keep_across * (1.0 if broken else alone), nx, ny, nx * sx + ny * sy))
        axx = SLOPE_RIDGE + sum(w * a * a for w, a, b, t in rows)
        axy = sum(w * a * b for w, a, b, t in rows)
        ayy = SLOPE_RIDGE + sum(w * b * b for w, a, b, t in rows)
        bx, by = sum(w * a * t for w, a, b, t in rows), sum(w * b * t for w, a, b, t in rows)
        det = axx * ayy - axy * axy
        moved = ((ayy * bx - axy * by) / det, (axx * by - axy * bx) / det)
        if abs(moved[0] - slope[0]) + abs(moved[1] - slope[1]) < 1e-6:
            return moved
        slope = moved
    return slope


def _plane(image: Image.Image, flat: Image.Image, ring: Image.Image, radius: float, sample: Image.Image, inside: Image.Image,
           width: int) -> Image.Image:
    """The surface as a plane fitted to the ring near each pixel (least squares, over a window as
    wide as the flat average's blur) and read at the pixel; except that across the selection's
    outline its slope counts lighter outward only as far as the backdrop's slope all round says
    (_shared_slope, from the ``sample`` pixels, the band's plain ones), since a soft shadow's tail
    in the ring is lighter outward too, on every side the shadow lies. On a backdrop graded from
    light to dark the band then reads as the backdrop it is, where the flat average, taken further
    out, makes it look darker (a shadow) on one side and lighter on the other; on a plain wall a
    soft shadow still stands out from it. The fit's ridge holds each pixel's tilt towards that
    slope all round, so where the picture's edge cuts the ring to a strip the plane keeps the
    backdrop's slope instead of going flat. Worked in floats: an 8-bit blur weighted by a ring this
    far off keeps only a few levels. Where the ring is scarce ``flat`` stands. ``width`` is the
    band's (from the selection to its outer edge)."""
    scale = max(1, round(radius / 1.5))
    w, h = image.size
    xs, ys = Image.new("F", (w, 1)), Image.new("F", (1, h))
    xs.putdata([x - w / 2 for x in range(w)])
    ys.putdata([y - h / 2 for y in range(h)])
    xs, ys = xs.resize((w, h), Image.Resampling.NEAREST), ys.resize((w, h), Image.Resampling.NEAREST)
    weight = ring.point(lambda v: 1 if v else 0).convert("F")

    def moment(make, **images: Image.Image) -> Image.Image:
        return _blur_float(ImageMath.lambda_eval(make, w=weight, x=xs, y=ys, **images), scale)

    shared = {"s": moment(lambda v: v["w"]), "sx": moment(lambda v: v["w"] * v["x"]), "sy": moment(lambda v: v["w"] * v["y"]),
              "sxx": moment(lambda v: v["w"] * v["x"] * v["x"]), "sxy": moment(lambda v: v["w"] * v["x"] * v["y"]),
              "syy": moment(lambda v: v["w"] * v["y"] * v["y"])}
    trusted = ImageMath.lambda_eval(lambda v: v["convert"]((v["s"] >= PLANE_COVER) * 255, "L"), s=shared["s"])
    counted = ImageChops.darker(sample, trusted).point(lambda v: 1 if v else 0).convert("F")
    total = _average(counted)
    if total <= 0:
        return flat
    # The fit's geometry, the same for every channel: the ring's centre near each pixel and the
    # spread of the ring's pixels about it, held back by the ridge.
    ridge = RIDGE * radius * radius
    inv = ImageMath.lambda_eval(lambda v: 1 / v["max"](v["s"], 1e-6), s=shared["s"])
    mx, my = _times(shared["sx"], inv), _times(shared["sy"], inv)
    spread_xx = ImageMath.lambda_eval(lambda v: v["q"] * v["i"] - v["m"] * v["m"], q=shared["sxx"], i=inv, m=mx)
    spread_yy = ImageMath.lambda_eval(lambda v: v["q"] * v["i"] - v["m"] * v["m"], q=shared["syy"], i=inv, m=my)
    vxy = ImageMath.lambda_eval(lambda v: v["q"] * v["i"] - v["a"] * v["b"], q=shared["sxy"], i=inv, a=mx, b=my)
    vxx, vyy = (ImageMath.lambda_eval(lambda v: v["a"] + ridge, a=spread) for spread in (spread_xx, spread_yy))
    det = ImageMath.lambda_eval(lambda v: v["max"](v["a"] * v["b"] - v["c"] * v["c"], 1e-6), a=vxx, b=vyy, c=vxy)
    # Which way the outline faces near each pixel: outward across the contours of the selection
    # blurred about as wide as the band. Past the picture's edge is no part of the blur (a ratio
    # of two blurs), so an object standing on the edge still faces straight out beside it.
    step = max(1, round(width / 1.5))
    soft = ImageMath.lambda_eval(lambda v: v["a"] / v["max"](v["b"], 1e-6), a=_blur_float(inside.point(lambda v: 1 if v else 0).convert("F"), step),
                                 b=_blur_float(Image.new("F", inside.size, 1.0), step))
    out_x = ImageMath.lambda_eval(lambda v: v["l"] - v["r"], r=soft.crop((1, 0, w + 1, h)), l=soft.crop((-1, 0, w - 1, h)))
    out_y = ImageMath.lambda_eval(lambda v: v["u"] - v["d"], d=soft.crop((0, 1, w, h + 1)), u=soft.crop((0, -1, w, h - 1)))
    norm = ImageMath.lambda_eval(lambda v: v["max"]((v["a"] * v["a"] + v["b"] * v["b"]) ** 0.5, 1e-9), a=out_x, b=out_y)
    out_x, out_y = ImageMath.lambda_eval(lambda v: v["a"] / v["n"], a=out_x, n=norm), ImageMath.lambda_eval(lambda v: v["b"] / v["n"], b=out_y, n=norm)
    # The outline's sides, the eight ways it can face: the sample pixels on each, the way each
    # faces, and how much of a true slope the fit keeps along it and across it (a spread of
    # ring pixels small against the ridge keeps little).
    sides = []
    for turn in range(8):
        cos, sin = math.cos(turn * math.pi / 4), math.sin(turn * math.pi / 4)
        on_side = ImageMath.lambda_eval(lambda v, cos=cos, sin=sin: v["w"] * ((v["ox"] * cos + v["oy"] * sin) >= FACING), w=counted, ox=out_x, oy=out_y)
        weight_there = _average(on_side)
        if weight_there >= SIDE_SHARE * total:
            fx, fy = _average(_times(out_x, on_side)), _average(_times(out_y, on_side))
            size = max(math.hypot(fx, fy), 1e-12)
            fx, fy = fx / size, fy / size
            axx, axy, ayy = (_average(_times(spread, on_side)) / weight_there for spread in (spread_xx, vxy, spread_yy))
            across = max(0.0, fx * fx * axx + 2 * fx * fy * axy + fy * fy * ayy)
            along = max(0.0, fy * fy * axx - 2 * fx * fy * axy + fx * fx * ayy)
            sides.append((weight_there / total, along / (along + ridge), across / (across + ridge), fx, fy, on_side, weight_there))
    channels = []
    for channel in image.split():
        sc = moment(lambda v: v["w"] * v["c"], c=channel)
        scx, scy = moment(lambda v: v["w"] * v["c"] * v["x"], c=channel), moment(lambda v: v["w"] * v["c"] * v["y"], c=channel)
        mean = _times(sc, inv)
        cx = ImageMath.lambda_eval(lambda v: v["q"] * v["i"] - v["m"] * v["c"], q=scx, i=inv, m=mx, c=mean)
        cy = ImageMath.lambda_eval(lambda v: v["q"] * v["i"] - v["m"] * v["c"], q=scy, i=inv, m=my, c=mean)
        local_x = ImageMath.lambda_eval(lambda v: (v["b"] * v["p"] - v["c"] * v["q"]) / v["d"], b=vyy, c=vxy, p=cx, q=cy, d=det)
        local_y = ImageMath.lambda_eval(lambda v: (v["a"] * v["q"] - v["c"] * v["p"]) / v["d"], a=vxx, c=vxy, p=cx, q=cy, d=det)
        tilt_x, tilt_y = _shared_slope([(share, keep_along, keep_across, fx, fy, _average(_times(local_x, on_side)) / weight_there,
                                         _average(_times(local_y, on_side)) / weight_there)
                                        for share, keep_along, keep_across, fx, fy, on_side, weight_there in sides])
        # The ridge holds each pixel's tilt towards the backdrop's slope all round rather than
        # towards none: where the ring is cut to a strip (by the picture's edge) the plane keeps
        # the slope the other sides see.
        local_x = ImageMath.lambda_eval(lambda v, tilt_x=tilt_x, tilt_y=tilt_y: v["l"] + ridge * (v["b"] * tilt_x - v["c"] * tilt_y) / v["d"], l=local_x, b=vyy, c=vxy, d=det)
        local_y = ImageMath.lambda_eval(lambda v, tilt_x=tilt_x, tilt_y=tilt_y: v["l"] + ridge * (v["a"] * tilt_y - v["c"] * tilt_x) / v["d"], l=local_y, a=vxx, c=vxy, d=det)
        # Lighter outward than the backdrop all round is a shadow's tail: that part of the slope goes.
        excess = ImageMath.lambda_eval(lambda v, tilt_x=tilt_x, tilt_y=tilt_y: v["max"](v["lx"] * v["ox"] + v["ly"] * v["oy"] - (tilt_x * v["ox"] + tilt_y * v["oy"]), 0),
                                       lx=local_x, ly=local_y, ox=out_x, oy=out_y)
        channels.append(ImageMath.lambda_eval(
            lambda v: v["convert"](v["c"] + (v["lx"] - v["e"] * v["ox"]) * (v["x"] - v["mx"]) + (v["ly"] - v["e"] * v["oy"]) * (v["y"] - v["my"]) + 0.5, "L"),
            c=mean, lx=local_x, ly=local_y, e=excess, ox=out_x, oy=out_y, x=xs, y=ys, mx=mx, my=my))
    return Image.composite(Image.merge("RGB", channels), flat, trusted)


def _extend(image: Image.Image, known: Image.Image) -> Image.Image:
    """``image`` where ``known`` is set, and elsewhere the colour of the nearest known pixels,
    averaged over an area about as wide as the distance to them (pull-push: halve until every
    block holds a known pixel, then fill back up). Unlike a masked blur it keeps its precision
    away from the known pixels. Halving goes on down to a single block if need be: a block left
    unknown at the coarsest level would bleed black into everything filled from it."""
    levels = []
    colour, weight = image.convert("RGB"), known
    while max(colour.size) > 1 and weight.getextrema()[0] == 0:
        rgba = colour.copy()
        rgba.putalpha(weight)
        smaller = rgba.convert("RGBa").reduce(2).convert("RGBA")
        levels.append((colour, weight))
        colour, weight = smaller.convert("RGB"), smaller.getchannel("A").point(lambda v: 255 if v else 0)
    for finer, known_there in reversed(levels):
        colour = Image.composite(finer, colour.resize(finer.size, Image.Resampling.BILINEAR), known_there)
    return colour


def _grow_capped(seed: Image.Image, within: Image.Image, steps: int, cap: float) -> tuple[Image.Image, bool]:
    """What ``seed`` reaches through ``within`` a pixel at a time (8 neighbours), at most ``steps``
    times, stopping before the addition would pass ``cap`` pixels so the pixels nearest the
    selection come first; and whether the cap stopped it. Only the part ``within`` covers is
    worked on."""
    added = Image.new("L", seed.size)
    bounds = within.getbbox()
    if bounds is None:
        return added, False
    box = (max(0, bounds[0] - 1), max(0, bounds[1] - 1), min(seed.width, bounds[2] + 1), min(seed.height, bounds[3] + 1))
    start, room = seed.crop(box), within.crop(box)
    base = start.histogram()[255]
    grown, capped = start, False
    for _ in range(steps):
        step = ImageChops.lighter(grown, ImageChops.darker(grow_support(grown, 1), room))
        total = step.histogram()[255]
        if total - base > cap:
            capped = True
            break
        if total == grown.histogram()[255]:
            break
        grown = step
    added.paste(ImageChops.subtract(grown, start), box[:2])
    return added, capped


def _cleared(side: float) -> float:
    """How far past the selection removal_mask already clears, in picture px (see removal_mask)."""
    reach = max(2, min(32, round(side * 0.045)))
    return round(reach * 1.5) + 2 * max(1.0, reach * 0.48)


def find(picture: Image.Image, selection: Image.Image, trace: dict | None = None) -> tuple[Image.Image | None, dict]:
    """The pixels next to ``selection`` that belong to the object's rim or contact shadow, as a
    canvas-sized L mask (255 = add), or None; and numbers that say what was looked at. ``trace``,
    when given, receives the working copy and its intermediate masks (for tuning by eye)."""
    if selection.mode != "L":
        selection = selection.convert("L")
    bounds = selection.getbbox()
    if bounds is None or picture.size != selection.size:
        return None, {}
    area = sum(level * count for level, count in enumerate(selection.histogram())) / 255
    side = math.sqrt(area)
    cleared = _cleared(side)
    margin = math.ceil(cleared + side * (NEAR + FAR) + 4)
    left, top, right, bottom = bounds
    box = (max(0, left - margin), max(0, top - margin), min(picture.width, right + margin), min(picture.height, bottom + margin))
    crop = picture.crop(box)
    chosen = selection.crop(box)
    scale = min(1.0, WORK / max(crop.size))
    size = (max(8, round(crop.width * scale)), max(8, round(crop.height * scale)))
    image = crop.convert("RGB").resize(size, Image.Resampling.BOX)
    coverage = chosen.resize(size, Image.Resampling.BOX)
    inside = coverage.point(lambda v: 255 if v >= 128 else 0)
    count = inside.histogram()[255]
    if not count:
        return None, {}
    opaque = Image.new("L", size, 255)
    if "A" in crop.getbands():
        opaque = crop.getchannel("A").resize(size, Image.Resampling.BOX).point(lambda v: 255 if v == 255 else 0)

    g = round(cleared * scale)
    r1 = max(2, round(side * NEAR * scale))
    r2 = max(4, round(side * FAR * scale))
    radius = (r1 + r2) / 2
    # A thin selection's rim is as thin as the selection (twice its area over its outline); the
    # ring stays where it is, past any shadow, so only the band where pixels are added narrows.
    outline = ImageChops.subtract(inside, inside.filter(ImageFilter.MinFilter(3))).histogram()[255]
    thickness = 2 * count / max(1, outline) / scale
    r0 = min(r1, max(1, round(THICK * thickness * NEAR * scale)))
    band = ImageChops.darker(ImageChops.subtract(grow_support(inside, g + r1), inside), opaque)
    if r0 < r1:
        band = ImageChops.darker(band, grow_support(inside, g + r0))
    ring = ImageChops.subtract(grow_support(inside, g + r1 + r2), grow_support(inside, g + r1 + 1))
    ring = ImageChops.darker(ring, opaque)

    surface, cover = _ring_mean(image, ring, radius)
    covered = cover.point(lambda v: 255 if v >= 24 else 0)
    edge_width = max(1, r1 // 2)
    core = ImageChops.invert(grow_support(ImageChops.invert(inside), edge_width))
    edge = ImageChops.subtract(inside, core)
    own = _extend(image, edge)
    unlike_own = _max_channel(ImageChops.difference(image, own))
    reach = max(1, round(OWN_REACH * (g + r1)))
    near_edge = grow_support(inside, reach)
    # What lies inside the selection's edge, averaged over the next two widths in, where there is
    # any near: a selection that stops short of the object's outline has the object there too; a
    # lasso loose round a sticker on a clock's face has the face at its edge, and the sticker's
    # border and colour within.
    within = ImageChops.subtract(core, ImageChops.invert(grow_support(ImageChops.invert(inside), 3 * edge_width)))
    unlike_within, near_within = Image.new("L", size), Image.new("L", size)
    if within.getbbox() is not None:
        within_colour, within_cover = _ring_mean(image, within, reach + 2 * edge_width)
        unlike_within = _max_channel(ImageChops.difference(image, within_colour))
        near_within = within_cover.point(lambda v: 255 if v >= 24 else 0)

    def stands_out(surface: Image.Image, floor: int = FLOOR) -> tuple[Image.Image, ...]:
        """Where a band pixel stands out from ``surface`` (by GAIN times its variation plus
        ``floor`` levels) as shadow or as the object's own edge, and the steps on the way
        (different, plain, shade, alike)."""
        diff = _max_channel(ImageChops.difference(image, surface))
        # How much the surface varies there: the same difference, averaged over the nearby ring.
        spread, _ = _ring_mean(Image.merge("RGB", [diff] * 3), ring, radius)
        spread = spread.getchannel(0)
        limit = spread.point(lambda v: min(255, round(v * GAIN) + floor))
        different = ImageChops.subtract(diff, limit).point(lambda v: 255 if v > 0 else 0)
        different = ImageChops.darker(different, covered)
        plain = spread.point(lambda v: 255 if v <= SPREAD_LIMIT else 0)
        # A contact shadow is darker than the surface; the object's missed edge looks like the
        # object's own edge. Something lighter that looks unlike the object (a cloud by a kite
        # string, a ledge's highlight under a cat, the sky's glow round a tree) is neither.
        shade = ImageChops.subtract(surface.convert("L"), image.convert("L")).point(lambda v: 255 if v > DARKER else 0)
        alike = ImageChops.subtract(diff, unlike_own).point(lambda v: 255 if v > 0 else 0)
        alike = ImageChops.darker(alike, near_edge)
        # ... and only where the selection's edge lies on the object: a loose lasso's edge is surface.
        distinct = ImageChops.subtract(_max_channel(ImageChops.difference(own, surface)), limit)
        alike = ImageChops.darker(alike, distinct.point(lambda v: 255 if v > 0 else 0))
        # ... and only as a thin strip, that gives way to the surface (within half the limit of it)
        # or to a shadow within the reach. A surface graded away from a loose selection (a dome
        # under a sticker) looks like the selection's edge next to it and comes back to the
        # surface only gradually, past the reach. A photographed outline fades over a few pixels,
        # and a strip may be wider than the reach (only the reach of it is taken, removal_mask
        # clears the rest): the surface may come back as far as SOFT_REACH times the reach out where
        # the strip also looks like what lies inside the selection's edge (or nothing lies
        # there, as in a cable), which a dome or a clock's face round a sticker does not.
        settled = ImageChops.subtract(ImageChops.add(diff, diff), limit).point(lambda v: 255 if v == 0 else 0)
        surface_again = ImageChops.darker(band, ImageChops.lighter(settled, shade))
        like_within = ImageChops.subtract(diff, unlike_within).point(lambda v: 255 if v > 0 else 0)
        soft_strip = ImageChops.darker(grow_support(surface_again, SOFT_REACH * reach), ImageChops.lighter(like_within, ImageChops.invert(near_within)))
        alike = ImageChops.darker(alike, ImageChops.lighter(grow_support(surface_again, reach), soft_strip))
        return ImageChops.darker(different, ImageChops.lighter(shade, alike)), different, plain, shade, alike

    found, different, plain, shade, alike = stands_out(surface)
    allowed = ImageChops.darker(ImageChops.darker(found, band), plain)
    sloped = surface
    if allowed.getbbox() is not None:
        # The flat average reads a graded backdrop (a studio sweep, a dome's shading, the sky
        # darkening towards the horizon) as a shadow on its light side; the plane alone would
        # overshoot where the ring crosses a step (a horizon, a floor meeting a wall). A pixel is
        # taken only when it stands out from both. Only worth the extra work when the flat reading
        # found something.
        sloped = _plane(image, surface, ring, radius, ImageChops.darker(band, plain), inside, g + r1)
        allowed = ImageChops.darker(allowed, stands_out(sloped, PLANE_FLOOR)[0])
    added, capped = _grow_capped(inside, allowed, g + r1 + 1, count * CAP)
    share = added.histogram()[255] / count
    if trace is not None:
        trace.update(box=box, image=image, inside=inside, band=band, ring=ring, surface=surface, sloped=sloped,
                     different=different, plain=plain, shade=shade, alike=alike, allowed=allowed, added=added)
    stats = {"scale": round(scale, 3), "g": g, "r1": r1, "r2": r2, "share": round(share, 4), "capped": capped,
             "thick": round(thickness, 1), "side": round(side, 1)}
    if share < MIN_SHARE:
        return None, stats
    # Work pixels the selection only partly covered sit between it and the addition; at full size
    # they would leave a gap as wide as one work pixel.
    partial = coverage.point(lambda v: 255 if 0 < v < 128 else 0)
    added = ImageChops.lighter(added, ImageChops.darker(partial, added.filter(ImageFilter.MaxFilter(3))))
    full = added.resize(crop.size, Image.Resampling.BILINEAR).point(lambda v: 255 if v >= 128 else 0)
    out = Image.new("L", picture.size)
    out.paste(full, box[:2])
    return out, stats


def widen(picture: Image.Image, selection: Image.Image) -> Image.Image | None:
    """``selection`` with the object's rim and contact shadow added, or None when there is
    nothing to add. ``picture`` is the visible canvas (RGB, or RGBA where transparent pixels are
    no surface), ``selection`` the canvas-sized L selection, grey feather kept."""
    added, _ = find(picture, selection)
    if added is None:
        return None
    return ImageChops.lighter(selection.convert("L"), added)
