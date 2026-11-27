# SPDX-License-Identifier: Apache-2.0
"""The parts of an object that Object Selection leaves out.

The selection model returns a bin without its wheel, a skier without the skis, a person without the
umbrella they hold. Asked for the same object "together with everything attached to it or held by
it", it returns them too. So every object selection is followed by that one question, and what the
answer adds is added, when the answer is about the same object (it holds the selection) and what it
adds is a part (well under the size of the selection, and more than the edge of a matte).

A part picked on purpose stays a part: asked about a lipstick's tip, the model answers with the
whole lipstick, six times its size, and that is turned down.

A string is not among "everything attached" for the model: a kite came back without it. Asked for
"the string, rope, cord, cable, leash or strap attached to" the object, it finds the string, so that
is the second question. Its answer counts where it hangs together with the selection and is a line:
long, thin, unbroken, and no larger than a part. The same answer held a speck on a bird far from the
kite, and for a string picked alone a piece of the kite, or a bird beside it and a bit at its end;
none of these is a line that runs on from the selection.

A part hangs on the object: a piece of the first answer that floats free of the selection is
something else it touched (the tip of a dachshund's tail, for its leash picked alone). Less than
LEAST of the selection counts only in pieces that reach OUT rims beyond it: the rest of a wheel or a
string that a click already holds three quarters of. An answer a few pixels looser all round never
reaches that far, whatever the object's size, because the rim is never narrower than LOOSE pixels.

An answer a little looser still leaves a ring just past the rim, all round the object, and a handle
or a string that touches the ring is one piece with it. So both answers are judged only on what lies
SLACK pixels past the rim. There the ring of an answer up to 8 px looser round a small object is
gone, and the handle or the string is a piece of its own. What is added is then taken from the
answer around that piece, so the part's root comes along, with the bit of the ring beside the root;
the rest of the ring does not. An object at the picture's edge, or less than a rim from it, has no ring
past it there, so that side counts as gone round; but then the ring has to lie along the other sides,
as skis under a skier cut off at the top pass both sides without lying along them, and nothing may run
past a corner along the next side as the skis do. A bag or a rod held beside a side lies along it only
where it crosses it. A strings answer that lies along the object, filled from it out, is the object
held loosely, not its string: a string crosses the line round the object where it leaves, and a ring
or a crescent of a loose answer lies along that line. A cord lying along the object a few pixels off it,
with background between, is a string.

A piece that lies under the object in its colours may be its reflection. It is added like any other
part, as in the first version of this step. Telling a reflection from a table's legs in the same wood took a third question,
which still cost the table its legs when the model answered it with the table's shadow, and the rule
rested on one case on record (a watercolour sailboat). Remove takes an object's reflection along
anyway.

Measured 2026-11-25: of the nine
selections in the evaluation corpus that had left a part out, the part comes back on eight (a bin's
wheel, a lipstick's tip, oars, skis, an umbrella, a balloon's string, a leash, and with the second
question a kite's string, 88% of it); the painted rigging of a watercolour sailboat does not. Of 77
selections that were whole, 74 stay as they were: a cable that had been cut short is completed to the
picture's edge, a sliver of a phone's side is added to one and part of the chair under a seated person
to another. Of nine parts selected on their own, six stay as they were and three grow by about 5%.
Before the second question: seven of nine, 75 of 77, seven of nine.

Since then, on CPU only (the saved additions given back as the model's answer to the question
that gave them; not yet run with the engine): the eight parts come back
as before, but for 0.6% of the boat's oars, which leave its hull at a shallow angle (see ``_kept``).
The kite's string and the leash picked alone get nothing; the umbrella picked alone grows by 4.1%
instead of 5.3%. The sailboat's hull gets its reflection, as in the first version. With three quarters of the
part already selected, the rest of the bin's wheel, the balloon's string and the leash now join (none
did). An answer 2 to 4 px looser all round adds nothing on discs, squares, bars, rings and lines 12 to
600 px across (the first version added something on 241 of 660 tries), nor one 5 to 8 px looser round discs and
squares of 10 to 60 px. With a handle or a string beside such an object, only the handle or the
string comes, with the bit of the ring beside its root. Answers 9 or 10 px looser round objects of 30
to 60 px still add most of their ring (0.7 to 1.5 times the object), as the version before this one
did; the first version refused those round objects of 30 to 40 px as more than MOST, its rim being 2 px there.
A square answer round a disc still adds its corners. A strings answer 9 to 12 px looser round squares
and discs of 100 to 400 px added its ring as a string (10 to 39% of the object), and so did 99 of 192
such answers held 1 to 3 px off centre; a parts answer 6 px looser round a 100 px object at the
picture's edge or in its corner added its ring (1872 and 1236 px). None of these adds anything now. A
string that comes in a strings answer that loose is lost with the ring. Several strings in one answer
(a hull's bow and stern lines and its stay, cables off both sides of a pole) still come, and so do skis
or a board right under a person cut off by the picture's edge. An object in a corner with a part along
one free side that sticks out past the other by no more than the slack band is cut at the band like a
ring shifted toward that other side, and the part loses what of it runs inside the band.

Fifth round of review (2026-11-26, synthetic, CPU only). A cord lying along the object 8 to 14 px off it
before it runs away (a kettle's cord along its base, a leash along a lying dog, a strap curled round a
camera, a halyard beside its pole) and four guy lines leaving a tent's apex 4 to 20 degrees off its walls
were turned down as the object held loosely; they come now, the cords whole and the guy lines but for
2 to 9% inside the slack band near the apex. A parts answer held 1 or 2 px off centre round an object at
the edge, with a rod off its tight side, added the whole ring with the rod (678 px for a 30 px mug); a
disc 1 to 5 px off the edge got its ring (82% of a 30 px disc); skis at the edge with a bag held beside
the skier were cut at the band (39 to 58% back). Each now gets what the same object away from the edge
gets: the rod with the bit of ring at its root, nothing, the skis whole. A sweep of answers 2 to 8 px
looser round squares and discs of 30 to 100 px, 0 to 8 px from the edge, adds nothing on all 1440 tries
(64 added something before, all discs 1 to 5 px off it). The 15 saved additions above give the same results.
"""

