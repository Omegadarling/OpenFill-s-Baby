# SPDX-License-Identifier: Apache-2.0
"""Get per-object alpha mattes from ComfyUI's built-in WeVisOne-VL node."""

from __future__ import annotations

import atexit
import hashlib
import io
import ipaddress
import json
import math
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageFilter

from openfill.backend import BackendUnavailable, GenerationCancelled
from openfill.comfy_api import ComfyApiError, ComfyClient, ComfyExecutionError
from openfill.sources import get_source

from .local_removal import grow_support

DEFAULT_QUERY = "all distinct visible foreground objects"
# A click that none of those objects is under. Measured 2026-11-25 on 77 objects, each clicked at its innermost
# point: the model's list held the object for 51, and for 23 it held nothing at the click (a swan alone on a lake,
# a dog on a beach, a man in a doorway among them). The same question about a close-up around the click found 12
# of the 26 that had failed, and asking what is at the point found 9 more: 72 of 77.
CLOSE_UPS = (0.4, 0.7)  # the close-up's side, as a share of the picture's longer side; the wider one when the first cuts the object off
POINT_QUESTION = "What is the object at the point <point><{x}><{y}></point>? Answer with a short noun phrase."
# A click on a small thing that lies on the picture's main subject: the list holds the subject, not the earring
# or the printed logo that was clicked. Measured 2026-11-25 (77 clicks): the
# list's matte under the click was 74% of the picture for the earring and 55% for the logo, 52% for a whole apple
# that was right; the next largest was a cat at 21%. Asked what is at the point, the model named the earring and
# the logo, 0.7% and 3.5% of the listed matte. Names that were only a piece of what was meant: a backpack 8% of a
# group of tourists, a charger 12% of a cable, shirts and coats 39 to 69% of a person.
SUBJECT = 0.3   # a listed matte this share of the picture or more is the main subject: ask what is at the click
SMALL = 1 / 16  # and take the named thing when it is at most this share of the subject, below the backpack's 8%
# A click this close to a matte, as a share of the matte's longer side, is on it: 3 pixels of the model's 1536-pixel
# frame, about one and a half pixels of the panel's preview. Simulated 2026-11-25 on the 9 thin objects among the 95
# stored mattes (the analyst's click_sim, through _distance): a click on one lands on its matte 96% of the time without
# this and 99.7% with it; one preview pixel off, 87% without and 98% with it.
# Only for a thin matte (see _thick): a click just outside a large object is on something else, maybe a leash or a
# cable the list left out, and the close-up looks for that (WeVisOne._under_click).
NEAR_CLICK = 3 / 1536
# A matte that holds a solid square this share of its longer side wide beside the click is a large object: 31 px of the
# 1536 px frame. Measured 2026-11-25 at frame size: the stored leash, cable, hose and lamp cord are 8 to 20 px wide at the
# hit level, soft edges included. A square as wide as the click's window (7 px) called them large, and one preview pixel
# off them 88% of clicks were found; with 31 px, 98% (99% with no such rule), while a click 2 px beside a 300 px dog or
# off its corner is still not on it.
THICK = 0.02
# Cleaning a matte (clean_matte). Measured 2026-11-25 on the 95 mattes as the model first returned them, at the
# model's frame size: 4 change materially, all four on record as bad selections (a cable's faint haze, 12 times its
# solid area over the laptop and the desk, goes; "the plate of cookies" gets its faint cookies; a watercolour
# sailboat and power lines lose haze 0.9 and 0.3 times their solid area). 84 stay practically as they are, and 7
# change a little: up to 4% of the solid area made solid (the faint space between a chair's rungs or a bicycle's
# spokes, away from the soft edges of their holes, see RIM) or up to 7% dropped as haze.
SURE = 128     # alpha at which the model is sure a pixel is the object; fainter pixels closed in by sure ones are the object too
FAINT = 64     # under a quarter strength and far from the sure part, a pixel is haze
HAZE = 0.012   # how far from the sure part haze is still the object's edge, as a share of the matte's longer side
WORK = 640     # holes in the sure part are looked for on a copy no larger than this
# Pixels of the matte (the model's frame) around a truly empty pixel that are the soft edge of a hole, not faint parts
# of the object. Measured 2026-11-25 on the stored first-returned mattes: of the faint pixels the firming raised to
# solid, 100% lay within 3 px of an empty pixel for the drone (the grass seen through its landing gear), one mug and
# the dachshund, 89-93% for the other mug, the woman in the hall mirror and the apples; of the cake's cookies, 4%.
# A softer edge goes on past that while it runs on up to the sure part (_hole_edges). Left at RIM alone, a ramp 6 px
# wide came back soft, then 3 px of solid, then faint again, and 13 of the stored mattes had such a band at a hole
# (a mug's handle, a dog on a beach, a napkin); following the ramp, 8, smaller (a chair, a bicycle, the dog). Followed
# through any faint pixel that rises from its neighbour, the faint cookies around a gap came back speckled with solid
# pixels, five times as many as before (3641 against 726 on the stored mattes); with the sure part's side too, 698.
RIM = 3
# A ramp rises, or falls, at least this much from one pixel to the next: the smallest step that is not the rounding
# of a flat area (whose neighbours differ by 0 or 1).
RISE = 2
# Seconds a courtesy question (a shadow, an attached part) waits for a server that missed its health check.
# Measured 2026-11-25: between two removals ComfyUI did not answer for about 25 s while it swapped models, and
# the three-second check gave the shadow up at once.
PATIENCE = 12.0
# Seconds a courtesy question waits for the engine's port to take a connection. A running engine, even one busy
# loading a model, takes it at once (the system queues it); one that is not running refuses it, which Windows
# reports only after about a second of retries. The same as openfill's own probe (LOOPBACK_PROBE_TIMEOUT).
KNOCK = 0.5
# The same for an engine on another computer, and how many times it is knocked on. Over Wi-Fi or a VPN one connection
# request can be lost, and the system sends it again only after its own retransmission timeout (1 s at the earliest,
# RFC 6298), so a single half-second knock called such an engine not running. 1.5 s lets a refusal come back as one
# (about a second, see KNOCK), and a refusal is believed at once; silence is knocked on once more with a fresh request.
# At most 3 s for a computer that is off, well inside the courtesy's PATIENCE.
FAR_KNOCK = 1.5
FAR_KNOCKS = 2
# What a refused connection says: Windows (WSAECONNREFUSED, 10061), Linux and macOS (ECONNREFUSED). Nothing listens at
# the engine's address any more; a wait that ran out, or a connection cut halfway, says something else.
REFUSED = ("winerror 10061", "econnrefused", "actively refused", "connection refused")
# Seconds within which a failed health check through an SSH tunnel was a refusal, not a wait: a refused or closed
# connection fails it in at most a KNOCK, while a busy engine makes it run out its 3 s (ComfyClient.is_alive).
QUICK = 1.5
# Seconds a tunnel opened for the question waits for the engine to answer before it is closed again
# (comfy_connection.ensure_connection). ssh that cannot reach the computer gives up sooner, after its ConnectTimeout of 8 s.
TUNNEL_WAIT = 15.0
MODEL = re.compile(r"wevisone[_-]vl[_-]([24])b[_-](bf16|int8_convrot)\.safetensors", re.I)


