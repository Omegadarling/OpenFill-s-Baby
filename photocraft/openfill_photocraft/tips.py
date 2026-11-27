# OpenFill for PhotoCraft
# SPDX-License-Identifier: Apache-2.0
"""Tooltips for both panels (no Tk here): what each control does, and what each model and removal
method is good and bad at.

A tip is a short description; a model's or method's tip follows it with a ``Pros:`` and a ``Cons:``
line. Star figures for the removal methods are the means of the finished ratings behind
``removal_table`` (1 to 5 stars, about 48 pictures each); timings are from the same runs on one
24 GB card and only show how the methods compare.
"""

from __future__ import annotations

from typing import Any

from openfill.remote_models import (
    DEV_HERETIC_ID,
    DEV_ID,
    FLUX3_KLEIN_ID,
    H3_IMAGE_ID,
    LAMA_ID,
    MOSS_ANYPAINT_ID,
    QWEN21_FULL_ID,
    QWEN21_LIGHTNING_ID,
    ROREM_ID,
    TURBO_HERETIC_ID,
    TURBO_ID,
)

from .edit_plan import AUTO_EDIT
from .removal_advice import AUTO, SCENE_PLACEHOLDER, TEXTURE_REMOVAL
from .removal_plan import AUTO_REMOVAL

MANAGE = "Opens Manage Models: download, hide or uninstall models."


def _tip(about: str, pros: str = "", cons: str = "") -> str:
    return "\n".join(line for line in (about, pros and f"Pros: {pros}", cons and f"Cons: {cons}") if line)


# ---- removal methods -----------------------------------------------------------------------

_ROREM = _tip(
    "RORem: an SDXL model trained to erase (3.5 of 5 stars). The best-rated remover on small selections.",
    "cleanest background on selections under 1% of the picture; rarely leaves the object behind; quick.",
    "can soften fine texture and bend shapes hidden behind the object; on larger selections FLUX 3 rated higher; "
    "about 6 s; needs 8 GB.",
)
_QWEN_REMOVE = (
    "For Remove: rebuilds hidden shapes such as a wheel or an eye (2.3 stars overall). It left the object in place "
    "in about half of the test repairs, so include the shadow and some background in the selection."
)
_LAMA = _tip(
    "LaMa: a small prompt-free inpainter that continues the surrounding pattern (2.3 stars).",
    "fast (about 3 s) and light (2 GB); good on small selections, wires and repeating patterns such as paving.",
    "smears large selections into a blur; cannot invent hidden objects or shapes.",
)
_LOCAL = _tip(
    "Local background repair: fills from nearby pixels on this computer, without ComfyUI (1.9 stars).",
    "instant; needs no graphics card or engine; good on smooth backdrops and gradual lighting.",
    "repeats or smears texture; breaks edges that cross the selection; rarely the best repair.",
)
_FIBO_REMOVE = (
    "For Remove: rated low (2.0 stars) and takes about two minutes; most test runs fell back to Local "
    "background repair. It did best on water and bubbles."
)
_H3_REMOVE = (
    "For Remove: the lowest-rated method (1.8 stars); two repairs in three matched Local background repair."
)
_ANYPAINT_REMOVE = "For Remove: mid-rated (2.4 stars, about 14 s); it never sees the object it replaces."

METHODS = {
    TEXTURE_REMOVAL: _LOCAL,
    AUTO_REMOVAL: _tip(
        "Automatic: makes two repairs with the methods that rated best for a selection this size, and you keep "
        "the better one. Where the surroundings are plain (a studio backdrop, a wall, a clear sky), Local's repair "
        "is shown as a third.",
        "the better of two rated 4.2 stars (a 4 or 5 on three pictures in four), against 3.3 for RORem alone; a "
        "repair that leaves the object is replaced by the next method.",
        "takes longer than one method: about 35 s once a selection covers 1% of the picture and about a minute over "
        "5%, where FLUX 3 runs first; needs RORem with FLUX 3, Qwen Lightning or LaMa on the engine.",
    ),
}

# ---- models --------------------------------------------------------------------------------