from __future__ import annotations

import math
import re

from PIL import Image, ImageChops

from openfill.backend import GenerationCancelled

from . import cast
from .local_removal import grow_support
from .pieces import pieces
from .wevisone import model_frame

OPTION = "With its attached parts"  # the Select option's label, in both panels
SECONDS = 20.0  # the questions of one selection together; past that the selection is used as it is
QUESTION = "the {name} together with everything attached to it or held by it"
COVERS = 0.8   # the answer must hold this share of the selection: the same object, and more
LEAST = 0.02   # of the selection's area. Less than this beyond its rim (judged past the slack band) is the edge of a matte, not a part
MOST = 0.6     # and more than this is what the selection belongs to (the lipstick, for its tip), not a part of it
RIM = 0.012    # the rim, as a share of the selection's longer side
LOOSE = 5      # and never less than this many pixels: an answer up to 4 px looser all round is the edge of a matte at any size
SLACK = 3      # px past the rim, which the answers are judged beyond: an answer 6 to 8 px looser round a small object leaves nothing there
OUT = 3        # under LEAST, a piece counts when it reaches this many rims beyond the selection (what a click left of the bin's wheel reaches 4)
NEAR = 0.04    # how far from the selection a matte may start and still be one piece with it
STRINGS = "the string, rope, cord, cable, leash or strap attached to the {name}"
THIN = 0.04    # a strand is no thicker than this share of the selection's longer side (its area over its length)
LONG = 0.3     # and at least this long, by the same measure
SOLID = 0.5    # and covers at least this many pixels per pixel of its length (strings on record 3 to 6, specks strewn along one 0.3)
HUGS = 0.05    # a strings answer filled from the object out to this share of the line just past the slack band holds it loosely: such halos
               # 0.08 and up (squares and discs of 60 to 800 px, 8 to 24 px looser, shifted up to 3 px), strings 0.024 at most (four leashes off a
               # dog's corners, a strap curled round a camera); a cord lying along the object a few px off it, or a guy line leaving it at a shallow
               # angle, is not filled from the object out and counts for nothing
ALONG = 0.15   # at the picture's edge, an answer on this share of the line just past the rim beside a side lies along that side: loose rings
               # 0.2 and up on every side they lie along (a 40 px disc 6 px looser), a pole or an arm across a side 0.04 to 0.1, and a bag held
               # beside it (0.22) nothing once the rows it reaches far out in are left out


def _solid(mask: Image.Image, level: int = 128) -> Image.Image:
    return mask.point(lambda value: 255 if value >= level else 0)


