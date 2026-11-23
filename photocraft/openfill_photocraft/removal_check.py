# SPDX-License-Identifier: Apache-2.0
"""Did a removal remove the object? Checked on a staged result before the artist sees it."""

from __future__ import annotations

from PIL import Image, ImageChops

from openfill import quality
from openfill.geometry import Rect, plan_native_removal

from .local_removal import removal_mask

SIDE = 192  # the longer side of the context the check looks at


def composite(visible: Image.Image, selection: Image.Image | None, layer) -> Image.Image:
    """The canvas as PhotoCraft will show it with the staged ``layer`` on top."""
    origin = layer.origin
    size = (origin.width, origin.height)
    box = (origin.x, origin.y, origin.x + origin.width, origin.y + origin.height)
    pixels = Image.frombytes("RGBA", size, layer.rgba)
    if layer.mask_kind == "pixels" and layer.mask is not None:
        mask = Image.frombytes("L", size, layer.mask)
    elif layer.mask_kind == "selection" and selection is not None:
        mask = selection.crop(box)
    else:
        mask = Image.new("L", size, 255)
    result = visible.convert("RGB")
    region = Image.composite(pixels.convert("RGB"), result.crop(box), ImageChops.multiply(mask, pixels.getchannel("A")))
    result.paste(region, box[:2])
    return result


def persistence(before: Image.Image, after: Image.Image, selection: Image.Image) -> float | None:
    """``quality.edge_persistence`` for whole pictures: the removal's own context, reduced."""
    box = selection.getbbox()
    if box is None:
        return None
    ctx = plan_native_removal(Rect(box[0], box[1], box[2] - box[0], box[3] - box[1]), Rect(0, 0, *before.size)).context
    crop = (ctx.x, ctx.y, ctx.right, ctx.bottom)
    scale = min(1.0, SIDE / max(ctx.width, ctx.height))
    size = (max(8, round(ctx.width * scale)), max(8, round(ctx.height * scale)))
    small = [image.crop(crop).convert("RGB").resize(size, Image.Resampling.BOX) for image in (before, after)]
    mask = removal_mask(selection.crop(crop)).resize(size, Image.Resampling.BOX).tobytes()
    distance = quality.signed_distance(*size, mask)
    return quality.edge_persistence(
        quality.Buffer(*size, small[0].tobytes(), 3), quality.Buffer(*size, small[1].tobytes(), 3), distance
    )


def object_remains(visible: Image.Image, selection: Image.Image | None, layer) -> bool:
    """True when the staged removal ``layer`` leaves the selected object's edges in place."""
    if selection is None:
        return False
    value = persistence(visible.convert("RGB"), composite(visible, selection, layer), selection)
    return value is not None and value > quality.STILL_THERE
