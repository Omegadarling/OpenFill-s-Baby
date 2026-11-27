# SPDX-License-Identifier: Apache-2.0
"""The pieces of a mask that each hang together, found with Pillow alone.

The selection model can put several things into one matte: three bottles that look alike, or a rider
and his image in a puddle. Each of them is a piece of its own, and a rule about one object has to
look at them one at a time.

A piece is followed row by row through its white stretches, so the time it takes goes with the number
of stretches and not with how far a piece winds. Growing a piece outwards from one pixel with a
filter, which is how ``whole._reached`` follows a string, took 0.6 s for a matte that fills a
1536 x 1024 picture and 17 s for a line winding across it; this takes about 10 ms for either.
"""

from __future__ import annotations

from PIL import Image, ImageChops

BLOCK = 4  # pieces are told apart on a copy this many times smaller, so a gap narrower than about this joins them
MOST = 24  # a matte in more pieces than this is noise: only its largest ones are worth a look


def _coarse(mask: Image.Image, block: int) -> Image.Image:
    """``mask`` ``block`` times smaller, white wherever its block holds anything, so nothing of it is lost."""
    width, height = -(-mask.width // block) * block, -(-mask.height // block) * block
    padded = Image.new("L", (width, height))
    padded.paste(mask, (0, 0))
    return padded.reduce(block).point(lambda value: 255 if value else 0)


def _stretches(row: bytes) -> list[tuple[int, int]]:
    """The white stretches of one row of a black-and-white mask, as (first pixel, pixel after the last)."""
    found, start = [], row.find(b"\xff")
    while start >= 0:
        end = row.find(b"\x00", start)
        if end < 0:
            end = len(row)
        found.append((start, end))
        start = row.find(b"\xff", end)
    return found


def _groups(mask: Image.Image) -> list[list[tuple[int, int, int]]]:
    """The stretches (row, first pixel, pixel after the last) of a black-and-white mask, grouped into
    the pieces they form. Two stretches belong together when they touch, a corner included."""
    width, height = mask.size
    data = mask.tobytes()
    stretches: list[tuple[int, int, int]] = []
    leader: list[int] = []   # for each stretch an earlier stretch of its piece, or itself

    def first(index: int) -> int:
        while leader[index] != index:
            leader[index] = leader[leader[index]]
            index = leader[index]
        return index

    above: list[int] = []
    for y in range(height):
        here: list[int] = []
        behind = 0   # the stretches of the row above before this one end left of every stretch still to come
        for start, end in _stretches(data[y * width:(y + 1) * width]):
            index = len(stretches)
            stretches.append((y, start, end))
            leader.append(index)
            here.append(index)
            while behind < len(above) and stretches[above[behind]][2] < start:
                behind += 1
            for other in above[behind:]:
                if stretches[other][1] > end:
                    break
                one, two = first(other), first(index)
                leader[max(one, two)] = min(one, two)
        above = here
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for index, stretch in enumerate(stretches):
        groups.setdefault(first(index), []).append(stretch)
    return list(groups.values())


def pieces(mask: Image.Image, touching: Image.Image | None = None, block: int = BLOCK, most: int = MOST) -> list[Image.Image]:
    """The connected pieces of ``mask`` (black and white), each as a mask of its own at the same size,
    the largest first. With ``touching`` (a mask of that size) only the pieces that reach it.

    At most ``most`` pieces are returned: the smaller ones of a matte in more pieces than that are left out."""
    coarse = _coarse(mask, block)
    width = coarse.width
    groups = _groups(coarse)
    if touching is not None:
        near = _coarse(touching, block).tobytes()
        groups = [group for group in groups if any(near.find(b"\xff", y * width + start, y * width + end) >= 0 for y, start, end in group)]
    groups.sort(key=lambda group: -sum(end - start for _y, start, end in group))
    found = []
    for group in groups[:most]:
        drawn = bytearray(width * coarse.height)
        for y, start, end in group:
            drawn[y * width + start:y * width + end] = b"\xff" * (end - start)
        piece = Image.frombytes("L", coarse.size, bytes(drawn))
        full = piece.resize((piece.width * block, piece.height * block), Image.Resampling.NEAREST)
        found.append(ImageChops.darker(mask, full.crop((0, 0, mask.width, mask.height))))
    return found