def _count(mask: Image.Image) -> int:
    return mask.histogram()[255]


def _rim(extent: int) -> int:
    return max(LOOSE, round(extent * RIM))


def _union(size: tuple[int, int], masks: list[Image.Image]) -> Image.Image:
    together = Image.new("L", size)
    for mask in masks:
        together = ImageChops.lighter(together, mask)
    return together


def _past(chosen: Image.Image, beyond: Image.Image, rim: int) -> Image.Image:
    """What of ``beyond`` lies past the slack band, SLACK pixels wide outside the rim."""
    return ImageChops.subtract(beyond, grow_support(chosen, rim + SLACK))


def parts(chosen: Image.Image, mattes: list[Image.Image]) -> tuple[bool, Image.Image | None]:
    """Whether the model's answer is about the selection, and the parts it adds to it (None when it
    adds none). ``chosen`` is the selection in black and white, at the mattes' size. The parts are
    given as far as they lie past the slack band; ``_kept`` takes them from the answer, roots and all."""
    box = chosen.getbbox()
    if box is None:
        return False, None
    extent = max(box[2] - box[0], box[3] - box[1])
    near = grow_support(chosen, max(3, round(extent * NEAR)))
    total = Image.new("L", chosen.size)
    for matte in mattes:
        solid = _solid(matte, 96)
        if _count(ImageChops.darker(solid, near)):
            total = ImageChops.lighter(total, solid)
    area = max(1, _count(chosen))
    if _count(ImageChops.darker(total, chosen)) < COVERS * area:
        return False, None
    rim = _rim(extent)
    beyond = ImageChops.subtract(total, grow_support(chosen, rim))
    # MOST is judged on all the answer adds past the rim, slack band included: a ring 9 or 10 px looser round an
    # object of 10 to 24 px is more than that, and is no part of it.
    if _count(beyond) > MOST * area:
        return True, None
    return True, _enough(chosen, _held(chosen, beyond, rim), rim)


def _ring(chosen: Image.Image, reach: int) -> Image.Image:
    """The line ``reach`` pixels out round the selection, one pixel wide, as far as it lies inside the picture."""
    return ImageChops.subtract(grow_support(chosen, reach), grow_support(chosen, reach - 1))


def _on(mask: Image.Image, line: Image.Image, box: tuple[int, int, int, int] | None = None) -> float:
    """The share of ``line`` (of the part of it inside ``box``, when given) that ``mask`` lies on."""
    on = ImageChops.darker(mask, line)
    if box is not None:
        line, on = line.crop(box), on.crop(box)
    return _count(on) / max(1, _count(line))


def _hugging(chosen: Image.Image, answer: Image.Image, reach: int) -> Image.Image:
    """What of ``answer`` lies against the selection out to ``reach`` pixels: grown from the selection a
    pixel at a time without leaving ``answer`` (and the selection), ``reach`` times. An answer that holds the
    object loosely is filled from the object out, so all of it that far out is reached; a cord lying beside
    the object with background between is reached only along itself from where it leaves the object, and a
    line leaving at a shallow angle only long after it is that far out."""
    box = chosen.getbbox()
    if box is None:
        return Image.new("L", chosen.size)
    region = (max(0, box[0] - reach), max(0, box[1] - reach), min(chosen.width, box[2] + reach), min(chosen.height, box[3] + reach))
    within = ImageChops.lighter(answer, chosen).crop(region)
    grown = chosen.crop(region)
    for _ in range(reach):
        grown = ImageChops.darker(grow_support(grown, 1), within)
    held = Image.new("L", chosen.size)
    held.paste(grown, region[:2])
    return held


def _flanks(box: tuple[int, int, int, int], size: tuple[int, int], side: int) -> tuple[tuple[int, int, int, int], ...]:
    """The picture beyond one side of ``box`` (left, top, right, bottom = 0 to 3): first the strip beside the
    side, then the two corners at its ends."""
    left, top, right, bottom = box
    width, height = size
    return (((0, top, left, bottom), (0, 0, left, top), (0, bottom, left, height)),
            ((left, 0, right, top), (0, 0, left, top), (right, 0, width, top)),
            ((right, top, width, bottom), (right, 0, width, top), (right, bottom, width, height)),
            ((left, bottom, right, height), (0, bottom, left, height), (right, bottom, width, height)))[side]


