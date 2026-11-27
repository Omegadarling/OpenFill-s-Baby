# SPDX-License-Identifier: Apache-2.0
"""Automatic editing: two edits from the models that complement each other, the likelier one first.

An artist rated ten edit models on 24 pictures (2026-11-24). No model gave a good result (4 or 5 stars) on
more than 11, and which one won depended on the edit: Qwen for a new colour, material, lettering or a change
to the whole picture; FIBO Turbo for a different or an added object; FIBO on a whole picture rated 1.0.
Qwen and FIBO Turbo together gave a good result on 16 of the 24, and on 17 with H3 as Qwen's partner for
whole-picture edits. So Automatic makes both and the artist keeps the better one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from openfill.remote_models import (
    DEV_HERETIC_ID,
    DEV_ID,
    H3_IMAGE_ID,
    QWEN21_FULL_ID,
    QWEN21_LIGHTNING_ID,
    TURBO_HERETIC_ID,
    TURBO_ID,
)

AUTO_EDIT = "openfill-auto-edit"  # the Model value a panel sends for Automatic
MEASURED = "2026-11-24"
PICTURES = 24
RESULTS = 2
WHOLE = 0.98  # a selection covering this much of the picture is an edit of the whole picture
# Each family in the order its members rated: Turbo and Dev rated alike with either text encoder; Turbo is faster.
FAMILIES = {
    "qwen": (QWEN21_LIGHTNING_ID, QWEN21_FULL_ID),
    "fibo": (TURBO_ID, TURBO_HERETIC_ID, DEV_ID, DEV_HERETIC_ID),
    "h3": (H3_IMAGE_ID,),
}
# Names short enough to sit under a thumbnail.
SHORT = {
    QWEN21_LIGHTNING_ID: "Qwen", QWEN21_FULL_ID: "Qwen BF16", TURBO_ID: "FIBO Turbo", TURBO_HERETIC_ID: "FIBO Turbo",
    DEV_ID: "FIBO Dev", DEV_HERETIC_ID: "FIBO Dev", H3_IMAGE_ID: "H3",
}
# Family order by kind of edit: mean stars were Qwen 4.0, H3 3.7, FIBO 1.0 on the whole picture; FIBO Turbo 3.2 and
# 3.8 against Qwen 1.7 and 2.4 for a swapped or added object; Qwen 3.3 and 4.3 against FIBO Turbo 2.6 and 2.7 for a
# recolour and for lettering or a pattern.
ORDERS = {
    "whole": ("qwen", "h3"),
    "object": ("fibo", "qwen", "h3"),
    "look": ("qwen", "fibo", "h3"),
}
# An instruction that asks for something that is not in the picture yet.
_NEW_OBJECT = re.compile(
    r"\b(add|insert|place|put|replace|swap|substitute)\b|\b(turn|change|convert|transform|make)\b.*\binto\b", re.IGNORECASE
)
# Lettering goes to Qwen even when the instruction says "replace": it is the only family that spelled the words.
_LETTERING = re.compile(r"\b(text|word|words|lettering|letters|caption|headline|title|says|spell|spelling|write|written)\b|[\"“”]",
                        re.IGNORECASE)
TIP = ("Automatic makes edits with the models that rated best together for this kind of edit, and FIBO in both its modes. "
       "Keep the best one.")
# Room for the edit. An object selection hugs the object, and Auto-feather then fades the edit in over the
# object's own outer rim, so the rim keeps its old look: the artist's note on four of the 24 rated edits was that
# the selection was too tight. And a different object does not fit the old one's outline (a Vespa in a bicycle).
ROOM = 0.08    # a new object gets the convex hull of each part, grown by this share of the largest part's longer side
SOLID = 0.85   # unless the selection, made solid, already fills this much of its hulls (a marquee, a ball, a plate)
REACH = 64     # the most (pixels) a selection is grown to move the feather off the object
# The selection model's matte is the object where it is at half strength or more, and also where it is weaker but hangs
# together with that: it returned the cookies on a plate at 9-127 and a coat's shaded flap at 24-127.
LOW = 24
SURROUNDINGS = 0.5  # a selection over this share of the picture, or along three of its edges, is the picture around something
WORK = 256     # parts and holes are found on a copy of the selection's box this many pixels across
UNSURE = 2 / 3  # a hole in a recolour is the model's doubt, not a hole in the object, when this much of its middle is at LOW
INNER = 1 / 3   # a hole's middle: its innermost third (a feather is at LOW only near the rim; the model's doubt is level)
RIM = 4         # the model's matte rises from half to full strength within 2 to 3.5 px at its edges (21 corpus mattes)
FULL = 250      # full strength, where that rise ends
PAD = 0.04      # the copy's box: the matte at LOW, grown by this share of its longer side so no part touches its edge
TAPER = 4       # where weak matte stops growing is found on a smaller copy, this many pixels each side of the ramp
# A new object whose solid shape is this many times the half-strength matte was given room inside it: the plate of
# cookies is 1.6 times; no other of the 21 rated edits of a selection is more than 1.2 times.
ROOM_GAIN = 1.25
ROOM_NOTICE = "Automatic gave the new object room around the selection."


def short_label(model: str, name: str = "") -> str:
    return SHORT.get(model) or name or model


def kind(prompt: str, share: float) -> str:
    """"whole" for the whole picture, "object" when the instruction asks for a new object, else "look"."""
    if share >= WHOLE:
        return "whole"
    if _LETTERING.search(prompt):
        return "look"
    return "object" if _NEW_OBJECT.search(prompt) else "look"


# FIBO Edit Turbo's two modes. A blind panel rated FIBO Turbo on 21 of the edits in all four combinations of mode and
# context (2026-11-25; independent raters calibrated on the artist's own
# stars, not the artist): Preserve keeps the selected thing's structure and gives it a new look (mean 3.9 against 3.0 in
# Replace on 12 looks); Replace draws a different object, or one added in an empty spot drawn as a box (4.0 against 3.0 on
# 9). Something added onto the selected subject (glasses on a face, a bandana on a dog) keeps the subject: Preserve.
# Until then Automatic ran FIBO in whatever mode was saved, so one mode was wrong for half of all edits.
_SWAPPED = re.compile(r"\b(replace|swap|substitute)\b|\b(turn|change|convert|transform|make)\b.*\binto\b", re.IGNORECASE)
BOX = 0.98   # a selection filling this much of its bounding box was drawn as a box: an empty spot, "here"
# What every Automatic edit runs with, whatever is saved: the settings the ratings were made with.
SETTINGS = {"auto_feather": True, "auto_feather_percent": 10.0, "fibo_boundary_match": True, "fibo_boundary_strength": 25.0,
            "fibo_context_percent": 100.0}
MODES = {
    # mode: its own settings, and the words under its thumbnail when FIBO is shown in both modes
    "replace": ({"fibo_edit_mode": "replace", "fibo_context_mode": "local"}, "FIBO Replace"),
    "preserve": ({"fibo_edit_mode": "preserve", "fibo_context_mode": "full"}, "FIBO Preserve"),
}
# Something added in a box drawn on an empty spot. A blind panel rated 11 such adds (2026-11-26,
# independent raters, calibrated on the artist's stars where the artist had
# rated the picture, not the artist): FIBO Replace was good (4 or 5 stars) on 8 and on 9 with another seed, Qwen on 2
# and FIBO Preserve on 1 (mean 1.5: they redrew the spot without the object). Qwen cannot see the box, so "here" told it
# nothing; told "in the middle of the picture", on a crop with little around the box, it was good on 5 (mean 3.6
# against 2.0). Replace, that Qwen and Replace with another seed gave a good result on 11 of 11 and two good ones on 8,
# against 10 and 1 for Replace, Qwen and Preserve.
TIGHT = 0.2          # Qwen's crop around a drawn box: this share of the box's size on each side (0.5 otherwise)
MIDDLE = "in the middle of the picture"
OTHER_SEED = 7919    # added to the job's seed for the second FIBO Replace (a random seed stays random)
SPOT_SECOND = "FIBO Replace 2"   # the words under the second one's thumbnail
# The panel's 11 adds all ended by pointing at the box with "here" ("Add a round wall clock here."). That plan is kept
# for such an add; any other add in a box keeps the plan it had (FIBO Replace, Qwen, FIBO Preserve): one that names the
# subject it goes on ("to the man", "on his head": Preserve keeps the subject), a look over an area ("thick fog",
# "snow"), lettering, or an add that does not end with "here" (sunglasses in a box round a face).
# Only the closing "here" is the box: one inside the sentence may be anything ("here's", "here and there", "up here").
_HERE = re.compile(r"\s*\b(?:(?:right|over)\s+)?here\s*([.!]*)\s*$", re.IGNORECASE)
# Words to be written, which _LETTERING misses: in quotes (an opening straight quote, not an apostrophe as in "man's"
# or "don't"), after a colon or a word that brings them in, or "with ... on it". Qwen is the one family that spells
# them, so it gets them as typed.
_WRITTEN = re.compile(
    r"[\"“”‘’‚‛«»‹›„「」『』:]|(?:^|(?<=[\s(\[]))'"
    r"|\b(?:read|reads|reading|say|saying|said|labell?ed|titled|entitled|named|inscribed|inscription|engraved|embossed|"
    r"printed|stamped|stencill?ed|embroidered|lettered|monogrammed|slogan|motto|logo|initials|spells?|spelled|spelt)\b"
    r"|\bon (?:it|them)\b",
    re.IGNORECASE)
# Capitalised words around "Here" in the middle of a sentence are a name or a sign's words: "with Welcome Here on it".
_TITLED = re.compile(r"(?:\b[A-Z][\w'’]*\s+)+(?:Here|HERE)\b|\b(?:Here|HERE)(?:\s+[A-Z][\w'’]*)+")
# The subject the new thing goes on, already in the picture: "to the man", "on her face", "around his neck".
_ONTO = re.compile(r"\b(?:his|her|hers|their|theirs|him|them|its)\b"
                   r"|(?<!\bnext )(?<!\bclose )(?<!\bcloser )(?<!\bup )\b(?:to|onto)\s+(?:the|this|that|these|those|a|an|my|our|your|it)\b",
                   re.IGNORECASE)
# A look laid over an area rather than a thing in an empty spot.
_OVER_AREA = re.compile(
    r"\b(?:fog|foggy|mist|misty|haze|hazy|smoke|smoky|steam|snow|snowy|snowfall|snowflakes|rain|raindrops|drizzle|frost|"
    r"frosty|dew|sunlight|sunshine|lighting|glow|glowing|shadows?|shade|sunbeams?|rays|flare|bokeh|blur|grain|noise|texture|"
    r"pattern|tint|sparkles?|glitter)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Step:
    """One of Automatic's edits: the model, the words under its thumbnail, the settings it runs with, the words the
    model gets when they differ from the artist's instruction, and a number added to the job's seed."""

    model: str
    label: str
    settings: dict = field(default_factory=dict)
    prompt: str = ""
    seed: int = 0


def _lettered(prompt: str) -> bool:
    """True when ``prompt`` gives words to be written (a sign, a plaque, a doormat)."""
    if _WRITTEN.search(prompt):
        return True
    for match in _TITLED.finditer(prompt):
        before = prompt[:match.start()].rstrip()
        if before and before[-1] not in ".!?":   # not just the first words of a sentence
            return True
    return False


def _into_spot(prompt: str) -> bool:
    """True for an add the spot panel rated: one that ends by pointing at the drawn box with "here", writes no words,
    names no subject it goes on and is no look over an area."""
    return (bool(_HERE.search(prompt)) and not _lettered(prompt) and not _ONTO.search(prompt)
            and not _OVER_AREA.search(prompt))


def told(prompt: str) -> str:
    """``prompt`` for a model that cannot see the drawn box it works in: the closing "here" becomes where the box is on
    the crop it gets (its middle). Anything else is returned as it is: words to be written are never changed."""
    if _lettered(prompt):
        return prompt
    return _HERE.sub(lambda match: " " + MIDDLE + match.group(1), prompt, count=1).strip()


def drawn_box(selection) -> bool:
    """True for a selection drawn as a box (a marquee), not picked as an object."""
    if selection is None:
        return False
    solid = _solid(selection)
    box = solid.getbbox()
    if box is None:
        return False
    return solid.histogram()[255] >= BOX * (box[2] - box[0]) * (box[3] - box[1])


def shape(prompt: str, share: float, selection=None) -> str:
    """What the edit does to the selected thing, for its room and for FIBO's mode: "whole" for the whole picture, "swap"
    when a different object takes its place, "spot" when something is added in an empty box, else "look" (the selected
    thing stays, with a new look or something added onto it)."""
    found = kind(prompt, share)
    if found != "object":
        return found
    if _SWAPPED.search(prompt):
        return "swap"
    return "spot" if drawn_box(selection) else "look"


def plan(prompt: str, share: float, available, selection=None) -> list[Step]:
    """Automatic's edits in the order they are shown, with the settings each runs with. FIBO runs in the mode that suits
    the edit, and, as a third result, in the other: which one an edit needs cannot be read from its words (a t-shirt made
    a hoodie kept its shape best in Preserve, tulips made sunflowers in Replace). Something added "here" in a drawn box
    gets FIBO Replace, Qwen told where the box is, and FIBO Replace with another seed (see TIGHT and _HERE)."""
    found = shape(prompt, share, selection)
    spot = found == "spot" and _into_spot(prompt)
    fibo_model = next((model for model in FAMILIES["fibo"] if model in available), None)
    if spot and fibo_model is not None:
        # FIBO Preserve, and Qwen told "here", redrew the spot without the object: Replace twice and Qwen told where.
        replace = dict(SETTINGS, **MODES["replace"][0])
        result = [Step(fibo_model, MODES["replace"][1], replace)]
        qwen = next((model for model in FAMILIES["qwen"] if model in available), None)
        if qwen is not None:
            result.append(Step(qwen, short_label(qwen), dict(SETTINGS, context_padding=TIGHT), prompt=told(prompt)))
        result.append(Step(fibo_model, SPOT_SECOND, replace, seed=OTHER_SEED))
        return result
    mode, other = ("replace", "preserve") if found in ("swap", "spot") else ("preserve", "replace")
    result = []
    for model in steps(prompt, share, available):
        if model in FAMILIES["fibo"]:   # named by its mode, as the other mode is shown beside it
            result.append(Step(model, MODES[mode][1], dict(SETTINGS, **MODES[mode][0])))
        elif spot and model in FAMILIES["qwen"]:   # a drawn box on an engine without FIBO
            result.append(Step(model, short_label(model), dict(SETTINGS, context_padding=TIGHT), prompt=told(prompt)))
        else:
            result.append(Step(model, short_label(model), dict(SETTINGS)))
    fibo = next((step for step in result if step.model in FAMILIES["fibo"]), None)
    if fibo is not None and found != "whole":
        result.append(Step(fibo.model, MODES[other][1], dict(SETTINGS, **MODES[other][0])))
    return result


def steps(prompt: str, share: float, available) -> list[str]:
    """Installed models to use, in the order their results are shown, for ``prompt`` on a selection covering
    ``share`` (0..1) of the picture. At most ``RESULTS``, one per family."""
    found = []
    for family in ORDERS[kind(prompt, share)]:
        model = next((model for model in FAMILIES[family] if model in available), None)
        if model is not None:
            found.append(model)
    return found[:RESULTS]


def offered(available) -> bool:
    """True when the models installed here can make ``RESULTS`` edits of a selection. (An edit of the whole
    picture may then still get one result, from the family that rated best for it.)"""
    return len(steps("", 0.0, available)) >= RESULTS and bool(steps("", 1.0, available))


def _solid(mask: Image.Image, level: int = 128) -> Image.Image:
    return mask.point(lambda value: 255 if value >= level else 0)


def _parts(binary: Image.Image) -> list[list[tuple[int, int, int]]]:
    """The pieces of a black-and-white mask that hang together (touching at a corner is enough), each as its
    white stretches (row, first pixel, pixel after the last)."""
    width, height = binary.size
    data = binary.tobytes()
    stretches: list[tuple[int, int, int]] = []
    leader: list[int] = []   # for each stretch an earlier stretch of its piece, or itself
    above: list[int] = []

    def first(index: int) -> int:
        while leader[index] != index:
            leader[index] = leader[leader[index]]
            index = leader[index]
        return index

    for y in range(height):
        here, offset = [], y * width
        start = data.find(b"\xff", offset, offset + width)
        while start >= 0:
            end = data.find(b"\x00", start, offset + width)
            end = offset + width if end < 0 else end
            index = len(stretches)
            stretches.append((y, start - offset, end - offset))
            leader.append(index)
            here.append(index)
            for other in above:
                if stretches[other][1] <= end - offset and stretches[other][2] >= start - offset:
                    one, two = first(other), first(index)
                    leader[max(one, two)] = min(one, two)
            start = data.find(b"\xff", end, offset + width)
        above = here
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for index, stretch in enumerate(stretches):
        groups.setdefault(first(index), []).append(stretch)
    return list(groups.values())


def _drawn(size: tuple[int, int], stretches, at: tuple[int, int] = (0, 0)) -> Image.Image:
    """``stretches`` white on black, moved back by ``at``."""
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    for y, start, end in stretches:
        draw.line([(start - at[0], y - at[1]), (end - 1 - at[0], y - at[1])], fill=255)
    return mask


def _holes(binary: Image.Image) -> Image.Image:
    """The black areas a black-and-white mask encloses: those not reached from its border. Pillow's flood
    runs in Python, so this is for the small copy only."""
    padded = Image.new("L", (binary.width + 2, binary.height + 2))
    padded.paste(binary, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128)
    return padded.crop((1, 1, binary.width + 1, binary.height + 1)).point(lambda value: 255 if value == 0 else 0)


def _hull(stretches) -> list[tuple[int, int]]:
    """The corners of the convex hull of one piece, given as its stretches (empty for a line or a dot)."""
    points = sorted({(start, y) for y, start, _end in stretches} | {(end - 1, y) for y, _start, end in stretches})
    if len(points) < 3:
        return []

    def half(sequence):
        chain = []
        for point in sequence:
            while len(chain) >= 2 and ((chain[-1][0] - chain[-2][0]) * (point[1] - chain[-2][1])
                                       - (chain[-1][1] - chain[-2][1]) * (point[0] - chain[-2][0])) <= 0:
                chain.pop()
            chain.append(point)
        return chain

    outline = half(points)[:-1] + half(list(reversed(points)))[:-1]
    return outline if len(outline) >= 3 else []


def _box(mask: Image.Image) -> tuple[int, int, int, int]:
    """The box of ``mask`` the copy is made of: where it is at LOW or more (weak parts that stick out of the
    half-strength box belong on the copy too), grown by PAD."""
    box = _solid(mask, LOW).getbbox()
    pad = max(2, round(max(box[2] - box[0], box[3] - box[1]) * PAD))
    return max(0, box[0] - pad), max(0, box[1] - pad), min(mask.width, box[2] + pad), min(mask.height, box[3] + pad)


def _middle(hole: Image.Image) -> Image.Image:
    """The innermost INNER of ``hole`` (white, with a black border so its rim peels from every side)."""
    area = hole.histogram()[255]
    middle = hole
    while middle.histogram()[255] > INNER * area:
        thinner = middle.filter(ImageFilter.MinFilter(3))
        if thinner.getbbox() is None:
            break
        middle = thinner
    return middle


def _surroundings(chosen: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """True for a selection that is the picture around something (an inverted subject, a sky, a wall) rather
    than an object. Growing it, or a hull over it, would take in what the artist left out."""
    edges = (box[0] <= 0) + (box[1] <= 0) + (box[2] >= chosen.width) + (box[3] >= chosen.height)
    return edges >= 3 or chosen.histogram()[255] > SURROUNDINGS * chosen.width * chosen.height


def _firm(matte: Image.Image, swap: bool):
    """The object in ``matte`` as the edit should treat it, at ``matte``'s size: (lift, shape, hulls, longest, gaps, body).

    ``lift`` is what goes to full strength: for a swap the whole object (a half-transparent old object would
    show through the new one), for a recolour only the holes where the model was unsure, with the rise to full
    strength around them (the matte's own soft edge and the artist's feather stay as they are). ``shape`` is
    the object at full strength, ``hulls`` the convex hull of each of its parts (a swap only; one hull over
    all of them took in what lay between), ``longest`` the longer side of its largest part, ``gaps`` the holes
    the edit must keep out of (None for none): the picture inside a frame, a detail the artist left out, and
    ``body`` the object at LOW or more: the half-strength matte and the weak parts that hang together with it."""
    from .local_removal import _dilate

    core = _solid(matte)
    box = _box(matte)
    full = (box[2] - box[0], box[3] - box[1])
    scale = min(1.0, WORK / max(full))
    size = (max(1, round(full[0] * scale)), max(1, round(full[1] * scale)))
    small = matte.crop(box).resize(size, Image.Resampling.BOX)
    centre = _solid(small)
    parts = _parts(centre)
    rows: dict[int, list[tuple[int, int, int]]] = {}
    for number, part in enumerate(parts):
        for y, start, end in part:
            rows.setdefault(y, []).append((start, end, number))
    areas = [sum(end - start for _y, start, end in part) for part in parts]
    filled, kept = [], []
    for hole in _parts(_holes(centre)):
        y, start, _end = min(hole)   # the pixel left of a hole's first stretch belongs to the part around it
        around = next((number for first, last, number in rows.get(y, ()) if first <= start - 1 < last), None)
        area = sum(end - start for _y, start, end in hole)
        if around is None or area >= areas[around]:
            kept += hole   # as large as the part around it: the picture in a frame, not a gap in the object
            continue
        if not swap:
            # Judged on its middle: an artist's feather leaves a detail they deselected matted at LOW near its rim
            # too, but falls towards its middle, where the model's doubt stays level (a patch it was unsure of).
            left, top = min(start for _y, start, _end in hole) - 1, hole[0][0] - 1   # a hole never touches the copy's edge
            right, bottom = max(end for _y, _start, end in hole) + 1, hole[-1][0] + 2
            middle = _middle(_drawn((right - left, bottom - top), hole, (left, top)))
            matted = ImageChops.darker(middle, _solid(small.crop((left, top, right, bottom)), LOW)).histogram()[255]
            if matted < UNSURE * middle.histogram()[255]:
                kept += hole   # clear of the matte in its middle: the artist or the model left it out
                continue
        filled += hole

    def placed(mask: Image.Image, spread: int = 0) -> Image.Image:
        if spread:   # one small pixel more, so no seam is left between the copy's outline and the matte's
            mask = _dilate(mask, spread)
        out = Image.new("L", matte.size)
        out.paste(mask.resize(full, Image.Resampling.NEAREST), box[:2])
        return out

    outline = placed(ImageChops.lighter(centre, _holes(centre)))
    lift = Image.new("L", matte.size)
    if filled:
        lifted = _drawn(size, filled)
        if not swap:
            # The rise from a lifted hole to the full-strength matte around it goes too: left as it was, it drew
            # the hole's outline as a thin ring at partial strength. Only where both are near, so a hole in a
            # half-selected glass does not take the glass with it.
            rim = max(1, math.ceil(RIM * scale))
            rise = ImageChops.darker(_dilate(lifted, rim), _dilate(_solid(small, FULL), rim))
            lifted = ImageChops.lighter(lifted, ImageChops.darker(rise, centre))
        lift = ImageChops.darker(placed(lifted, 1), outline)
    touching = centre.tobytes()
    joined = [part for part in _parts(_solid(small, LOW))
              if any(touching.find(b"\xff", y * size[0] + start, y * size[0] + end) >= 0 for y, start, end in part)]
    body = _drawn(size, [stretch for part in joined for stretch in part])
    hulls, longest = None, 0.0
    if swap:
        solid = ImageChops.lighter(body, _drawn(size, filled))
        lift = ImageChops.lighter(ImageChops.lighter(core, lift), ImageChops.multiply(_solid(matte, LOW), placed(solid, 1)))
        pieces = _parts(solid)
        hulls = Image.new("L", matte.size)
        draw = ImageDraw.Draw(hulls)
        across, down = full[0] / size[0], full[1] / size[1]
        for piece in pieces:
            corners = _hull(piece)
            if corners:
                draw.polygon([(box[0] + (x + 0.5) * across, box[1] + (y + 0.5) * down) for x, y in corners], fill=255)
        if pieces:
            largest = max(pieces, key=lambda piece: sum(end - start for _y, start, end in piece))
            longest = max((max(end for _y, _start, end in largest) - min(start for _y, start, _end in largest)) * across,
                          (largest[-1][0] - largest[0][0] + 1) * down)
        if kept:
            hulls = ImageChops.subtract(hulls, placed(_drawn(size, kept)))
        hulls = ImageChops.lighter(hulls, lift)
    shape = ImageChops.lighter(core, lift)
    gaps = None
    if kept:
        gaps = ImageChops.darker(ImageChops.darker(placed(_drawn(size, kept), 1), outline), ImageChops.invert(shape))
        if gaps.getbbox() is None:
            gaps = None
    return lift, shape, hulls, longest, gaps, placed(body, 1)


def _grown(matte: Image.Image, lift: Image.Image, reach: int, fence: Image.Image | None, body: Image.Image) -> Image.Image:
    """``matte`` with ``lift`` at full strength and both grown by ``reach``, kept out of ``fence``. Weak matte
    grows only within reach of ``body`` and ``lift``: farther out it is a neighbour's (the cable by a mug), and
    growing it spread the edit along the neighbour.

    Nothing is taken away, and no two neighbouring pixels differ more than in the matte itself or across a
    ramp of at least five pixels: pasting a hard shape over a soft matte left a cliff that Auto-feather does
    not soften (a crisp outline where the coat stayed beige)."""
    from .local_removal import _dilate

    half = max(2, (reach - 1) // 2)   # the ramps are 2 * half + 1 pixels wide: no step over 51 levels
    # Where the weak matte may grow: all of it within reach of the object, then a ramp, so no cut is drawn
    # across a neighbour that comes close. A smooth ramp, so it is found on a smaller copy: at full size it took
    # a 48 MP picture a second longer.
    step = max(1, half // TAPER)
    near = ImageChops.lighter(ImageChops.lighter(_solid(matte), lift), body)
    if step > 1:
        near = near.reduce(step).point(lambda value: 255 if value else 0)   # a block with any of the object in it
    near = _dilate(near, -(-(reach + half) // step) + 1).filter(ImageFilter.BoxBlur(max(1, half // step)))
    if step > 1:
        near = near.resize(matte.size, Image.Resampling.BILINEAR, box=(0, 0, matte.width / step, matte.height / step))
    weak = ImageChops.multiply(matte.point(lambda value: value if value >= LOW else 0), near)   # a faint halo stays as it was
    grown = _dilate(weak, reach)
    if lift.getbbox() is not None:
        grown = ImageChops.lighter(grown, ImageChops.lighter(lift, _dilate(lift, half).filter(ImageFilter.BoxBlur(half))))
    if fence is not None:
        guard = ImageChops.lighter(fence, _dilate(fence, half).filter(ImageFilter.BoxBlur(half)))
        grown = ImageChops.darker(grown, ImageChops.invert(guard))
    return ImageChops.lighter(matte, grown)


def roomy(selection: Image.Image, kind: str, feather_percent: float) -> tuple[Image.Image | None, bool]:
    """The selection an Automatic edit works in (None for the artist's own, as it is), and whether a
    new object was given room.

    ``kind`` is "whole" (left alone), "look" (the object stays: a new colour, material, lettering or something
    on it) or "swap" (a different object takes its place; "object" means the same). ``feather_percent`` is
    Auto-feather's amount, 0 when it is off: the selection is grown until the band Auto-feather softens lies
    outside the object, so the object's edge gets the whole edit. A selection that is the picture around
    something is left as it is, and nothing grows into a hole the growth cannot close."""
    from .local_removal import _dilate

    chosen = _solid(selection)
    box = chosen.getbbox()
    if kind == "whole" or box is None or _surroundings(chosen, box):
        return None, False
    swap = kind in ("swap", "object")
    feather = 0 < feather_percent < 100
    if not swap and not feather:
        return None, False
    # Work on the part of the picture the selection, its room and its growth can reach: fast on a large picture.
    # The margin comes from the box the copy is made of, as the ring does: it is measured on a part that may
    # reach past the half-strength box (a weak arm joined to it), and was cut straight at the crop's edge.
    tail, work = selection.getbbox(), _box(selection)
    margin = round(max(work[2] - work[0], work[3] - work[1]) * ROOM) + REACH + 4
    area = (max(0, tail[0] - margin), max(0, tail[1] - margin),
            min(selection.width, tail[2] + margin), min(selection.height, tail[3] + margin))
    matte = selection.crop(area)
    core = _solid(matte)
    lift, shape, hulls, longest, gaps, body = _firm(matte, swap)
    region, ring, room = shape, 0, False
    if swap:
        if shape.histogram()[255] < SOLID * hulls.histogram()[255]:
            ring = max(2, round(longest * ROOM))
            region = lift = _dilate(hulls, ring)
            room = True
        else:
            room = shape.histogram()[255] >= ROOM_GAIN * core.histogram()[255]
    if not feather and not ring and ImageChops.difference(shape, core).getbbox() is None:
        return None, False
    reach = 0
    if feather:
        count = region.histogram()[255]
        edge = ImageChops.subtract(region, ImageChops.invert(_dilate(ImageChops.invert(region), 1))).histogram()[255]
        reach = min(REACH, max(1, round(count * feather_percent / (100 - feather_percent) / max(1, edge))))
    fence = None
    if gaps is not None:
        # A hole the growth closes is a gap in the matte; one it cannot close was left out and stays out.
        left = ImageChops.subtract(gaps, _dilate(shape, ring + reach))
        if left.getbbox() is not None:
            fence = ImageChops.darker(_dilate(left, ring + reach), gaps)
    fitted = _grown(matte, lift, reach, fence, body)
    if ImageChops.difference(fitted, matte).getbbox() is None:
        return None, False
    widened = selection.copy()
    widened.paste(fitted, area[:2])
    if sum(level * count for level, count in enumerate(widened.histogram())) >= WHOLE * 255 * widened.width * widened.height:
        return None, False   # the plan was made for a selection, not for the whole picture
    return widened, room