MODELS = {
    AUTO_EDIT: _tip(
        "Automatic: makes two edits with the models that rated best together for this kind of edit, and you keep "
        "the better one. Qwen goes first for a new colour, material or lettering, FIBO Turbo for a new or different "
        "object; an edit of the whole picture uses Qwen and H3. FIBO runs in the mode that suits the edit (Preserve "
        "keeps the object's shape, Replace draws a new one) and in the other as a third result, with the settings "
        "the edits were rated with. Something added \"here\" in a box you drew gets FIBO Replace twice and Qwen told "
        "where the box is; words to be written reach Qwen as you typed them.",
        "on 24 rated edits the better of the two was good (4 or 5 stars) on 17, against 11 for the best single "
        "model; you do not have to know which model or setting suits the edit.",
        "takes the models' time, and a third result to compare; reference images need a model picked by hand; "
        "needs two of Qwen, FIBO and H3 on the engine.",
    ),
    ROREM_ID: _ROREM,
    "rorem-sdxl": _ROREM,
    LAMA_ID: _LAMA,
    "lama-big": _LAMA,
    "mat-places512": _tip(
        "MAT: a small prompt-free inpainter, an alternative to LaMa.",
        "fast and light (2 GB); can invent more varied texture than LaMa.",
        "works at 512 px, so large selections come out soft; not part of the rated comparison.",
    ),
    "flux1-fill-object-removal-lora": _tip(
        "FLUX removal LoRA: FLUX.1 Fill with an adapter trained to erase.",
        "detailed, realistic backgrounds on large selections.",
        "slow and heavy; needs FLUX.1 Fill installed; not part of the rated comparison.",
    ),
    QWEN21_LIGHTNING_ID: _tip(
        "Qwen Image 2.1 Lightning: instruction editing in four steps. Describe the change and what to keep.",
        "fast (about 7 s); follows instructions; takes up to 15 reference images; also offered for Remove.",
        "needs 24 GB; may redraw details you did not ask about.",
    ) + "\n" + _QWEN_REMOVE,
    QWEN21_FULL_ID: _tip(
        "Qwen Image 2.1: the full 25-step model behind Lightning. Describe the change and what to keep.",
        "the model Lightning was distilled from; takes up to 15 reference images.",
        "about six times slower than Lightning (about 46 s); it was not better than Lightning in the removal "
        "test, so it is not offered for Remove.",
    ),
    TURBO_ID: _tip(
        "FIBO 2.0 Edit Turbo: instruction editing in four steps. Describe the change.",
        "fast; takes reference images; edits the selection or the whole image.",
        "less detail than FIBO Dev; needs 24 GB and a card that runs NVFP4; not offered for Remove.",
    ),
    DEV_ID: _tip(
        "FIBO 2.0 Dev: instruction editing in 30 steps. Describe the change.",
        "finer detail than Turbo; takes reference images.",
        "slow (about two minutes); needs 24 GB and a card that runs NVFP4.",
    ) + "\n" + _FIBO_REMOVE,
    TURBO_HERETIC_ID: _tip(
        "FIBO 2.0 Edit Turbo with the Heretic text encoder, which follows prompts the original encoder may refuse.",
        "fast; fewer refused prompts.",
        "less detail than Dev; results differ from the original encoder; not offered for Remove.",
    ),
    DEV_HERETIC_ID: _tip(
        "FIBO 2.0 Dev with the Heretic text encoder, which follows prompts the original encoder may refuse.",
        "finer detail than Turbo; fewer refused prompts.",
        "slow (about two minutes); results differ from the original encoder; not offered for Remove.",
    ),
    FLUX3_KLEIN_ID: _tip(
        "FLUX 3 Image Klein NVFP4: edits the selection in 16 steps. The selection is greyed in the reference the model sees.",
        "one picture, no reference images; fast on Blackwell GPUs.",
        "rated low for edits (2.1 of 5 stars; it often changed too much); its FLUX Kommunity License limits "
        "commercial output to companies under US$5M annual revenue.",
    ) + ("\nFor Remove: the best-rated method on selections covering 1% of the picture or more (3.9 stars, against "
         "3.2 for RORem), so Automatic shows its repair first there, with RORem beside it. About 35 s, or about a "
         "minute on large selections. On small selections use RORem."),
    H3_IMAGE_ID: _tip(
        "MiniMax H3 Image: instruction editing in six steps, on the selection or the whole image.",
        "quick for its size; follows instructions; takes up to 4 reference images.",
        "needs 24 GB.",
    ) + "\n" + _H3_REMOVE,
    MOSS_ANYPAINT_ID: _tip(
        "MOSS AnyPaint: repaints the selection in eight steps. Describe the finished picture, not the change.",
        "blends the repaint into its surroundings; needs 16 GB.",
        "does not follow edit instructions; no reference images.",
    ) + "\n" + _ANYPAINT_REMOVE,
}

