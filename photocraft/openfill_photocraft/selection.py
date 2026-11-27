# SPDX-License-Identifier: Apache-2.0
"""Snapshot the artist's canvas, then apply one native, undoable selection change."""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageChops

from openfill.supervisor import read_json, write_json

from .bridge import PCImage
from .selection_advice import document_key
from .wevisone import DEFAULT_QUERY, NothingFound, WeVisOne

STALE = "The document or selection changed. Use the updated preview and try again."
OPERATIONS = {"replace": "new", "add": "add", "subtract": "subtract"}
DEFAULT_GROW_PIXELS = 4
MAX_GROW_PIXELS = 500
# These editor history commands only change the selection. Unknown steps, Undo,
# rewritten history and missed revisions take a fresh flat export.
MASK_ONLY_EDITS = {
    "Quick Selection",
    "Object Selection",
    "Magic Wand",
    "Lasso",
    "Polygonal Lasso",
    "Magnetic Lasso",
    "Rectangular Marquee",
    "Elliptical Marquee",
    "Single Row Marquee",
    "Single Column Marquee",
    "Load Selection",
    "Load Transparency",
    "Add to Selection",
    "Subtract from Selection",
    "Intersect with Selection",
    "Select All",
    "Deselect",
    "Inverse",
    "Reselect",
    "Select Subject",
    "Select Sky",
    "Focus Area",
    "Expand Selection",
    "Contract Selection",
    "Border Selection",
    "Smooth Selection",
    "Feather Selection",
    "Grow",
    "Similar",
    "Color Range",
    "Transform Selection",
}


def valid_grow_pixels(value):
    return type(value) is int and 1 <= value <= MAX_GROW_PIXELS


def saved_grow_pixels(value):
    return value if valid_grow_pixels(value) else DEFAULT_GROW_PIXELS


def key_for(index, doc):
    return document_key(
        index,
        doc.get("name"),
        doc.get("revision"),
        doc.get("width"),
        doc.get("height"),
        doc.get("selectionBounds") if doc.get("hasSelection") else None,
        doc.get("activeLayer"),
    )


def active(client, expected):
    view = client.call("ui.inspect")
    doc = view.get("document") or {}
    if not doc:
        raise ValueError("Open an image in PhotoCraft first.")
    index = (view.get("session") or {}).get("active")
    if not expected or key_for(index, doc) != expected:
        raise ValueError(STALE)
    return index, doc