def _passes(part: Image.Image, region: tuple[int, int, int, int], box: tuple[int, int, int, int], side: int) -> int:
    """How many pixels past that side of ``box`` ``part`` (a mask cropped to ``region``, of the picture beyond
    that side) reaches (0 when it lies nowhere there)."""
    reach = part.getbbox() if part.width and part.height else None
    if reach is None:
        return 0
    return (box[0] - region[0] - reach[0], box[1] - region[1] - reach[1], region[0] + reach[2] - box[2], region[1] + reach[3] - box[3])[side]


def _apart(mask: Image.Image, far: Image.Image, region: tuple[int, int, int, int], side: int) -> Image.Image:
    """``mask`` cropped to ``region``, the strip beside one side, without the rows or columns across the side
    that hold anything of ``far``: a handle, a rod or a bag held beside the side lies along it only where it
    crosses it, and a ring beside it is judged on the rest."""
    crop = mask.crop(region)
    columns, rows = far.crop(region).getprojection()
    across = columns if side in (1, 3) else rows
    if not any(across):
        return crop
    flags = bytes(255 if value else 0 for value in across)
    stripe = Image.frombytes("L", (len(flags), 1) if side in (1, 3) else (1, len(flags)), flags)
    return ImageChops.subtract(crop, stripe.resize(crop.size, Image.Resampling.NEAREST))


def _around(chosen: Image.Image, hanging: Image.Image, rim: int) -> bool:
    """Whether ``hanging`` (what hangs on the selection past the rim) goes round the selection: past its
    box on every side. A side at the picture's edge counts as gone round, since no answer reaches past the
    picture: a 100 px object at the bottom edge, with an answer 6 px looser on its other three sides, got
    that answer's ring (1872 px) as a part. So does a side less than a rim from the edge, when the box
    alone does not go round, since nothing past the rim fits there: a 30 px disc 1 px off the left edge got
    82% of its area in ring.

    With the edge standing in for a side, one part can pass all the others by itself: skis wider than a
    skier cut off at the top pass both sides and the bottom, and cutting them at the slack band cost them
    43%, a gap under the boots. So there a ring has to lie along every free side but one, on ALONG of the
    line just past the rim beside it; and the side it does not lie along it passes no further than the
    slack band, as a ring held a few pixels off centre passes the side it is tight on only at its corners.
    Rows (or columns) across a side that hold something reaching twice the band out are left out of both
    tests there: a rod, a handle or a bag held beside the side is a part on the ring, and lies along the
    side only where it crosses it (a bag beside a skier lay on a fifth of the line beside its side, and the
    skis were cut). What passes a side beyond its corners further than twice the band runs along the next
    side, as the skis do, and is no ring. Away from the edge the box alone decides, as before."""
    box, reach = chosen.getbbox(), hanging.getbbox()
    if box is None or reach is None:
        return False
    left, top, right, bottom = box
    width, height = chosen.size
    if left and top and right < width and bottom < height and reach[0] < left and reach[1] < top and reach[2] > right and reach[3] > bottom:
        return True
    edges = (left <= rim, top <= rim, right >= width - rim, bottom >= height - rim)
    if not any(edges):
        return False
    line = _ring(chosen, rim + 1)
    band = rim + SLACK
    # What reaches twice the band out is no loose ring that is left out (those reach at most the band).
    far = ImageChops.subtract(hanging, grow_support(chosen, 2 * band))
    free = along = 0
    for side, edge in enumerate(edges):
        if edge:
            continue
        free += 1
        beside, *corners = _flanks(box, chosen.size, side)
        corners = [(hanging.crop(corner), corner) for corner in corners]
        if any(_passes(part, corner, box, side) > 2 * band for part, corner in corners):
            return False
        near = _apart(hanging, far, beside, side)
        if _on(near, line.crop(beside)) >= ALONG:
            along += 1
        elif not any(0 < _passes(part, region, box, side) <= band for part, region in [(near, beside), *corners]):
            return False
    return along >= free - 1


def _held(chosen: Image.Image, beyond: Image.Image, rim: int) -> list[Image.Image]:
    """The pieces of ``beyond`` (the answer past the rim) that hang on the selection, each on its own.
    Only an answer that goes round the object (see ``_around``) can be a loose ring, whole or broken into
    caps: then the pieces are judged as far as they lie past the slack band, where a handle or a string
    comes apart from the ring. Otherwise each piece is a part as it is: skis under a skier's boots run
    along the edge inside the band, and cutting them there left a gap under the boots. A free skier with
    skis wider than the body and a pole raised above it still goes round, and loses the skis under the boots."""
    hanging = pieces(beyond, touching=grow_support(chosen, rim + 1))
    together = _union(chosen.size, hanging)
    if _around(chosen, together, rim):
        return pieces(_past(chosen, together, rim))
    return hanging