# Models known only by how they are run (downloaded catalog models, discovered SDXL checkpoints).
TEMPLATES = {
    "sdxl_edit": _tip(
        "SDXL masked editing: repaints the selection at 75% change strength. Describe what should appear there.",
        "keeps the shape and colours of what was selected; leaves everything outside the selection untouched.",
        "cannot follow instructions or use reference images; cannot change the selection completely.",
    ),
    "sdxl_inpaint_edit": _tip(
        "SDXL inpainting: replaces the selection. Describe what should appear there.",
        "can replace the selection completely; leaves everything outside it untouched.",
        "cannot follow instructions or use reference images.",
    ),
    "sd15_inpaint": _tip(
        "Stable Diffusion 1.5 inpainting: fill, expand and create on small graphics cards.",
        "fast; runs in 4 GB; a small download.",
        "works near 512 px, so large areas lose detail; weak at text, hands and following long prompts.",
    ),
    "sdxl_fooocus": _tip(
        "SDXL with the Fooocus inpaint patch: fill, expand and create at about 1024 px.",
        "sharper and more coherent than SD 1.5; runs in 8 GB.",
        "slower than SD 1.5; needs the Fooocus patch download; describes content, not instructions.",
    ),
    "flux_fill": _tip(
        "FLUX.1 Fill: the highest-quality fill and expand model here.",
        "realistic detail and lighting; follows the prompt closely; continues a scene well when expanding.",
        "slow and large; needs a free Hugging Face key; paints everything you name, so describe only the "
        "selection; cannot create from nothing.",
    ),
    "qwen_edit": _tip(
        "Qwen-Image-Edit: instruction editing. Describe the change instead of the content.",
        "follows instructions such as 'make the jacket red'; also fills and expands.",
        "needs 12 GB; slower than the SDXL models; may redraw details you did not ask about.",
    ),
    "inpaint_model": _LAMA,
    "sdxl_eraser": _ROREM,
    "flux_eraser": MODELS["flux1-fill-object-removal-lora"],
}


def model_tip(model_id: str, rows: Any = ()) -> str:
    """The tip for a model or removal method; ``rows`` (the panel's model rows) names the template
    of a model that is only known by how it runs."""
    if model_id == "default":
        row = next((r for r in rows if r.get("id") in ("lama-big", "mat-places512") and r.get("installed")), None)
        base = MODELS[row["id"]] if row else _LAMA
        return base + "\nThe default: it runs at the picture's own resolution."
    if model_id in METHODS:
        return METHODS[model_id]
    if model_id in MODELS:
        return MODELS[model_id]
    row = next((r for r in rows if r.get("id") == model_id), None)
    return TEMPLATES.get((row or {}).get("template") or "", "")


def model_tips(rows: Any) -> dict[str, str]:
    """Every tip the browser view needs for the models and methods it can list."""
    out = {key: model_tip(key, rows) for key in ("default", *METHODS, AUTO_EDIT)}
    out.update({row["id"]: tip for row in rows if (tip := model_tip(row["id"], rows))})
    return out


def scene_tip(label: str, scene: dict | None = None, method: str = "") -> str:
    """The tip for an entry of Kind of removal; ``method`` is the name the scene picks."""
    if label == AUTO:
        return METHODS[AUTO_REMOVAL] + "\nWithout those methods it recommends one from the colours and edges around the selection."
    if label == SCENE_PLACEHOLDER:
        return "Manual method: you choose the Method yourself, and one repair is made with it."
    if not scene:
        return ""
    picks = f"Picks {method}. " if method else "Picks no method: choose one yourself. "
    return picks + scene.get("tip", "")


# ---- controls ------------------------------------------------------------------------------

# Auto-feather softens inward, so a model picked by hand works inside the selection as it is. Automatic first
# grows the selection until that soft band lies outside the object (edit_plan.roomy). Both panels say so under
# the slider too, in these words.
FEATHER_GROWTH = (
    "With a model picked by hand the selection does not grow. "
    "Automatic grows it first, so the soft edge lies outside the object."
)