class Selection:
    def __init__(self, bridge):
        self.bridge = bridge
        self.model = WeVisOne(bridge.profile)
        self.key, self.source, self.preview = "", None, None
        self.matte_key, self.matte = "", None
        self.document, self.revision, self.history = None, None, []
        self._settings_path = Path(bridge.profile) / "selection-settings.json"
        stored = read_json(self._settings_path)
        self.grow_pixels = saved_grow_pixels(stored.get("grow_pixels") if isinstance(stored, dict) else None)

    def capture(self, key, *, show_mask=False):
        client = self.bridge.client
        index, doc = active(client, key)
        has_mask = bool(doc.get("hasSelection"))
        if self.key == key and (not show_mask or self.preview["maskShown"] or not has_mask):
            return self.preview

        document = (index, doc.get("path"), doc["name"], doc["width"], doc["height"], doc.get("activeLayer"))
        history = doc.get("history") or []
        steps = history[len(self.history) :]
        revision = doc.get("revision")
        same_pixels = (
            document == self.document
            and self.source is not None
            and self.revision is not None
            and revision == self.revision + len(steps)
            and history[: len(self.history)] == self.history
            and (key == self.key or bool(steps))
            and all(step in MASK_ONLY_EDITS for step in steps)
        )
        mask = self.matte if self.matte_key == key and has_mask else None
        if show_mask and has_mask and mask is None:
            # Older PhotoCraft versions expose the exact selection through a scratch
            # document. Only the artist's explicit overlay request takes this path.
            full, mask = self.bridge._snapshot(PCImage(self.bridge, doc["name"], index))
        elif not same_pixels:
            # A flat PNG export is a copy. It leaves the active tab, file and history
            # in place, even while the artist is refining the selection in PhotoCraft.
            path = self.bridge._file("preview")
            try:
                client.call("app.save", path=path)
                full = self.bridge._read(path).convert("RGBA")
            finally:
                self.bridge._forget(path)
        if same_pixels:
            source = self.source
        else:
            source = Image.new("RGB", full.size, (112, 112, 112))
            source.paste(full, mask=full.getchannel("A"))
        thumb = source.copy()
        thumb.thumbnail((720, 500), Image.Resampling.LANCZOS)
        if mask is not None:
            overlay = mask.resize(thumb.size, Image.Resampling.LANCZOS).point(lambda a: round(a * 0.48))
            thumb.paste((55, 175, 255), (0, 0, *thumb.size), overlay)
        stream = io.BytesIO()
        thumb.save(stream, "JPEG", quality=90)
        preview = {
            "key": key,
            "width": doc["width"],
            "height": doc["height"],
            "maskShown": mask is not None,
            "image": base64.b64encode(stream.getvalue()).decode("ascii"),
        }
        active(client, key)
        self.document, self.revision, self.history = document, revision, history
        self.matte_key, self.matte = key, mask
        self.key, self.source, self.preview = key, source, preview
        return preview

    def validate(self, params):
        action = params.get("action")
        _, doc = active(self.bridge.client, params.get("key"))
        if action == "grow":
            if not doc.get("hasSelection"):
                raise ValueError("Make a selection before growing it.")
            pixels = params.get("pixels")
            if not valid_grow_pixels(pixels):
                raise ValueError(f"Choose a whole number from 1 to {MAX_GROW_PIXELS} pixels.")
            return
        if action not in ("click", "box", "describe") or params.get("operation", "replace") not in OPERATIONS:
            raise ValueError("Choose a valid selection action.")
        if type(params.get("whole", True)) is not bool:
            raise ValueError("Attached parts must be on or off.")
        if self.key != params.get("key") or self.source is None:
            raise ValueError(STALE)
        query = params.get("query", "")
        if not isinstance(query, str) or len(query) > 500 or (action == "describe" and not query.strip()):
            raise ValueError("Describe what to select in 1–500 characters.")
        if action == "click":
            point = params.get("point")
            if (
                not isinstance(point, (list, tuple))
                or len(point) != 2
                or not all(type(p) in (int, float) and math.isfinite(p) and 0 <= p < 1 for p in point)
            ):
                raise ValueError("Click inside the image preview.")
        if action == "box":
            box = params.get("box")
            if (
                not isinstance(box, (list, tuple))
                or len(box) != 4
                or not all(type(p) in (int, float) and 0 <= p <= 1 for p in box)
                or box[0] == box[2]
                or box[1] == box[3]
            ):
                raise ValueError("Drag a box around the object in the image preview.")
        if params.get("operation") == "subtract" and not doc.get("hasSelection"):
            raise ValueError("Make a selection before subtracting from it.")

    def run(self, app, params, progress, cancel, begin_commit):
        self.validate(params)
        key, action = params["key"], params["action"]
        client = self.bridge.client
        if action == "grow":
            begin_commit()
            active(client, key)
            pixels = params["pixels"]
            client.run("select.modify.expand", radius=pixels)
            message = f"Selection grew by {pixels} px."
            if pixels != self.grow_pixels:
                try:
                    write_json(self._settings_path, {"grow_pixels": pixels})
                except OSError:
                    return f"{message} Could not save this amount for next time."
                self.grow_pixels = pixels
            return message
        progress(0.05, "Finding the object with WeVisOne-VL…")
        described = params.get("query", "").strip()
        point = params.get("point") if action == "click" else None
        box = params["box"] if action == "box" else None
        unused = False
        try:
            mask = self.model.mask(app, self.source, described or DEFAULT_QUERY, point, progress, cancel, box=box)
        except NothingFound:
            if not described or action == "describe":
                raise
            # The field keeps its text after a description was used: the click or the box says what is meant now.
            progress(0.1, "Nothing there matches the description. Looking for any object…")
            mask = self.model.mask(app, self.source, DEFAULT_QUERY, point, progress, cancel, box=box)
            unused = True
        index, doc = active(client, key)
        size = (doc["width"], doc["height"])
        if mask.size != size:
            mask = mask.resize(size, Image.Resampling.BILINEAR)
        operation = OPERATIONS[params.get("operation", "replace")] if doc.get("hasSelection") else "new"
        with_parts = missed = False
        # Subtract trims a part away: what the model counts as attached to that part is no part of the trim.
        if params.get("whole", True) and operation != "subtract" and self.source.size == size:
            # The bin's wheel, the skier's skis, the umbrella in a hand: asked for, they come along.
            progress(0.7, "Looking for parts of the object the selection left out…")
            completed = self.model.whole(app, self.source, mask, described if action == "describe" else "", cancel)
            with_parts, mask = completed is not mask, completed
            missed = not with_parts and getattr(self.model, "parts_missed", False)
        guide = Image.new("RGBA", size, "white")
        guide.putalpha(mask)
        begin_commit()
        active(client, key)
        scratch, layer = self.bridge._open(guide, "mask")
        try:
            # Opening the scratch tab is deliberate. Do not redirect the result if the artist
            # has since changed the target, or switched away from our scratch tab.
            if (
                self.bridge.session().get("active") != scratch
                or key_for(index, client.run("document.inspect", document=index)) != key
            ):
                raise ValueError(STALE)
            client.run("document.activate", document=index)
            active(client, key)
            client.run(
                "select.loadSelection", channel="transparency", document=scratch, layer=layer, operation=operation
            )
            # A stroke after the import must never acquire the model's old overlay.
            known_mask = mask if operation == "new" else None
            if operation != "new" and self.matte_key == key and self.matte is not None:
                combine = ImageChops.lighter if operation == "add" else ImageChops.subtract
                known_mask = combine(self.matte, mask)
            if known_mask is not None:
                updated = client.run("document.inspect", document=index)
                if updated.get("revision") == doc.get("revision", 0) + 1:
                    self.matte_key, self.matte = key_for(index, updated), known_mask
        finally:
            self.bridge._close(scratch)
        message = {
            "new": "Object selected.",
            "add": "Added to the selection.",
            "subtract": "Subtracted from the selection.",
        }[operation][:-1] + (", with its attached parts." if with_parts else ".")
        if unused:
            message += " Nothing there matched the description, so it was not used."
        if missed:
            message += " The engine was too busy to look for attached parts."
        return message
