# SPDX-License-Identifier: Apache-2.0
"""Automatic removal as a short cascade: two repairs from the methods that rated best.

No single method wins on every picture, and no cheap measurement picks the better of two repairs
reliably, so Automatic shows two and the artist chooses. Which two depends on how much of the picture
is selected (``removal_table``). A repair that leaves the object in place is replaced by the next method.
"""

from __future__ import annotations

from openfill.remote_models import (
    DEV_ID,
    FLUX3_KLEIN_ID,
    H3_IMAGE_ID,
    LAMA_ID,
    MOSS_ANYPAINT_ID,
    QWEN21_LIGHTNING_ID,
    ROREM_ID,
)

from . import removal_table
from .removal_advice import TEXTURE_REMOVAL

AUTO_REMOVAL = "openfill-auto"  # the Remove value a panel sends for Automatic when a plan exists
RESULTS = 2
FAMILIES = {
    "rorem": (ROREM_ID, "rorem-sdxl"),
    "lama": (LAMA_ID, "lama-big"),
    "qwen": (QWEN21_LIGHTNING_ID,),
    "flux3": (FLUX3_KLEIN_ID,),
    "local": (TEXTURE_REMOVAL,),
}
# Names short enough to sit under a thumbnail.
SHORT = {
    TEXTURE_REMOVAL: "Local",
    ROREM_ID: "RORem",
    "rorem-sdxl": "RORem",
    LAMA_ID: "LaMa",
    "lama-big": "LaMa",
    "mat-places512": "MAT",
    QWEN21_LIGHTNING_ID: "Qwen",
    DEV_ID: "FIBO",
    H3_IMAGE_ID: "H3",
    MOSS_ANYPAINT_ID: "AnyPaint",
    FLUX3_KLEIN_ID: "FLUX 3",
}
TIP = (
    "Automatic makes two repairs with the methods that rated best for a selection this size, and a third with "
    "Local where the surroundings are plain. Keep the best one."
)


# What every engine removal runs with, whatever is saved. The ratings behind the table were made with the seam
# and edge correction and the grain match on, and the panels have no switch for either; an installer of 2026-10 had
# saved both off, so the installed app was removing without them (found 2026-11-25).
SETTINGS = {"seam_correction": True, "grain_match": True}


def short_label(method: str, name: str = "") -> str:
    return SHORT.get(method) or name or method


def _resolve(family: str, available) -> str | None:
    return next((method for method in FAMILIES.get(family, ()) if method in available), None)


def steps(share: float, available) -> list[tuple[str, int]]:
    """Installed methods to try in order for a selection covering ``share`` (0..1) of the picture,
    each with the number of its variations that count as results."""
    order = next((order for below, order in removal_table.SIZES if share < below), removal_table.SIZES[-1][1])
    found = []
    for family, variations in order:
        method = _resolve(family, available)
        if method is not None and all(method != seen for seen, _count in found):
            found.append((method, max(1, int(variations))))
    return found


def first_method(available) -> str | None:
    """The method Automatic starts with on this engine, or None when the rated methods installed
    here cannot make ``RESULTS`` repairs for every selection size (then the older advice applies)."""
    shares = [0.0, *(below for below, _order in removal_table.SIZES[:-1])]
    if any(sum(count for _method, count in steps(share, available)) < RESULTS for share in shares):
        return None
    middle = shares[len(shares) // 2]
    return steps(middle, available)[0][0]
