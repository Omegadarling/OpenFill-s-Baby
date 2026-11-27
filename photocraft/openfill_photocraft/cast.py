# SPDX-License-Identifier: Apache-2.0
"""What a selected object casts on its surroundings: its shadow and its reflection.

Remove leaves a shadow or a reflection behind unless the artist selects it too, and Object Selection
never does. The selection model can find them when it is asked about the object by name, so three
questions go to it: what the selected object is, where "the shadow of the <name>" is and where
"the reflection of the <name>" is. A name counts only if the model's own selection for it, or a
piece of it, leads back to the artist's selection; when only a piece of it did, only the pieces of a
find that reach the object, and lie no nearer one of its look-alikes, are judged. A find counts as a shadow only if it touches the object and is
darker than the surface around it, as a reflection only if it lies under the object in the object's
own colours, and as either only if it does not run out on both sides of the object (a reflection,
whose sides are straight, a little past them) and is no larger than an object can cast. Anything else
leaves the selection as the artist made it.

Measured 2026-11-25 (judged by eye from overlay sheets): on the 75 removal
pictures of the evaluation corpus something was taken along on 18, the object's own shadow or
reflection on 17 of them (the other is a cone pasted in front of a motorcycle, which got the
motorcycle's floor shadow); 4 faint contact shadows were missed. On 26 objects in 24 scenes written
afterwards, the rules as tuned on the corpus took something on 12, all 12 right, and missed 10; with
bright reflections, reflections below a bank and look-alike neighbours added from those misses they
take something on 17, all 17 right (a neighbour's shadow, a dark notebook beside a mug and a dark
doorway behind a man are left alone), and miss 5. Asking for every "shadow" and keeping those that
touch the selection, which is what the first prototype did, attached the wrong thing on 3 of 16.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageFilter, ImageStat

from openfill.backend import GenerationCancelled

from .local_removal import _dilate, grow_support
from .pieces import pieces
from .wevisone import model_frame, named  # noqa: F401 - ``named`` is part of this module's vocabulary

OPTION = "With its shadow and reflection"  # the Remove option's label, in both panels
SECONDS = 30.0  # all the questions of one removal together; past that it goes ahead without them
# What the artist is told when the engine could not be asked in that time, since the shadow then stays.
BUSY = "The engine was too busy to look for the object's shadow and reflection. Select them too if they should go."
NAME_PROMPT = "What is the main object in this image? Answer with a short noun phrase."
KINDS = ("shadow", "reflection")
WORK = 512        # the checks run on a copy this long on its longer side
WHOLE = 0.6       # a selection over this share of the picture has no surroundings to search
LEADS_BACK = 0.5  # the model's selection for its own name: share of the artist's selection it covers, and share of it inside
OWN = 0.5         # a find with this share inside the selection is the object again
# The model's matte from this level up is the place it found. Below it is the model's doubt, not a soft
# edge: on a lamp post's shadow it covered 52% of the picture against 11%, and repaired at half strength.
SURE = 96
NEAR = 0.06       # how far from the object "touching" reaches, as a share of the object's longer side
TOUCH = 0.2       # pixels of the find within that reach, per pixel of the object's longer side
# A piece of a find is a neighbouring look-alike's only when it reaches that one this many working pixels
# before the selected one. Shrinking to the working size, where whatever is left of a look-alike counts,
# moves an edge by up to a pixel, and a glass's own shadow drawn onto the next glass's base overlapped
# that glass and touched its own glass one pixel later.
CLOSER = 2
# A piece of the name's matte smaller than this share of the selection is a speck of the model's, not another
# look-alike. The specks in the stored name mattes of that measurement were 0.6% of the selection at most,
# and a 6 x 6 speck lying in a glass's own shadow, 0.7% of the glass, counted as a look-alike in that shadow
# and handed the shadow away. Real pieces are small too: a wine glass's foot cut off by a stem the model lost
# (5.6%), a small glass beside a tall one (6.9%). At a tenth, the foot's band was kept and, with the glass's
# own, ran out on both sides of it, and the small glass's band was taken. 2% lies about three times from both.
# The cost: a neighbour under it loses its band to the selected one if it reaches it (a glass a sixtieth the
# size did), and a speck over it still hands a shadow it lies in away (one of 2.8%), as before the floor.
SPECK = 0.02
DARKER = 2.0      # levels (of 255) a shadow must be darker than the surface just around it
# A shadow runs to one side of its object. With this share of the find wholly left of the object and as
# much wholly right of it, the object stands in a shadow that is not its own: a cone pasted in front of a
# motorcycle 0.10 to 0.17, the 34 right finds of that measurement 0.05 at most (reflections
# included). The cost: a soft contact shadow centred under its object and wider than about 1.3 to 1.4
# times it is lost too, whatever the object's colour (a ball's: 1.3 times kept, 1.35 not).
# It holds for a reflection too, because a dark object's own colours let a floor shadow pass the
# reflection rule. But a reflection has straight sides: counted from the object's box, a post's
# reflection one working pixel wider on each side was lost. So what the reflection question returns is
# counted past the box grown by the touching reach (NEAR) of the object's WIDTH: a tall post's reach
# measured on its height let a floor band three times its width through. A centred reflection is kept up
# to about 1.3 times a boat's width and 1.4 times a box's (1.17 without the margin). A floor band under a
# dark object still runs far past it (three bottles: 0.24 and 0.28). The shadow question keeps the box
# itself, because past the margin the cone's motorcycle shadow measured 0.03.
BOTH_SIDES = 0.08
RESEMBLE = 0.6    # a reflection's colour: this much nearer the object's than the surroundings' colour is
CONTRAST = 40.0   # and the object must stand this far (in RGB) from those surroundings for that to mean anything
GAP = 0.5         # a reflection may start this share of the object's height below it (a bank between a cabin and the lake)
INLINE = 0.5      # and must then share this much of the narrower one's width with the object
LARGE = 1.2       # a find over this many times the object's area needs both questions to agree on it
LARGEST = 3.0     # and nothing over this many times is taken
AGREE = 0.7       # overlap (intersection over union) at which the two questions found the same place
FRAME = 0.25      # nor anything over this share of the picture
PENUMBRA = 0.08   # the find is grown by this share of the square root of its area, to take the soft edge along


@dataclass(frozen=True)
class Cast:
    """``mask``: what to add to the selection (canvas-sized, soft), or None. ``words``: what it is, for
    a notice ("shadow", "reflection", "shadow and reflection", or "shadow or reflection" when the model
    put both names on one place). ``name``: the model's name for the object, "" when none led back to
    the selection."""

    mask: Image.Image | None = None
    words: str = ""
    name: str = ""


def notice(words: str) -> str:
    """What the artist is told when more than the selection was removed."""
    several = " and " in words
    return (f"The object's {words} {'were' if several else 'was'} removed too. "
            f"Untick “{OPTION}” to keep {'them' if several else 'it'}.")


def _solid(mask: Image.Image, level: int = 128) -> Image.Image:
    return mask.point(lambda value: 255 if value >= level else 0)


def _count(mask: Image.Image) -> int:
    return mask.histogram()[255]


def _small(image: Image.Image) -> Image.Image:
    scale = WORK / max(image.size)
    if scale >= 1:
        return image
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.BILINEAR)


def name_pictures(image: Image.Image, selection: Image.Image, pad: float = 0.15):
    """The pictures the model names the object from, in the order they are tried: the object with a
    margin of its surroundings, then the object alone on flat grey. A hose or a cable has a crop full
    of other things, and the model names one of those. Both come from the full-size picture, so a
    small object keeps its detail."""
    left, top, right, bottom = _solid(selection).getbbox()
    margin = round(max(right - left, bottom - top) * pad)
    box = (max(0, left - margin), max(0, top - margin), min(image.width, right + margin), min(image.height, bottom + margin))
    picture, chosen = image.crop(box), _solid(selection.crop(box))
    yield model_frame(picture)
    edge = max(2, round(max(picture.size) / 400))
    soft = _dilate(chosen, edge).filter(ImageFilter.GaussianBlur(edge))
    yield model_frame(Image.composite(picture, Image.new("RGB", picture.size, (128, 128, 128)), soft))


def back(found: list[Image.Image], chosen: Image.Image) -> dict:
    """What ``leads_back`` decides by: ``covered``, the share of the selection under the mattes that lie
    mostly inside it, and in ``each`` per matte the share of it ``inside`` the selection and how it counted.

    A matte that does not lie mostly inside counts by its pieces instead: three bottles that look alike
    can come back as one matte, and so can a rider and his image in a puddle, and the selected one is
    then a third or a half of it. A matte that counts whole is not split, so nothing that led back
    before stops leading back."""
    needed, inside, rows = LEADS_BACK * max(1, _count(chosen)), 0, []
    for matte in found:
        solid = _solid(matte)
        size, both = _count(solid), _count(ImageChops.darker(solid, chosen))
        row = {"inside": both / max(1, size), "counted": ""}
        if both >= LEADS_BACK * max(1, size):
            inside += both
            row["counted"] = "whole"
        elif both:
            for piece in pieces(solid, touching=chosen):
                part = _count(ImageChops.darker(piece, chosen))
                if part >= LEADS_BACK * max(1, _count(piece)):
                    inside += part
                    row["counted"] = "pieces"
        rows.append(row)
    return {"covered": inside / max(1, _count(chosen)), "each": rows, "leads_back": inside >= needed}


def leads_back(found: list[Image.Image], chosen: Image.Image) -> bool:
    """True when the model's selection for a name is the artist's selection, near enough. ``found`` is
    one matte per thing the model matched: "the man" matches both men in a picture of two, and the
    one that was selected is among them."""
    return back(found, chosen)["leads_back"]


def _luma(colour) -> float:
    return 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]


def measure(picture: Image.Image, chosen: Image.Image, matte: Image.Image) -> dict | None:
    """What decides whether a matte is the object's own shadow or reflection. Everything is at one
    working size; ``picture`` is RGB and ``chosen`` the selection in black and white. None for a matte
    that is empty, or is the object again."""
    solid = _solid(matte, SURE)
    size = _count(solid)
    if not size:
        return None
    outside = ImageChops.subtract(solid, chosen)
    beyond = _count(outside)
    if not beyond or size - beyond >= OWN * size:
        return None
    box = chosen.getbbox()
    width, height = box[2] - box[0], box[3] - box[1]
    extent = max(width, height)
    reach = max(2, round(extent * NEAR))
    near = ImageChops.subtract(grow_support(chosen, reach), chosen)
    ring = ImageChops.subtract(ImageChops.subtract(grow_support(solid, max(3, round(extent * 0.08))), solid), chosen)
    own, found = ImageStat.Stat(picture, chosen).mean, ImageStat.Stat(picture, outside).mean
    around = ImageStat.Stat(picture, ring).mean if _count(ring) else [255.0, 255.0, 255.0]
    stands_out = math.dist(around, own)
    left, top, right, _bottom = outside.getbbox()
    shared = min(box[2], right) - max(box[0], left)
    wholly_left = _count(outside.crop((0, 0, box[0], outside.height)))
    wholly_right = _count(outside.crop((box[2], 0, outside.width, outside.height)))
    # The same past the object's box grown by the touching reach, for a reflection: it has straight sides,
    # so its matte a pixel wider than a post would otherwise run out on both sides (see BOTH_SIDES).
    margin = max(2, round(width * NEAR))   # the object's width: a post's height says nothing about its reflection's sides
    far_left = _count(outside.crop((0, 0, max(0, box[0] - margin), outside.height)))
    far_right = _count(outside.crop((min(outside.width, box[2] + margin), 0, outside.width, outside.height)))
    return {
        "area": beyond / max(1, _count(chosen)),
        "frame": size / (solid.width * solid.height),
        "touch": _count(ImageChops.darker(outside, near)) / max(1, extent),
        "darker": _luma(around) - _luma(found),
        # 0 when the find has the object's colour, 1 when it is as far from it as the surroundings are
        "unlike": math.dist(found, own) / stands_out if stands_out >= CONTRAST else math.inf,
        # where a mirror image lies: starting in the object's lower half or a little below it, and in line with it
        "under": top >= box[1] + height / 2 and top - box[3] <= GAP * height and shared >= INLINE * min(width, right - left),
        # shares of the find lying wholly left and wholly right of the object
        "left": wholly_left / beyond,
        "right": wholly_right / beyond,
        # and past the touching reach on each side, which a reflection is judged by
        "far_left": far_left / beyond,
        "far_right": far_right / beyond,
        "solid": solid,
    }


def _reach(chosen: Image.Image) -> Image.Image:
    """Where a piece of a find has to come to be the selected object's: as near as ``measure`` counts as
    touching, or below the object where its reflection may start (``GAP``)."""
    box = chosen.getbbox()
    width, height = box[2] - box[0], box[3] - box[1]
    reach = grow_support(chosen, max(2, round(max(width, height) * NEAR)))
    reach.paste(255, (box[0], box[1] + height // 2, box[2], min(chosen.height, round(box[3] + GAP * height))))
    return reach


@dataclass(frozen=True)
class Alike:
    """Another look-alike than the selected one (``_look_alikes``): at the working size, at full size, and
    whether it lies where the selection's mirror image would (``_mirror_place``). Its boxes and its size
    are worked out once here, since every piece of every find is held against it (``_nearer_another``)."""

    small: Image.Image
    whole: Image.Image
    mirror: bool
    box: tuple[int, int, int, int] | None = field(init=False)        # of ``whole``
    size: int = field(init=False)                                     # pixels of ``whole``
    small_box: tuple[int, int, int, int] | None = field(init=False)  # of ``small``

    def __post_init__(self):
        box = self.whole.getbbox()
        object.__setattr__(self, "box", box)
        object.__setattr__(self, "size", _count(self.whole.crop(box)) if box is not None else 0)
        object.__setattr__(self, "small_box", self.small.getbbox())


def _overlap(one: tuple[int, int, int, int] | None, two: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    """Where two boxes overlap, or None when they do not or either is missing."""
    if one is None or two is None:
        return None
    box = (max(one[0], two[0]), max(one[1], two[1]), min(one[2], two[2]), min(one[3], two[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def _mirror_place(box: tuple[int, int, int, int], other: tuple[int, int, int, int]) -> bool:
    """Whether the box ``other`` lies where the mirror image of the object in ``box`` would, as ``measure``
    counts ``under``: starting in the object's lower half or a little below it (``GAP``), and in line with it."""
    width, height = box[2] - box[0], box[3] - box[1]
    shared = min(box[2], other[2]) - max(box[0], other[0])
    return other[1] >= box[1] + height / 2 and other[1] - box[3] <= GAP * height and shared >= INLINE * min(width, other[2] - other[0])