class NothingFound(ValueError):
    """The picture holds nothing the model matched at the click, in the box or for the description: the
    engine worked, there was just no such object there. Kept apart from the engine's own errors, so a
    caller can try another way."""


class EngineNotRunning(BackendUnavailable, ValueError):
    """Nothing serves at the engine's address: nothing takes a connection there or, through an SSH tunnel,
    the connection is refused or closed at once. It is not running, and no wait brings it back.
    Also a ValueError, as the local selection engine's "not running" always was."""


class EngineBusy(BackendUnavailable):
    """The engine takes connections but did not answer for ``PATIENCE`` seconds: it is running, busy elsewhere."""


def encoder(client):
    if "WeVisOneDetect" not in client.object_info("WeVisOneDetect"):
        raise ValueError(
            "This ComfyUI needs the WeVisOne-VL Detect node. Update ComfyUI or choose a compatible source."
        )
    choices = []
    for name in client.combo_options("CLIPLoader", "clip_name"):
        match = MODEL.fullmatch(name.replace("\\", "/").split("/")[-1])
        if match:
            choices.append((int(match[1]), match[2].lower() == "bf16", name))
    if not choices:
        raise ValueError("Install a Comfy-Org WeVisOne-VL encoder in ComfyUI/models/text_encoders, then try again.")
    return max(choices)[2]  # pass the loader's exact path back, including its separators


def workflow(name, source, query):
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": source}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": name, "type": "stable_diffusion"}},
        "3": {
            "class_type": "WeVisOneDetect",
            "inputs": {
                "clip": ["2", 0],
                "image": ["1", 0],
                "task": "segmentation",
                "query": query,
                "individual_masks": True,
                "max_length": 1024,
            },
        },
        "4": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
    }


def question_workflow(name, source, task, query):
    """One question about a picture: the model's answer in words (node 6) and, for a description, its mattes (node 5)."""
    graph = workflow(name, source, query)
    graph["3"]["inputs"]["task"] = task
    graph["6"] = {"class_type": "PreviewAny", "inputs": {"source": ["3", 3]}}
    if task == "freeform":
        del graph["4"], graph["5"]
    return graph


def questions(client, name, cancel=lambda: False, seconds=60.0):
    """``ask(picture, question)`` and ``segment(picture, description)`` on one server, for ``cast.find``.

    A picture is uploaded once however many questions are asked about it. ``seconds`` bounds all of
    them together: this is a courtesy to a removal, which goes ahead without it rather than wait
    behind somebody else's render."""
    if "PreviewAny" not in client.object_info("PreviewAny"):
        raise ValueError("This ComfyUI cannot return the selection model's answers in words.")
    uploaded: dict[str, str] = {}
    until = time.monotonic() + seconds

    def run(picture, task, query):
        if cancel():
            raise GenerationCancelled("Selection cancelled.")
        stream = io.BytesIO()
        picture.save(stream, "PNG", compress_level=1)
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest not in uploaded:
            uploaded[digest] = client.upload_image(f"{digest}.png", data, subfolder="openfill/selection")
        for attempt in (1, 2):
            prompt_id = client.queue_prompt(question_workflow(name, uploaded[digest], task, query))
            try:
                history = client.wait_for(prompt_id, 1, until, poll_interval=0.25, cancel=cancel)
                break
            except BaseException as err:
                if attempt == 1 and isinstance(err, ComfyExecutionError) and err.is_loader_exhausted:
                    client.clear_cache()  # the server can load pictures again; ask once more
                    continue
                try:
                    client.cancel_prompt(prompt_id)  # ours alone, and only while it still waits
                except Exception:  # noqa: BLE001 - the reason we stopped waiting is the one to report
                    pass
                raise
        outputs = history.get("outputs", {})
        mattes = []
        for item in outputs.get("5", {}).get("images", []):
            if item.get("type") != "temp":
                raise ValueError("The selection engine returned an unexpected preview.")
            with Image.open(io.BytesIO(client.view(item["filename"], item.get("subfolder", ""), "temp"))) as image:
                if image.size != picture.size:
                    raise ValueError("The selection mask does not match the image dimensions.")
                mattes.append(image.convert("L"))
        return "".join(str(part) for part in outputs.get("6", {}).get("text", [])), mattes

    def ask(picture, question):
        return run(picture, "freeform", question)[0]

    def segment(picture, description):
        return run(picture, "segmentation", description)[1]

    return ask, segment