def _enough(chosen: Image.Image, held: list[Image.Image], rim: int) -> Image.Image | None:
    """The pieces together when they are a part and not the edge of a matte, or None. At least LEAST
    of the selection is a part; less is one only in the pieces that reach OUT rims beyond it."""
    together = _union(chosen.size, held)
    if _count(together) >= LEAST * max(1, _count(chosen)):
        return together
    far = grow_support(chosen, OUT * rim)
    reaching = _union(chosen.size, [piece for piece in held if _count(ImageChops.subtract(piece, far))])
    return reaching if reaching.getbbox() else None


def complete(image: Image.Image, selection: Image.Image, ask, segment, described: str = "", cancelled=lambda: False) -> Image.Image | None:
    """``selection`` (L, canvas-sized) with the parts it left out, or None when it left none out.

    ``described`` is the artist's own description when the selection came from one; otherwise the
    model names the object. ``ask`` and ``segment`` are ``wevisone.questions``."""
    image = image.convert("RGB")
    frame = model_frame(image)
    chosen = _solid(selection if selection.size == frame.size else selection.resize(frame.size, Image.Resampling.BILINEAR))
    if chosen.getbbox() is None or _count(chosen) > cast.WHOLE * frame.width * frame.height:
        return None
    if described.strip():
        names = iter([described])
    else:
        names = (cast.named(ask(picture, cast.NAME_PROMPT)) for picture in cast.name_pictures(image, selection))
    tried: set[str] = set()
    for name in names:
        if cancelled():
            return None
        name = re.sub(r"^\s*(a|an|the)\s+", "", name.strip(), flags=re.IGNORECASE).strip(" .,:;")
        if not name or name.lower() in tried:
            continue
        tried.add(name.lower())
        mattes = segment(frame, QUESTION.format(name=name))
        about, beyond = parts(chosen, mattes)
        if not about:
            continue  # the crop's "main object" was something else: the object alone on grey is named next
        added = _kept(frame.size, chosen, mattes, beyond)
        if not cancelled():
            # A kite's string is not among "everything attached": it has to be asked for.
            try:
                strands = segment(frame, STRINGS.format(name=name))
            except GenerationCancelled:
                raise
            except Exception:  # noqa: BLE001 - the parts found so far stand
                if cancelled():
                    raise
                strands = []
            added = ImageChops.lighter(added, _kept(frame.size, chosen, strands, strand(chosen, strands)))
        if added.getbbox() is None:
            return None
        if added.size != selection.size:
            added = added.resize(selection.size, Image.Resampling.BILINEAR)
        return ImageChops.lighter(selection, added)
    return None


def _kept(size: tuple[int, int], chosen: Image.Image, mattes: list[Image.Image], beyond: Image.Image | None) -> Image.Image:
    """The model's answer where it is a part: the parts with their own soft edges and their roots, and
    nothing of the rest. ``beyond`` holds the parts past the slack band. Reaching back from there across
    the band to the selection brings a handle's or a string's root along, and of a loose ring only the
    bit beside that root.

    A part that leaves the object at a shallow angle runs inside the band for a while, and what of it
    lies further than the reach from where it leaves the band is lost: in a synthetic test, 2.7% of an
    oar at 20 degrees to the hull and 20% of one at 10 degrees (8% before the slack band). Inside the
    band nothing tells such an oar from a loose ring."""
    added = Image.new("L", size)
    if beyond is None:
        return added
    box = chosen.getbbox()
    rim = _rim(max(box[2] - box[0], box[3] - box[1]))
    # 2 rims for the soft edges, as before the slack band, and SLACK more, so it reaches back toward the
    # selection as far as before. With 2 rims, the boat's oars on record lost 1.5% of their area, not 0.6%.
    keep = grow_support(beyond, 2 * rim + SLACK)
    for matte in mattes:
        added = ImageChops.lighter(added, matte)
    return ImageChops.darker(added, keep)