CONTROLS = {
    # header and sections
    "status": "The AI engine: Ready, starting, or what is missing. Details are under Engine.",
    "collapse": "Fold the panel into a small strip in PhotoCraft. Click the strip to open it again.",
    "pill": "OpenFill: click to open the panel. Drag to move the strip.",
    "section": "Show or hide this section.",
    "grip": "Drag to reorder the sections, or focus it and use the Up and Down arrows.",
    # the setup card
    "setup.download": "Download ComfyUI into the folder shown and set it up. You can keep using PhotoCraft meanwhile.",
    "setup.folder": "Point OpenFill at a ComfyUI you already have: the folder with main.py.",
    "setup.help": "Open the install guide in your browser.",
    "setup.models": "Open Manage Models to download a model.",
    # modes
    "mode.select": "Select: pick an object by clicking it, dragging a box around it or describing it. The result is "
                   "a PhotoCraft selection for Edit or Remove.",
    "mode.fill": "Edit: change the selection (or the whole image with Select All) from a prompt.",
    "mode.remove": "Remove: erase the selected object and continue the background. No prompt.",
    "tool.fill": "Fill: replace or change what is inside the selection.",
    "tool.expand": "Expand: add canvas around the picture and paint the new area. Works best in rounds of up to "
                   "25% per side.",
    "tool.txt2img": "Create: make a picture from a prompt alone, as a new image or as a layer.",
    "context": "What PhotoCraft has open and selected, and what the next run will work on.",
    # select
    "select.canvas": "The current canvas. Click an object, or drag a box around it, to select it in PhotoCraft.",
    "select.overlay": "Show the current PhotoCraft selection on this preview.",
    "select.replace": "Replace: the next pick becomes the selection.",
    "select.add": "Add: the next pick is added to the selection, for an object plus its shadow.",
    "select.subtract": "Subtract: the next pick is cut out of the selection.",
    "select.whole": "Also select what is attached to the object or held by it: a bin's wheel, a skier's skis, a "
                    "kite's string. Untick to pick one part on its own.",
    "select.query": "Describe the object in a few words, such as 'the cup on the left', then press Enter. "
                    "Use it when a click picks the wrong thing.",
    "select.grow": "How many pixels Grow widens the selection by. A wider selection clears outlines and shadows, "
                   "and Qwen removes better with one.",
    "select.grow_button": "Widen the current selection by this many pixels.",
    "go.select": "Select the object described above.",
    # remove
    "remove.kind": "How Remove chooses its method: Automatic, your own choice, or a kind of scene that picks a "
                   "method known to suit it.",
    "method": "The removal method. Picking one here switches Kind of removal to Manual method.",
    "remove.cast": "Also remove the object's own shadow and reflection, and the shaded edge the selection stops short "
                   "of. OpenFill finds them for a selected object; untick to remove only what you selected.",
    "go.remove": "Remove the selected object. Leave some background around it.",
    # edit
    "prompt": "What to make. Enter runs it, Shift+Enter adds a line, Up and Down recall earlier prompts.",
    "variations": "How many results to make. Each is a layer; you pick one afterwards. More take longer.",
    "model": "The model that runs this tool. The choice is remembered.",
    "references": "Extra images the prompt can refer to. Qwen and FIBO edit models use them.",
    "go.fill": "Run the edit on the selection.",
    "go.expand": "Add the new canvas and paint it.",
    "go.txt2img": "Create the picture.",
    "cancel": "Stop the run. The image stays unchanged.",
    "neg": "Avoid: things that should not appear, such as 'text, people'.",
    "strength": "Change amount: 1.00 replaces the selection, lower values keep more of what is there.",
    "seed": "Seed: the same number with the same settings repeats a result. Empty picks a new one each run.",
    # expand
    "expand.ratio": "The shape of the new canvas. Custom follows the edges you drag.",
    "expand.turn": "Whether the chosen ratio is wide or tall.",
    "expand.canvas": "Drag an edge or corner outward to add space, inward to crop; drag inside to move the frame. "
                     "The wheel zooms.",
    "expand.margin": "Pixels to add on this side. A negative value crops it.",
    # create
    "size": "The size of the created picture: Square 1024 x 1024, Landscape 1216 x 832, Portrait 832 x 1216.",
    "target.image": "Open the result as a new PhotoCraft image.",
    "target.layer": "Add the result as a layer in the current image.",
    # results
    "thumbs": "Click a result to see it in the picture.",
    "keep": "Keep the shown result and remove the others.",
    "keep_all": "Keep every result as a layer in one group, with the shown one visible.",
    "discard": "Remove every result. The image stays as it was.",
    # reference images
    "refs.add": "Add up to the number of images the model allows (PNG, JPEG or WebP). Name them in the prompt "
                "by the labels shown.",
    "refs.up": "Move this image earlier. The order sets its label in the prompt.",
    "refs.down": "Move this image later. The order sets its label in the prompt.",
    "refs.remove": "Take this image off the list. The file is not deleted.",
    # models
    "models.hardware": "This computer's graphics card, memory and free disk space, or the engine the models are on.",
    "models.visible": "Show this model in the Model dropdown. Unchecking hides it without uninstalling.",
    "models.download": "Download this model into the ComfyUI folder. You can keep working meanwhile.",
    "models.install": "Install the ComfyUI nodes this model needs.",
    "models.uninstall": "Delete this model's files. Files other models use are kept.",
    "models.stop": "Stop this download. The part already downloaded is kept, so it can be resumed.",
    "models.token": "A free Hugging Face access token. FLUX needs it because its download requires accepting a license.",
    "models.save_token": "Save the key on this computer. It is only sent to Hugging Face.",
    # engine
    "engine.multi": "Use a different ComfyUI engine for Select, Edit and Remove, for example a small card for "
                    "Select and a large one for Edit.",
    "engine.select": "The engine that runs Select (WeVisOne-VL).",
    "engine.edit": "The engine that runs Edit. Its models fill the Model dropdown.",
    "engine.remove": "The engine that runs Remove. Its models decide which methods Remove offers.",
    "servers.find": "Look for ComfyUI on this computer and the local network, on its usual ports.",
    "servers.add": "Save a ComfyUI server by address: another port, an SSH connection, or one that is offline now.",
    "servers.found": "Save this server so the engine menus offer it.",
    "servers.address": "host:port, or ssh://host:port to reach ComfyUI through an SSH connection you already have.",
    "servers.name": "The name the engine menus show. Empty uses the address.",
    "servers.save": "Save the server. Choose it afterwards in an engine menu.",
    "servers.cancel": "Close this form without saving.",
    "engine.shared": "The ComfyUI engine every tool uses. Each engine offers the models installed on it.",
    "engine.folder": "The folder with ComfyUI's main.py. OpenFill starts ComfyUI from here.",
    "engine.browse": "Choose the ComfyUI folder.",
    "engine.autostart": "Start ComfyUI hidden when PhotoCraft opens, so the first run does not wait about a minute.",
    "engine.idle": "Stop ComfyUI after this many idle minutes to free graphics memory. 0 keeps it running.",
    "engine.extra": "Extra command-line options for ComfyUI, such as --lowvram on a card with little memory.",
    "engine.check": "Test the engine now and say what it runs on. Starts it if needed.",
    "engine.stop": "Stop ComfyUI now to free graphics memory. It starts again on the next run.",
    "engine.logs": "Open the folder with openfill.log and comfyui.log.",
    "engine.save": "Save the folder, start-up and idle settings above.",
    # generation
    "gen.steps": "Steps: more add detail and take longer. 20 to 30 is typical.",
    "gen.cfg": "Prompt strictness: how closely the result follows the prompt. 5 to 8 is typical; higher can look harsh.",
    "gen.grow_percent": "Repaint margin: how far past the selection the fill may repaint so edges blend, as a share "
                        "of the selection's size.",
    "gen.feather_percent": "Edge softness: how softly the new layer fades into the picture, as a share of the "
                           "selection's size.",
    "gen.auto_feather": "Soften the selection's edge inward before an edit, so the result blends without a seam. "
                        + FEATHER_GROWTH,
    "gen.auto_feather_percent": "How much of the selected area the soft edge covers. Higher blends more and keeps "
                                "less of the edit near the edge.",
    "gen.fibo_edit_mode": "How FIBO treats the selection.",
    "gen.fibo_edit_mode.preserve": _tip(
        "Preserve structure: starts from the original pixels.",
        "keeps the layout, pose and shapes; best for restyling and recolouring.",
        "cannot replace the content with something of a different shape.",
    ),
    "gen.fibo_edit_mode.replace": _tip(
        "Replace selected content: rebuilds the selection from its surroundings.",
        "can put something new there; needed for removal-like edits.",
        "the original details inside the selection are lost.",
    ),
    "gen.fibo_context_mode": "How much of the picture FIBO sees around the selection.",
    "gen.fibo_context_mode.local": _tip(
        "Surrounding area: FIBO sees the selection plus a margin.",
        "small selections keep fine detail at the model's working size.",
        "misses lighting and objects farther away.",
    ),
    "gen.fibo_context_mode.full": _tip(
        "Full image: FIBO sees the whole picture.",
        "matches the overall lighting and style.",
        "small selections lose fine detail at the model's working size.",
    ),
    "gen.fibo_context_percent": "The margin FIBO sees around the selection, as a share of its larger side. "
                                "More gives more context and less detail.",
    "gen.fibo_boundary_match": "Shift the result's colours to match the picture at the selection's edge. "
                               "It hides colour seams; it does not reconnect shapes.",
    "gen.fibo_boundary_strength": "How strongly edge colours are matched. High values can tint the whole edit.",
}


def page(rows: Any = ()) -> dict[str, Any]:
    """Everything the browser view shows as tooltips."""
    return {"controls": CONTROLS, "models": model_tips(rows), "manage": MANAGE,
            "auto": scene_tip(AUTO), "manual": scene_tip(SCENE_PLACEHOLDER)}