def named(answer: str) -> str:
    """The model's short name for what it was asked about: the phrase it grounds first, else its first clause."""
    found = re.search(r"<ref>(.*?)</ref>", answer, re.DOTALL)
    phrase = found.group(1) if found else re.split(r"[.\n<]", answer)[0]
    phrase = re.sub(r"<[^>]*>", " ", phrase)
    phrase = re.sub(r"^\s*(a|an|the)\s+", "", phrase, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", phrase).strip(" .,:;")[:80]


def close_up(size, point, share=CLOSE_UPS[0]):
    """The square around a click, inside the picture, as a Pillow box."""
    width, height = size
    side = max(64, round(max(size) * share))
    left = min(max(0, round(point[0] * width - side / 2)), max(0, width - side))
    top = min(max(0, round(point[1] * height - side / 2)), max(0, height - side))
    return left, top, min(width, left + side), min(height, top + side)


def cut_off(matte, cut_edges):
    """True when a matte from a close-up runs into an edge where the picture goes on (left, top, right, bottom):
    the close-up shows only part of that object."""
    peak = matte.getextrema()[1]
    threshold = max(8, round(peak * 0.5))
    box = matte.point([0] * threshold + [255] * (256 - threshold)).getbbox()
    if box is None:
        return False
    touches = (box[0] == 0, box[1] == 0, box[2] == matte.width, box[3] == matte.height)
    return any(cut and touched for cut, touched in zip(cut_edges, touches, strict=True))


def choose_matte(masks, point):
    """Prefer the smallest object actually under the click; a description selects all matches. When no
    matte is exactly under the click, the nearest thin one within ``NEAR_CLICK`` is: a wire one pixel wide
    is hard to hit. A large object beside the click is not: the click was on something else."""
    best, area, near = None, None, None
    for mask in masks:
        peak = mask.getextrema()[1]
        if peak < 8:
            continue
        if point is None:
            best = mask if best is None else ImageChops.lighter(best, mask)
        else:
            xy = tuple(min(n - 1, int(p * n)) for p, n in zip(point, mask.size, strict=True))
            occupied = sum(mask.histogram()[max(8, round(peak * 0.5)) :])
            if mask.getpixel(xy) < max(8, round(peak * 0.25)):
                away = _distance(mask, xy, max(8, round(peak * 0.25)))
                if away is not None and (near is None or (away, occupied) < near[:2]):
                    near = away, occupied, mask
                continue
            if area is None or occupied < area:
                best, area = mask, occupied
    if best is None and near is not None:
        best = near[2]
    if best is None:
        if point is not None:
            raise NothingFound("No object found at that point. Click inside it or describe what to select.")
        raise NothingFound("No matching object found. Try a more specific description.")
    return clean_matte(best)


def _distance(mask, xy, level):
    """How far from ``xy`` the nearest pixel of ``mask`` at ``level`` or more lies, in pixels, or None when
    none lies within ``NEAR_CLICK``, or when the matte is thick there (``_thick``)."""
    reach = round(NEAR_CLICK * max(mask.size))
    if reach < 1:
        return None
    x, y = xy
    left, top = max(0, x - reach), max(0, y - reach)
    window = mask.crop((left, top, min(mask.width, x + reach + 1), min(mask.height, y + reach + 1)))
    found = [
        math.hypot(left + index % window.width - x, top + index // window.width - y)
        for index, value in enumerate(window.tobytes())
        if value >= level
    ]
    away = min((away for away in found if away <= reach), default=None)
    if away is None or _thick(mask, xy, level, reach):
        return None
    return away


def _thick(mask, xy, level, reach):
    """Whether ``mask`` beside ``xy`` is a large object rather than a thin one: whether it holds a solid square
    ``THICK`` of its longer side wide (never narrower than the click's window) whose edge passes within ``reach``
    of ``xy``, so whose middle lies within ``reach`` + half the side of it. A wire, a leash or a cable narrower
    than that, soft edges included, holds no such square."""
    side = max(2 * reach + 1, 2 * round(THICK * max(mask.size) / 2) + 1)   # odd, as MinFilter needs
    half = side // 2
    x, y = xy
    near, span = reach + half, reach + side   # every pixel within ``near`` has its whole square inside the crop
    left, top = max(0, x - span), max(0, y - span)
    crop = mask.crop((left, top, min(mask.width, x + span + 1), min(mask.height, y + span + 1)))
    # Black around it: MinFilter repeats the outermost pixels, which would make a strap along the picture's edge
    # look as wide as the square.
    padded = Image.new("L", (crop.width + 2 * half, crop.height + 2 * half))
    padded.paste(crop.point(lambda alpha: 255 if alpha >= level else 0), (half, half))
    solid = padded.filter(ImageFilter.MinFilter(side))
    middle = (half + max(0, x - near - left), half + max(0, y - near - top),
              half + min(crop.width, x + near + 1 - left), half + min(crop.height, y + near + 1 - top))
    return solid.crop(middle).getbbox() is not None


def clean_matte(mask):
    """The matte as a selection. Barely visible specks go, so they do not enlarge PhotoCraft's selection
    bounds, and an opaque core becomes fully editable; the soft edge stays.

    The model is sure of most of an object and unsure of some: "the plate of cookies" came back with the
    plate solid and the cookies faint, a cable with a faint haze over the desk around it. So faint pixels
    that the sure part closes in on every side become sure, and then faint pixels far from the sure part
    are dropped. In that order: the other way round the haze cut would drop the cookies too. Truly empty
    holes (between a chair's slats) stay empty, with their own soft edge (``_hole_edges``), and a matte with
    no sure part at all (glass) stays as it is."""
    mask = mask.point(lambda alpha: 0 if alpha < 6 else 255 if alpha >= 250 else alpha)
    sure = mask.point(lambda alpha: 255 if alpha >= SURE else 0)
    if sure.getbbox() is None:
        return mask
    longer = max(mask.size)
    block = max(1, math.ceil(longer / WORK))
    inside = _enclosed(_smaller(sure, block).point(lambda value: 255 if value >= 128 else 0))
    if inside.getbbox() is not None:
        inside = _larger(inside, block, mask.size)
        unsure = ImageChops.darker(ImageChops.darker(inside, ImageChops.invert(sure)), mask.point(lambda alpha: 255 if alpha else 0))
        # At the matte's own size: the soft edge of an empty hole is a few of its pixels wide, finer than the copy.
        unsure = ImageChops.darker(unsure, ImageChops.invert(_hole_edges(mask, unsure)))
        mask, sure = ImageChops.lighter(mask, unsure), ImageChops.lighter(sure, unsure)
    near = grow_support(_smaller(sure, block).point(lambda value: 255 if value else 0), max(2, round(HAZE * longer / block)))
    keep = ImageChops.lighter(_larger(near, block, mask.size), mask.point(lambda alpha: 255 if alpha >= FAINT else 0))
    return ImageChops.darker(mask, keep)


def _hole_edges(mask, unsure):
    """The soft edges of ``mask``'s truly empty pixels, where they meet ``unsure`` (white): the faint pixels within
    ``RIM`` of an empty pixel and, beyond that, a ramp that runs on from the hole up to the sure part, followed to
    its end or to ``HAZE`` of the matte's longer side. A ramp pixel rises from the hole and falls from the sure part
    along a path, by at least ``RISE`` at each step. Faint texture around a hole that never reaches the sure part
    (the cake's cookies around a gap between them) rises and falls at random: it is not an edge, and is made solid,
    rather than left faint here and there."""
    edges = Image.new("L", mask.size)
    box = unsure.getbbox()
    if box is None:
        return edges
    steps = max(0, round(HAZE * max(mask.size)) - RIM)
    # Only around the unsure pixels: a path longer than the edge can grow does not reach them. One pixel more for the
    # neighbours a step compares with, so they are the matte's own pixels, not ones a filter repeats at the crop's edge.
    margin = RIM + steps + 1
    region = (max(0, box[0] - margin), max(0, box[1] - margin), min(mask.width, box[2] + margin), min(mask.height, box[3] + margin))
    part = mask.crop(region)
    faint = part.point(lambda alpha: 255 if 0 < alpha < SURE else 0)
    rim = grow_support(part.point(lambda alpha: 255 if alpha == 0 else 0), RIM)
    up = _follow(part, faint, rim, steps, rising=True)
    down = _follow(part, faint, part.point(lambda alpha: 255 if alpha >= SURE else 0), RIM + steps, rising=False)
    edges.paste(ImageChops.lighter(rim, ImageChops.darker(up, down)), region[:2])
    return edges


def _follow(matte, faint, start, steps, rising):
    """``start`` (white) grown up to ``steps`` pixels into ``faint`` pixels, each at least ``RISE`` brighter (with
    ``rising``) or darker than a pixel of it beside them: where the matte keeps rising, or falling, away from it."""
    grown = start
    for _ in range(steps):
        if rising:   # the darkest grown pixel beside each pixel; white where there is none, so nothing is brighter
            beside = _around(ImageChops.lighter(ImageChops.darker(matte, grown), ImageChops.invert(grown)), lowest=True)
            step = ImageChops.subtract(matte, beside)
        else:        # the brightest grown pixel beside each pixel; black where there is none
            beside = _around(ImageChops.darker(matte, grown), lowest=False)
            step = ImageChops.subtract(beside, matte)
        more = ImageChops.lighter(grown, ImageChops.darker(faint, step.point(lambda change: 255 if change >= RISE else 0)))
        if ImageChops.difference(more, grown).getbbox() is None:
            break
        grown = more
    return grown


def _around(image, lowest):
    """The darkest (``lowest``) or brightest pixel of each pixel's 3 x 3 square, as MinFilter(3) and MaxFilter(3)
    give, from shifted copies: about a quarter of the time (7 against 26 ms on a 2048-pixel matte)."""
    pick, outside = (ImageChops.darker, 255) if lowest else (ImageChops.lighter, 0)
    result = image
    for offset in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        if offset == (0, -1):
            image = result   # up and down from what left and right gave: the whole square
        moved = Image.new("L", image.size, outside)
        moved.paste(image, offset)
        result = pick(result, moved)
    return result


def _smaller(mask, block):
    """``mask`` ``block`` times smaller, each pixel the mean of its block (the edge padded with black)."""
    if block == 1:
        return mask
    padded = Image.new("L", (-(-mask.width // block) * block, -(-mask.height // block) * block))
    padded.paste(mask, (0, 0))
    return padded.reduce(block)


def _larger(mask, block, size):
    """A mask from ``_smaller`` back at ``size``."""
    if block == 1:
        return mask
    return mask.resize((mask.width * block, mask.height * block), Image.Resampling.NEAREST).crop((0, 0, *size))


def _enclosed(sure):
    """The black patches of ``sure`` (black and white) that its white closes in on every side, in white.
    Black joins black at a corner too, so a gap of one pixel lets a patch out. Followed row by row through
    the black stretches, so the time goes with the number of stretches, not with the picture's size."""
    width, height = sure.size
    data = sure.tobytes()
    stretches, leader, outside = [], [], []

    def first(index):
        while leader[index] != index:
            leader[index] = leader[leader[index]]
            index = leader[index]
        return index

    above = []
    for y in range(height):
        here, behind = [], 0
        for found in re.finditer(rb"\x00+", data[y * width:(y + 1) * width]):
            start, end = found.span()
            index = len(stretches)
            stretches.append((y, start, end))
            leader.append(index)
            outside.append(y in (0, height - 1) or start == 0 or end == width)
            here.append(index)
            while behind < len(above) and stretches[above[behind]][2] < start:
                behind += 1
            for other in above[behind:]:
                if stretches[other][1] > end:
                    break
                one, two = first(other), first(index)
                if one != two:
                    leader[max(one, two)] = min(one, two)
                    outside[min(one, two)] = outside[one] or outside[two]
        above = here
    drawn = bytearray(width * height)
    for index, (y, start, end) in enumerate(stretches):
        if not outside[first(index)]:
            drawn[y * width + start:y * width + end] = b"\xff" * (end - start)
    return Image.frombytes("L", (width, height), bytes(drawn))


def box_pixels(size, box):
    """Map normalized drag endpoints to a nonempty Pillow box (exclusive right/bottom)."""
    x1, x2 = sorted((box[0], box[2]))
    y1, y2 = sorted((box[1], box[3]))
    return math.floor(x1 * size[0]), math.floor(y1 * size[1]), math.ceil(x2 * size[0]), math.ceil(y2 * size[1])


def choose_box_matte(masks, rect, cut_edges=(False, False, False, False)):
    """Match a box around an object to its whole matte, including holes and soft edges."""
    left, top, right, bottom = rect
    box_area = (right - left) * (bottom - top)
    best = None
    best_score = 0
    for mask in masks:
        peak = mask.getextrema()[1]
        if peak < 8:
            continue
        threshold = max(8, round(peak * 0.5))
        core = mask.point([0] * threshold + [255] * (256 - threshold))
        x1, y1, x2, y2 = core.getbbox()
        # A matte cut off by the temporary crop needs a second look on the full image.
        touches = (x1 == 0, y1 == 0, x2 == mask.width, y2 == mask.height)
        if any(cut and touched for cut, touched in zip(cut_edges, touches, strict=True)):
            continue
        area = core.histogram()[255]
        inside = core.crop(rect).histogram()[255]
        if inside < area * 0.5:
            continue
        # Favor a tightly enclosed instance over a larger subject behind it. Use the
        # silhouette's bounds for fit so thin wires and hollow handles are eligible.
        overlap = (min(right, x2) - max(left, x1)) * (min(bottom, y2) - max(top, y1))
        union = box_area + (x2 - x1) * (y2 - y1) - overlap
        score = (inside / area) * (overlap / union)
        if score > best_score:
            best, best_score = mask, score
    return clean_matte(best) if best is not None else None


def model_frame(image):
    """Limit full-frame inference without losing original pixels for a smaller box."""
    if max(image.size) <= 1536:
        return image
    frame = image.copy()
    frame.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
    return frame


def _loopback(host):
    """Whether ``host`` is this computer: localhost, 127.0.0.0/8 or ::1."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _taken(url):
    """Whether something takes a connection at ``url``'s host and port. True when that cannot be told. This
    computer is knocked on once (``KNOCK``); another computer longer, and again when it did not answer (``FAR_KNOCK``)."""
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return True
    if not parts.hostname:
        return True
    near = _loopback(parts.hostname)
    for _knock in range(1 if near else FAR_KNOCKS):
        try:
            with socket.create_connection((parts.hostname, port), timeout=KNOCK if near else FAR_KNOCK):
                return True
        except ConnectionRefusedError:
            return False   # the computer answered that nothing listens there: a second knock changes nothing
        except OSError:
            continue   # no answer in time: the request may have been lost on the way
    return False


def refused(err):
    """Whether ``err`` (or what caused it) is a refused connection: nothing listens at the engine's address, so it
    stopped after it was last seen running. A wait that ran out or was interrupted is not one."""
    seen = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        if isinstance(err, ConnectionRefusedError) or isinstance(getattr(err, "reason", None), ConnectionRefusedError):
            return True
        if any(sign in str(err).lower() for sign in REFUSED):
            return True
        err = err.__cause__   # only what it was raised from: not whatever happened to be handled at the time
    return False


def _starts(app):
    """Whether ``ensure_running`` starts the engine when it is not running, and waits minutes for it: this computer's
    own ComfyUI, managed by OpenFill. Every other engine (another computer, a tunnel, the local selection engine) is
    only asked whether it is there, which the courtesy does as well."""
    if getattr(app, "selection_helper", False):
        return False
    settings = getattr(app, "settings", None)
    return bool(getattr(settings, "autostart", False)) and not getattr(settings, "remote_fibo", False)


def _tunnelled(app):
    """Whether the engine is reached through an SSH tunnel (a saved server with an SSH alias, or an engine routed to
    one): its address on this computer is the tunnel's, so whether that takes a connection says nothing about
    the engine itself."""
    source = getattr(app.server, "source", None)   # an engine routed to Select (engine_routes.ExternalServer)
    alias = getattr(source, "ssh_alias", None)
    if source is None and getattr(app.settings, "remote_fibo", False):
        try:
            alias = get_source(app.settings.fibo_source, getattr(app.settings, "comfy_servers", ())).ssh_alias
        except (AttributeError, ValueError):   # no source named, or one this build does not know
            alias = None
    return isinstance(alias, str) and bool(alias)


def _running(app, progress, cancel):
    """The address of the engine, for a question asked as a courtesy: it never starts an engine or waits
    for one to start. Raises ``EngineNotRunning`` when nothing serves at the engine's address, and
    ``BackendUnavailable`` when something does but does not answer: a busy engine, worth asking again.
    An engine behind an SSH tunnel is asked as a selection asks it, which may open the tunnel."""
    try:
        url = app.server.url()
    except Exception:  # noqa: BLE001 - a server that cannot say where it is is asked to make sure, as before
        url = None
    if not isinstance(url, str):
        url = None
    if _tunnelled(app):
        return _through_tunnel(app, url, progress, cancel)
    if url is not None and not _taken(url):
        raise EngineNotRunning(f"No engine is running at {url}.")
    if _starts(app):
        # The one kind of server that ensure_running would start, and wait minutes for.
        if url is not None and app.server.is_healthy(url):
            app.server.touch()  # as ensure_running does: the engine is in use, its idle time starts again
            return url
        raise BackendUnavailable(f"ComfyUI is not answering at {url}.")
    try:
        return app.server.ensure_running(progress, cancel)
    except (BackendUnavailable, GenerationCancelled):
        raise
    except (RuntimeError, OSError) as err:
        # Something took the connection but the server could not use it: worth asking again, as a busy engine is.
        raise BackendUnavailable(str(err)) from err


def _through_tunnel(app, url, progress, cancel):
    """``_running`` for an engine behind an SSH tunnel. Asked through ``ensure_running`` as before, which opens
    the tunnel when it is closed (it never starts the engine), and a failure is told by how it failed. Refused
    or closed at once, or nothing left listening once the tunnel gave up early: the engine is not running there
    (``EngineNotRunning``). A health check that ran out its time: a busy engine, worth asking again. A tunnel
    opened for the question that stayed up its whole wait without an answer, then was closed: busy
    (``EngineBusy``), as nothing more can be told from here and the patience is spent anyway."""
    opened_here = url is not None and not _taken(url)   # closed before: a tunnel up during the wait was opened for it
    started = time.monotonic()
    try:
        return app.server.ensure_running(progress, cancel)
    except GenerationCancelled:
        raise
    except (BackendUnavailable, RuntimeError, OSError) as err:
        spent = time.monotonic() - started
        if spent < QUICK:
            raise EngineNotRunning(str(err)) from err
        if opened_here and spent >= TUNNEL_WAIT:
            # ssh stayed up and the engine stayed silent (or closed each connection: from here the two look the same);
            # the tunnel is gone again, so the knock below would call it not running.
            raise EngineBusy(str(err)) from err
        if url is not None and not _taken(url):
            raise EngineNotRunning(str(err)) from err
        raise BackendUnavailable(str(err)) from err


class WeVisOne:
    """Use the configured edit server, or an optional dedicated loopback GPU for selection."""

    def __init__(self, profile):
        self.config = Path(profile) / "selection-engine.json"
        self.process = None
        self.cache_key, self.cached_masks = None, ()
        self.parts_missed = False

    def _stop_owned(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _client(self, app, progress, cancel, *, start=True):
        """The engine Select uses. With ``start`` False it is used only while it is already running (see
        ``_running``); a busy one raises ``BackendUnavailable``, one that is not running ``EngineNotRunning``."""
        if not getattr(app, "selection_helper", False):
            if app.settings.backend == "a1111":
                raise ValueError("Choose a ComfyUI engine to select objects with WeVisOne-VL.")
            url = app.server.ensure_running(progress, cancel) if start else _running(app, progress, cancel)
            return ComfyClient(url, timeout=30)
        # This local, optional profile is written by the installer, never by the web API.
        cfg = json.loads(self.config.read_text(encoding="utf-8-sig"))
        port = int(cfg["port"])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid local selection engine port.")
        client = ComfyClient(f"http://127.0.0.1:{port}", timeout=30)
        if client.is_alive():
            return client
        if not start:
            if _taken(client.base_url):
                raise BackendUnavailable("The local selection engine is not answering.")
            raise EngineNotRunning("The local selection engine is not running.")
        root, data, python = (Path(cfg[key]).resolve(strict=True) for key in ("comfy_dir", "data_dir", "python"))
        progress(0.05, "Starting the local selection engine…")
        if self.process is None or self.process.poll() is not None:
            args = [
                str(python),
                "-u",
                str(root / "main.py"),
                "--listen",
                "127.0.0.1",
                "--port",
                str(port),
                "--base-directory",
                str(data),
                "--disable-all-custom-nodes",
                "--offline",
                "--disable-auto-launch",
            ]
            if "cuda_device" in cfg:
                args.extend(("--cuda-device", str(int(cfg["cuda_device"]))))
            with (self.config.parent / "selection-engine.log").open("ab") as log:
                self.process = subprocess.Popen(
                    args,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            atexit.unregister(self._stop_owned)
            atexit.register(self._stop_owned)
        until = time.monotonic() + 120
        while self.process.poll() is None and time.monotonic() < until:
            if cancel():
                raise GenerationCancelled("Selection cancelled.")
            if client.is_alive(timeout=1):
                return client
            time.sleep(0.25)
        raise RuntimeError(
            "The local selection engine could not start. See selection-engine.log in the OpenFill profile."
        )

    def _courtesy_client(self, app, cancel, seconds, *, start=False, progress=None):
        """The server for questions asked as a courtesy, and the seconds left for them. Only a server that
        is already running is used: nothing is started for these questions (an SSH tunnel to one may be
        opened, as for a selection), and an engine that is not
        running is given up at once (``EngineNotRunning``). One that takes connections but misses its
        health check is loading a model; it is asked again for a few seconds, then given up as busy
        (``EngineBusy``).

        With ``start``, the caller is about to start this same engine anyway (a removal on it): one that
        ``ensure_running`` would start (``_starts``) is started now, with its usual wait and ``progress``,
        and the questions get their whole ``seconds`` once it answers. One it cannot start is not running."""
        def quiet(_fraction, _text):
            return None

        started = time.monotonic()
        while True:
            try:
                client = self._client(app, quiet, cancel, start=False)
                return client, max(1.0, seconds - (time.monotonic() - started))
            except EngineNotRunning:
                if not (start and _starts(app)):
                    raise
                try:
                    return self._client(app, progress or quiet, cancel, start=True), seconds
                except GenerationCancelled:
                    raise
                except (BackendUnavailable, RuntimeError, OSError) as failed:
                    # Not started (no ComfyUI folder, its port taken by another program): as far as the
                    # look-up can tell, still not running.
                    raise EngineNotRunning(str(failed)) from failed
            except BackendUnavailable as err:
                if cancel():
                    raise
                if time.monotonic() - started >= min(PATIENCE, seconds):
                    raise EngineBusy(str(err)) from err
                time.sleep(1.0)

    def questions(self, app, cancel=lambda: False, seconds=60.0, *, start=False, progress=None):
        """``ask`` and ``segment`` on the engine Select uses, for ``cast.find``. ``start``: the caller is about to
        start this engine anyway, so it may be started for them (see ``_courtesy_client``)."""
        client, left = self._courtesy_client(app, cancel, seconds, start=start, progress=progress)
        return questions(client, encoder(client), cancel, left)

    def whole(self, app, source, matte, described, cancel):
        """``matte`` with the parts of the object it left out (see ``whole``), or ``matte`` itself.
        A courtesy after a selection that already succeeded: whatever goes wrong, the selection stands,
        and why is written to the log. ``parts_missed`` then says whether the engine was too busy to look,
        which the artist cannot tell from an object that has no attached parts."""
        from . import whole

        self.parts_missed = False
        try:
            client, left = self._courtesy_client(app, cancel, whole.SECONDS)
            ask, segment = questions(client, encoder(client), cancel, left)
            return whole.complete(source, matte, ask, segment, described, cancel) or matte
        except GenerationCancelled:
            raise
        except Exception as err:  # noqa: BLE001 - the object is selected; its parts are a bonus
            if cancel():
                raise
            # Busy: it kept missing its health check, or the questions waited out their time behind other work.
            # Not one that refused the questions: it stopped after its health check.
            self.parts_missed = isinstance(err, EngineBusy) or (
                type(err) is ComfyApiError and not err.status and not refused(err)
            )
            app.log.info("parts: skipped (%s)", err)
            return matte

    def mask(self, app, source, query, point, progress, cancel, *, box=None):
        client = self._client(app, progress, cancel)
        name = encoder(client)
        if box is None:
            frame = model_frame(source)
            masks = self._instances(client, name, frame, query, progress, cancel)
            try:
                listed = choose_matte(masks, point)
            except ValueError:
                if point is None or query != DEFAULT_QUERY:
                    raise
                found = self._under_click(client, name, source, point, progress, cancel)
                if found is None:
                    raise
                return found
            if point is None or query != DEFAULT_QUERY:
                return listed
            return self._on_it(client, name, frame, point, listed, progress, cancel)

        # Keep surrounding context. For a small item this crop uses the original document
        # pixels, before the full image would be reduced to the model's input limit.
        width, height = source.size
        left, top, right, bottom = box_pixels(source.size, box)
        pad = max(16, math.ceil(max(right - left, bottom - top) / 2))
        x1, y1, x2, y2 = max(0, left - pad), max(0, top - pad), min(width, right + pad), min(height, bottom + pad)
        crop_size = x2 - x1, y2 - y1
        if crop_size[0] * crop_size[1] < 0.75 * width * height:
            frame = model_frame(source.crop((x1, y1, x2, y2)))
            crop_box = (
                (left - x1) / crop_size[0],
                (top - y1) / crop_size[1],
                (right - x1) / crop_size[0],
                (bottom - y1) / crop_size[1],
            )
            masks = self._instances(client, name, frame, query, progress, cancel, cache=False)
            matte = choose_box_matte(masks, box_pixels(frame.size, crop_box), (x1 > 0, y1 > 0, x2 < width, y2 < height))
            if matte is not None:
                if matte.size != crop_size:
                    matte = matte.resize(crop_size, Image.Resampling.BILINEAR)
                result = Image.new("L", source.size)
                result.paste(matte, (x1, y1))
                return result
            progress(0.1, "Checking the full image for the boxed object…")

        frame = model_frame(source)
        masks = self._instances(client, name, frame, query, progress, cancel)
        matte = choose_box_matte(masks, box_pixels(frame.size, box))
        if matte is None:
            raise NothingFound("No object found in that box. Draw a box around the whole object or describe it.")
        return matte

    def _on_it(self, client, name, frame, point, listed, progress, cancel):
        """The thing at a click when the listed object under it is the picture's main subject: a small
        thing on it (an earring, a printed logo) that the model names when asked what is at the point.
        Otherwise, and when there is no answer, the listed object."""
        area = sum(listed.histogram()[128:])
        if area < SUBJECT * listed.width * listed.height:
            return listed
        progress(0.5, "Asking what is at that point…")
        try:
            ask, segment = questions(client, name, cancel, 30.0)
            label = named(ask(frame, POINT_QUESTION.format(x=round(point[0] * 1000), y=round(point[1] * 1000))))
            if label:
                asked = choose_matte(segment(frame, f"the {label}"), point)
                # Much smaller than the subject: a thing on it. Larger: a piece of it (a shirt, of a person).
                if 0 < sum(asked.histogram()[128:]) <= SMALL * area:
                    return asked
        except GenerationCancelled:
            raise
        except Exception:  # noqa: BLE001 - no answer, or no match: the listed object stands
            if cancel():
                raise
        return listed

    def _under_click(self, client, name, source, point, progress, cancel):
        """The object at a click that none of the picture's listed objects is under, or None.
        First the same question about a close-up around the click, which finds whole objects the
        full picture's list left out, and about a wider one when the first cuts the object off.
        Then the model is asked what is at the point, and that is selected, unless it is only a
        piece of what a close-up showed (a shirt, of a man the close-up cut at the knees)."""
        width, height = source.size
        part = None
        for share in CLOSE_UPS:
            x1, y1, x2, y2 = close_up(source.size, point, share)
            if (x2 - x1) * (y2 - y1) >= 0.75 * width * height:
                break
            progress(0.3, "Looking closer at that point…")
            frame = model_frame(source.crop((x1, y1, x2, y2)))
            local = ((point[0] * width - x1) / (x2 - x1), (point[1] * height - y1) / (y2 - y1))
            try:
                matte = choose_matte(self._instances(client, name, frame, DEFAULT_QUERY, progress, cancel, cache=False), local)
            except ValueError:
                if part is None:
                    break  # nothing there at this scale: a wider look will not show more of it
                continue
            found = Image.new("L", source.size)
            found.paste(matte if matte.size == (x2 - x1, y2 - y1) else matte.resize((x2 - x1, y2 - y1), Image.Resampling.BILINEAR), (x1, y1))
            if not cut_off(matte, (x1 > 0, y1 > 0, x2 < width, y2 < height)):
                return found
            part = found
        progress(0.5, "Asking what is at that point…")
        try:
            ask, segment = questions(client, name, cancel, 30.0)
            frame = model_frame(source)
            label = named(ask(frame, POINT_QUESTION.format(x=round(point[0] * 1000), y=round(point[1] * 1000))))
            if label:
                asked = choose_matte(segment(frame, f"the {label}"), point)
                if part is None:
                    return asked
                whole = asked if asked.size == source.size else asked.resize(source.size, Image.Resampling.BILINEAR)
                # The larger of the two is the object: a name can be a piece of it, a close-up can show a piece of it.
                return whole if sum(whole.histogram()[128:]) >= sum(part.histogram()[128:]) else part
        except GenerationCancelled:
            raise
        except Exception:  # noqa: BLE001 - no answer, or no match: the close-up's part of the object is better than nothing
            if cancel():
                raise
        return part

    def _instances(self, client, name, source, query, progress, cancel, *, cache=True):
        if cancel():
            raise GenerationCancelled("Selection cancelled.")
        stream = io.BytesIO()
        source.save(stream, "PNG", compress_level=1)
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        key = (client.base_url, name, digest, query)
        if key == self.cache_key:
            return self.cached_masks
        path = client.upload_image(f"{digest}.png", data, subfolder="openfill/selection")
        for attempt in (1, 2):
            prompt_id = client.queue_prompt(workflow(name, path, query))
            try:
                history = client.wait_for(
                    prompt_id,
                    1,
                    time.monotonic() + 360,
                    progress=progress,
                    cancel=cancel,
                    wait_for_queue=True,
                    shared_queue=True,
                )
                break
            except ComfyExecutionError as err:
                if attempt == 2 or not err.is_loader_exhausted:
                    raise
                client.clear_cache()  # the server can load pictures again; ask once more
        outputs = history.get("outputs", {}).get("5", {}).get("images", [])
        masks = []
        for item in outputs:
            if cancel():
                raise GenerationCancelled("Selection cancelled.")
            if item.get("type") != "temp":
                raise ValueError("The selection engine returned an unexpected preview.")
            raw = client.view(item["filename"], item.get("subfolder", ""), "temp")
            with Image.open(io.BytesIO(raw)) as image:
                if image.size != source.size:
                    raise ValueError("The selection mask does not match the image dimensions.")
                masks.append(image.convert("L"))
        # Bound retained masks. Repeated clicks on the same composite normally need no GPU pass.
        if cache and source.width * source.height * len(masks) <= 48 * 1024 * 1024:
            self.cache_key, self.cached_masks = key, masks
        return masks