def _look_alikes(mattes: list[Image.Image], rows: list[dict], chosen: Image.Image,
                 low: Image.Image) -> tuple[Image.Image, list[Alike], int] | None:
    """The look-alikes a name led back through by a piece of its matte, at the working size of ``low`` (the
    selection there): of the pieces of each matte ``back`` counted by its pieces (``rows``), the one the
    selection covers, as one mask together with the selection, and the others from ``SPECK`` of the
    selection's size up one by one (``Alike``); with the touching reach at that size. None when there are no
    others."""
    # The selection is the selected one too: the model's piece of it can miss its foot, and its own shadow
    # would then lie nearer a neighbour than the piece.
    mine, theirs, speck = chosen, [], SPECK * _count(chosen)
    for matte, row in zip(mattes, rows, strict=False):
        if row["counted"] != "pieces":
            continue
        for piece in pieces(_solid(matte)):
            size = _count(piece)
            # the test ``back`` counts a piece by
            if _count(ImageChops.darker(piece, chosen)) >= LEADS_BACK * max(1, size):
                mine = ImageChops.lighter(mine, piece)
            elif size >= speck:
                theirs.append(piece)
    box = low.getbbox()
    reach = max(2, round(max(box[2] - box[0], box[3] - box[1]) * NEAR))   # as ``measure`` counts touching
    place = chosen.getbbox()

    def shrunk(mask: Image.Image) -> Image.Image:
        # Whatever is left of it after shrinking counts, so a thin one does not vanish at the working size.
        return _solid(mask.resize(low.size, Image.Resampling.BILINEAR), 1)

    others = [Alike(small, piece, _mirror_place(place, piece.getbbox()))
              for small, piece in zip(map(shrunk, theirs), theirs, strict=False) if small.getbbox() is not None]
    return (shrunk(mine), others, reach) if others else None