def _reached(seed: Image.Image, within: Image.Image, block: int = 4) -> Image.Image:
    """The part of ``within`` that hangs together with ``seed``, grown from it step by step without
    leaving ``within``. Done on copies ``block`` times smaller in which every pixel is the brightest of
    its block, so a line one pixel wide survives and a gap of a few pixels does not break it."""
    def small(mask):
        width, height = -(-mask.width // block) * block, -(-mask.height // block) * block
        padded = Image.new("L", (width, height))
        padded.paste(mask, (0, 0))
        return padded.reduce(block).point(lambda value: 255 if value else 0)

    field = small(within)
    reached = ImageChops.darker(small(seed), field)
    count = _count(reached)
    while count:
        grown = ImageChops.darker(grow_support(reached, 2), field)
        now = _count(grown)
        if now == count:
            break
        reached, count = grown, now
    full = reached.resize((reached.width * block, reached.height * block), Image.Resampling.NEAREST)
    return full.crop((0, 0, within.width, within.height))


def strand(chosen: Image.Image, mattes: list[Image.Image]) -> Image.Image | None:
    """The string, rope or leash among the model's answers: what starts at the selection and runs on
    from it as a line (None when nothing does), as far as it lies past the slack band. ``chosen`` as
    for ``parts``."""
    box = chosen.getbbox()
    if box is None:
        return None
    extent = max(box[2] - box[0], box[3] - box[1])
    outside = ImageChops.subtract(grow_support(chosen, max(3, round(extent * NEAR))), chosen)
    total = Image.new("L", chosen.size)
    for matte in mattes:
        solid = _solid(matte, 96)
        if _count(ImageChops.darker(solid, outside)):
            total = ImageChops.lighter(total, solid)
    # Only what hangs together with the selection: the same answer can hold a speck on a bird far off, and
    # a line is measured by where it ends.
    total = ImageChops.darker(total, _reached(ImageChops.darker(total, outside), total))
    rim = _rim(extent)
    beyond = ImageChops.subtract(total, grow_support(chosen, rim))
    # Measured past the slack band, as for parts. The answer may hold the object loosely with its string;
    # the ring that leaves would pass for a line round the object, or count as part of the string's line,
    # and _kept would take it all round. Cut, not split into pieces, so a string broken by a gap stays one line.
    line = _past(chosen, beyond, rim)
    area, own = _count(line), _count(chosen)
    if not area or _count(beyond) > MOST * own:   # MOST as for parts, slack band included
        return None
    short = area < LEAST * own
    if short:
        # The rest of a string the selection already mostly holds is too little to be LONG: like a part
        # under LEAST, it has to hang on the selection and reach OUT rims beyond it.
        line = _enough(chosen, _held(chosen, beyond, rim), rim)
        if line is None:
            return None
    # A string runs off the object: it crosses the line just past the slack band where it leaves. An answer that
    # lies along that line, filled from the object out to it, is the object held loosely. An answer 9 to 12 px
    # looser round squares and discs of 100 to 400 px leaves a ring or a crescent there, long and thin enough to
    # pass for a line, and _kept took the whole loose ring with it (39% of a 100 px square, 31% of a disc held
    # 2 px off centre). Strings leaving on every side each cross the line once: a hull's bow and stern lines and
    # its stay lie on 1.4% of it, the kite's string, the balloon's, the leash and the cable on record 0 to 2%.
    # Telling them apart by the sides they pass and lie beside lost such strings, and missed a crescent, which
    # does not go round. Counting all of the answer on the line lost every cord that lies along the object a
    # few pixels off it before it runs away (a kettle's cord along its base, a leash along a lying dog, a strap
    # curled round a camera, a halyard beside its pole), and guy lines leaving a tent's apex 10 to 20 degrees off
    # its walls: each covers its width over the sine of its angle, and four together passed HUGS. Background lies
    # between such a cord and the object, and a guy line is that far out only well along it.
    if _on(_hugging(chosen, total, rim + SLACK + 1), _ring(chosen, rim + SLACK + 1)) >= HUGS:
        return None
    reach = line.getbbox()
    area, length = _count(line), math.hypot(reach[2] - reach[0], reach[3] - reach[1])
    if not short and length < LONG * extent:
        return None
    # A line covers its length. Specks strewn along a string picked alone (a bird beside it, a bit at its
    # end) span a long box and cover little of it.
    if area / length > THIN * extent or area / length < SOLID:
        return None
    return line