def _nearer_another(piece: Image.Image, alikes: tuple[Image.Image, list[Alike], int]) -> bool:
    """Whether a piece of a find lies nearer another look-alike than the selected one (``_look_alikes``).
    The piece is grown a working pixel at a time, and it is the other one's when it meets it within the
    touching reach and ``CLOSER`` pixels before it meets the selected one. Anything less, or out of reach of
    them all, leaves it to the selected one as before: a reflection that starts below a bank is out of reach
    of all of them, and one straight under the object is never nearer a look-alike beside it.

    A look-alike that lies mostly in the piece is in it, whatever the piece meets first: taking the piece
    would take that look-alike too (the model returned a bottle with its contact pool as the shadow of the
    bottle beside it). Only where the selected one's mirror image would lie is such a look-alike, or one the
    piece lies mostly in, the find itself and no neighbour: the model can put an object's image in a puddle
    into the matte for its name (``back``). The shares are counted at full size: shrunk to the working
    size, a thin contact line at a neighbour's foot overlapped it by half."""
    mine, theirs, reach = alikes
    grown = _solid(piece.resize(mine.size, Image.Resampling.BILINEAR), 1)
    box = grown.getbbox()
    if box is None:
        return False
    # Nothing past the last step is looked at, so only that much around the piece is needed.
    last = reach + CLOSER - 1
    area = (max(0, box[0] - last), max(0, box[1] - last), min(grown.width, box[2] + last), min(grown.height, box[3] + last))
    # The shares are counted only where the piece's box and a look-alike's meet, since outside that one of
    # the two is empty: counted over the whole picture for every piece and look-alike, a shelf of 26 glasses
    # took 3 s a find.
    place = piece.getbbox()
    size, others = _count(piece.crop(place)), Image.new("L", (area[2] - area[0], area[3] - area[1]))
    for other in theirs:
        meet = _overlap(place, other.box)
        # One mostly in the other by the share at which a find inside the selection is the object again (OWN).
        both = _count(ImageChops.darker(piece.crop(meet), other.whole.crop(meet))) if meet else 0
        whole = other.size
        if other.mirror and both >= OWN * min(size, whole):
            continue
        if both >= OWN * whole:
            return True
        if _overlap(area, other.small_box):
            others = ImageChops.lighter(others, other.small.crop(area))
    grown, mine = grown.crop(area), mine.crop(area)
    met = None   # the step at which the piece met another look-alike
    for step in range(last + 1):
        if step:
            grown = grown.filter(ImageFilter.MaxFilter(3))
        if ImageChops.darker(grown, mine).getbbox() is not None:
            return False
        if met is None and step <= reach and ImageChops.darker(grown, others).getbbox() is not None:
            met = step
        if met is not None and step - met + 1 >= CLOSER:
            return True
    return False


def _own(matte: Image.Image, reach: Image.Image,
         alikes: tuple[Image.Image, list[Alike], int] | None = None) -> tuple[Image.Image, int, int]:
    """The pieces of ``matte`` (the part from ``SURE`` up) that come into ``reach`` and, given the
    look-alikes, are not nearer another of them (``_nearer_another``), with the matte's own levels; how
    many there are, and how many came into reach but were nearer another look-alike."""
    kept, count, theirs = Image.new("L", matte.size), 0, 0
    for piece in pieces(_solid(matte, SURE), touching=reach):
        if alikes is not None and _nearer_another(piece, alikes):
            theirs += 1
            continue
        kept, count = ImageChops.lighter(kept, piece), count + 1
    return ImageChops.darker(matte, kept), count, theirs


def _same_place(first: Image.Image, second: Image.Image) -> float:
    return _count(ImageChops.darker(first, second)) / max(1, _count(ImageChops.lighter(first, second)))


def mirrored(measured: dict) -> bool:
    """Whether a find ``measure`` measured lies where the object's mirror image would and has its colours."""
    return bool(measured["under"]) and measured["unlike"] <= RESEMBLE


def accepted(found: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    """The finds that pass, as (kind, measurements). ``found`` maps each kind to its measured mattes.

    A shadow touches the object and is darker than the surface around it. A reflection lies under the
    object and has its colours, whether it is darker than the water or brighter; it may start a little
    below the object instead of touching it. Neither runs out on both sides of the object: a dark area
    out on both sides is a shadow the object stands in, and under a dark object it would pass as a
    reflection as well. A reflection is counted from a little past the object's sides, since its own
    sides are straight. Measurements without the side shares, or without the far ones, are judged as
    they were before those were added."""
    kept = []
    for kind, items in found.items():
        others = [other for name, rows in found.items() if name != kind for other in rows]
        for item in items:
            left, right = item.get("left", 0.0), item.get("right", 0.0)
            sides = min(left, right)
            # Only what the reflection question returned gets the margin: a shadow's sides are not straight.
            far = min(item.get("far_left", left), item.get("far_right", right)) if kind == "reflection" else sides
            shadow = item["touch"] >= TOUCH and item["darker"] >= DARKER and sides < BOTH_SIDES
            if not (shadow or (mirrored(item) and far < BOTH_SIDES)):
                continue
            if item["frame"] > FRAME or item["area"] > LARGEST:
                continue
            if item["area"] > LARGE and not any(_same_place(item["solid"], other["solid"]) >= AGREE for other in others):
                continue
            kept.append((kind, item))
    return kept


def find(image: Image.Image, selection: Image.Image, ask, segment, cancelled=lambda: False,
         answers: list | None = None) -> Cast:
    """The selected object's shadow and reflection in ``image`` (RGB) for ``selection`` (L), both canvas-sized.

    ``ask(picture, question)`` returns the selection model's answer in words, ``segment(picture,
    description)`` its mattes for a description, each the size of the picture it was given. ``answers``,
    when given, gets every answer of the model with what the rules made of it, in the order asked."""
    image = image.convert("RGB")
    frame = model_frame(image)
    chosen = _solid(selection if selection.size == frame.size else selection.resize(frame.size, Image.Resampling.BILINEAR))
    if chosen.getbbox() is None or _count(chosen) > WHOLE * frame.width * frame.height:
        return Cast()
    notes = answers if answers is not None else []
    name, tried, alike, led = "", set(), False, ([], [])
    for number, picture in enumerate(name_pictures(image, selection), 1):
        if cancelled():
            return Cast()
        said = ask(picture, NAME_PROMPT)
        label = named(said)
        note = {"question": "name", "picture": number, "said": said, "name": label}
        notes.append(note)
        if not label or label.lower() in tried:
            continue
        tried.add(label.lower())
        mattes = segment(frame, f"the {label}")
        note.update(back(mattes, chosen), mattes=mattes)
        if note["leads_back"]:
            name, led = label, (mattes, note["each"])
            alike = not any(row["counted"] == "whole" for row in note["each"])
            break
    if not name:
        return Cast()
    small = _small(frame)
    low = _solid(chosen.resize(small.size, Image.Resampling.BILINEAR))
    if low.getbbox() is None:
        return Cast(name=name)
    # When the name led back only through a piece of a matte, the model put look-alikes into one matte, and
    # it puts their shadows into one matte as well: judged whole, every look-alike's shadow would go while
    # the look-alikes stay. Then only the pieces of a find that reach the selected object count, and of those
    # only the ones no nearer another look-alike: a neighbour's shadow can end inside the reach too. A name that
    # counted whole keeps the whole matte, so a shadow broken in two by a kerb is still taken whole.
    reach = _reach(chosen) if alike else None
    alikes = _look_alikes(*led, chosen, low) if alike else None
    found: dict[str, list[dict]] = {}
    for kind in KINDS:
        if cancelled():
            return Cast(name=name)
        try:
            mattes = segment(frame, f"the {kind} of the {name}")
        except Exception as err:
            # All the questions share one time limit, so a later one can run out of it. What the earlier
            # ones found and the rules take is still the object's; without it the removal would leave it.
            # Only a cancel, or a failure before anything was taken, ends it all.
            if isinstance(err, GenerationCancelled) or cancelled() or not accepted(found):
                raise
            notes.append({"question": kind, "failed": str(err)})
            break
        for matte in mattes:
            note = {"question": kind, "matte": matte, "measure": None, "kept": False}
            notes.append(note)
            judged = matte
            if reach is not None:
                judged, note["pieces"], note["theirs"] = _own(matte, reach, alikes)
            if judged.getextrema()[1] < 8:
                continue
            note["measure"] = measure(small, low, judged.resize(small.size, Image.Resampling.BILINEAR))
            if note["measure"] is not None:
                found.setdefault(kind, []).append({**note["measure"], "matte": judged, "note": note})
    kept = accepted(found)
    if not kept:
        return Cast(name=name)
    mask = Image.new("L", frame.size)
    for _kind, item in kept:
        item["note"]["kept"] = True
        # Only the part the rules judged: grown and blurred, so the rim is soft, and whole in the middle.
        solid = _solid(item["matte"], SURE)
        reach = max(2, round(math.sqrt(_count(solid)) * PENUMBRA))
        mask = ImageChops.lighter(mask, _dilate(solid, reach).filter(ImageFilter.GaussianBlur(max(1.0, reach / 2))))
        mask = ImageChops.lighter(mask, solid)
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.BILINEAR)
    kinds = [kind for kind in KINDS if any(kind == found_kind for found_kind, _item in kept)]
    one_place = len(kinds) == 2 and all(
        any(_same_place(item["solid"], other["solid"]) >= AGREE for other_kind, other in kept if other_kind != kind)
        for kind, item in kept
    )
    return Cast(mask, " or ".join(kinds) if one_place else " and ".join(kinds), name)
