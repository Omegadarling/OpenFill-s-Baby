"""Read-only discovery of supported editors on an existing ComfyUI server.

No weights are copied or installed. Loader names are taken from the server, since
the client does not inspect the selected server's filesystem. Entries expire promptly;
each generation forces a fresh capability check before submitting a graph.
"""

from __future__ import annotations

import threading
import time
from pathlib import PurePosixPath

from .backend import (
    H3_IMAGE_EDIT_TEMPLATE,
    MOSS_ANYPAINT_TEMPLATE,
    QWEN21_EDIT_TEMPLATE,
    QWEN21_FULL_FILE,
    QWEN21_LIGHTNING_FILE,
    BackendUnavailable,
)
from .catalog import FileSpec, ModelEntry
from .comfy_api import ComfyClient

TURBO_ID = "remote-fibo2-edit-turbo-nvfp4"
DEV_ID = "remote-fibo2-dev-nvfp4"
TURBO_HERETIC_ID = TURBO_ID + "-heretic"
DEV_HERETIC_ID = DEV_ID + "-heretic"
HERETIC_ENCODER = "qwen3_vl_4b_nvfp4_full.safetensors"
QWEN21_FULL_ID = "remote-qwen-image-2.1-bf16"
QWEN21_LIGHTNING_ID = "remote-qwen-image-2.1-lightning-v2-rl"
LAMA_ID = "remote-lama-rmbg"
ROREM_ID = "remote-rorem-sdxl"
QWEN21_ENCODER = "qwen3vl_8b_fp8_scaled.safetensors"
QWEN21_VAE = "qwen_image_2.1_vae_bf16.safetensors"
H3_IMAGE_ID = "remote-minimax-h3-image"
MOSS_ANYPAINT_ID = "remote-moss-anypaint"
MOSS_ANYPAINT_NODES = ("MOSSAnyPaintPrepare", "MOSSAnyPaintEncode", "MOSSAnyPaintModelPatch")
# AnyPaint was trained on MOSS-Image Base (not SFT). Comfy-Org's INT8 repack is the
# published recipe; the BF16 export of the same Base weights is accepted where a host
# already has it, so no second copy is needed.
MOSS_BASE_FILES = ("moss_image_base_int8_convrot.safetensors", "moss_image_base_bf16.safetensors")
MOSS_ENCODER_FILES = ("qwen3.5_9b_int8_convrot.safetensors", "qwen3.5_9b_bf16.safetensors")
MOSS_VAE_FILE = "flux2-vae_small.safetensors"
MOSS_ANYPAINT_LORA = "moss_anypaint_rank32.safetensors"
# lodestones' Base 8-step DMD2 adapter. The KroMOSS install published the same file
# (SHA-256 7683fe1f...f6f2) under its own name; both spellings are one adapter.
MOSS_DMD2_FILES = ("lora_dmd2.safetensors", "kromoss_8-step_dmd2.safetensors")


def _first_filename(names: list[str], wanted: tuple[str, ...]) -> str:
    return next((found for found in (_filename(names, name) for name in wanted) if found), "")


def _discover_moss_anypaint(client: ComfyClient, source_label: str) -> list[ModelEntry]:
    """Offer MOSS AnyPaint only when its three nodes and every model file are visible."""
    if not all(client.object_info(node).get(node) for node in MOSS_ANYPAINT_NODES):
        return []
    unet = _first_filename(_options(client.object_info("UNETLoader"), "UNETLoader", "unet_name"), MOSS_BASE_FILES)
    clip_info = client.object_info("CLIPLoader")
    if not unet or "moss_image" not in _options(clip_info, "CLIPLoader", "type"):
        return []
    encoder = _first_filename(_options(clip_info, "CLIPLoader", "clip_name"), MOSS_ENCODER_FILES)
    vae = _filename(_options(client.object_info("VAELoader"), "VAELoader", "vae_name"), MOSS_VAE_FILE)
    loras = _options(client.object_info("LoraLoaderModelOnly"), "LoraLoaderModelOnly", "lora_name")
    adapter = _filename(loras, MOSS_ANYPAINT_LORA)
    distill = _first_filename(loras, MOSS_DMD2_FILES)
    if not all((encoder, vae, adapter, distill)):
        return []
    return [
        ModelEntry(
            id=MOSS_ANYPAINT_ID,
            name=f"MOSS AnyPaint · {source_label}",
            tier="edit",
            capabilities=("fill", "edit"),
            arch="moss_image",
            template=MOSS_ANYPAINT_TEMPLATE,
            page="https://huggingface.co/yijunwang2/moss-anypaint",
            license_name="Apache-2.0 (weights) · MIT (nodes)",
            license_url="https://huggingface.co/yijunwang2/moss-anypaint/blob/main/LICENSE",
            gated=False,
            source_type="community",
            uploader="yijunwang2 (unofficial adapter for OpenMOSS MOSS-Image Base)",
            min_vram_gb=16,
            requires_nodes=(),
            requires_models=(),
            source="remote",
            family="moss-anypaint",
            files=(
                FileSpec("unet", "", "diffusion_models/" + unet),
                FileSpec("clip1", "", "text_encoders/" + encoder),
                FileSpec("vae", "", "vae/" + vae),
                FileSpec("lora", "", "loras/" + adapter),
                FileSpec("lora2", "", "loras/" + distill),
            ),
        )
    ]


def _discover_h3_image(client: ComfyClient, source_label: str) -> list[ModelEntry]:
    """Only offer H3 when the native node and every compatible model are visible."""
    unet = _filename(
        _options(client.object_info("UNETLoader"), "UNETLoader", "unet_name"),
        "minimax_h3_image_pruned_int8_convrot.safetensors",
    )
    if not unet or not client.object_info("MiniMaxH3ImageConditioning").get("MiniMaxH3ImageConditioning"):
        return []
    clip_info = client.object_info("CLIPLoader")
    if "minimax" not in _options(clip_info, "CLIPLoader", "type"):
        return []
    clips = _options(clip_info, "CLIPLoader", "clip_name")
    # INT8 works on Ada and Blackwell; NVFP4 requires a supported GPU path.
    encoder = _filename(clips, "qwen3vl_32b_minimax_h3_int8_convrot.safetensors") or _filename(
        clips, "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    )
    vae = _filename(
        _options(client.object_info("VAELoader"), "VAELoader", "vae_name"), "minimax_h3_image_vae_fp16.safetensors"
    )
    if not encoder or not vae:
        return []
    return [
        ModelEntry(
            id=H3_IMAGE_ID,
            name=f"MiniMax H3 Image · {source_label}",
            tier="edit",
            capabilities=("fill", "edit", "remove"),
            arch="h3_image",
            template=H3_IMAGE_EDIT_TEMPLATE,
            page="https://huggingface.co/Comfy-Org/MiniMax-H3-Image",
            license_name="MiniMax H3 Image license",
            license_url="https://huggingface.co/MiniMaxAI/MiniMax-H3-Image/blob/main/LICENSE",
            gated=False,
            source_type="first-party",
            uploader="Comfy-Org (MiniMax)",
            min_vram_gb=24,
            requires_nodes=(),
            requires_models=(),
            source="remote",
            family="h3-image",
            files=(
                FileSpec("unet", "", "diffusion_models/" + unet),
                FileSpec("clip1", "", "text_encoders/" + encoder),
                FileSpec("vae", "", "vae/" + vae),
            ),
        )
    ]


def _options(info: dict, node: str, field: str) -> list[str]:
    item = info.get(node, {}).get("input", {}).get("required", {}).get(field, [])
    return [x for x in item[0] if isinstance(x, str)] if item and isinstance(item[0], list) else []


def _filename(names: list[str], wanted: str) -> str:
    matches = [n for n in names if PurePosixPath(n.replace("\\", "/")).name.lower() == wanted.lower()]
    # Prefer a top-level exact filename; otherwise retain the server's folder spelling.
    return next((n for n in matches if n.lower() == wanted.lower()), min(matches, key=len, default=""))


def _discover_fibo(client: ComfyClient, source_label: str = "Local ComfyUI") -> list[ModelEntry]:
    info = {name: client.object_info(name) for name in ("TextEncodeFibo2", "UNETLoader", "CLIPLoader", "VAELoader")}
    if not info["TextEncodeFibo2"].get("TextEncodeFibo2"):
        raise BackendUnavailable(
            f"{source_label}'s ComfyUI does not expose TextEncodeFibo2. Start its FIBO-enabled backend."
        )
    if "fibo2" not in _options(info["CLIPLoader"], "CLIPLoader", "type"):
        raise BackendUnavailable(f"{source_label}'s CLIPLoader does not support FIBO 2.0.")
    encoder = _filename(_options(info["CLIPLoader"], "CLIPLoader", "clip_name"), "qwen3vl_4b_bf16.safetensors")
    heretic = _filename(_options(info["CLIPLoader"], "CLIPLoader", "clip_name"), HERETIC_ENCODER)
    vae_names = _options(info["VAELoader"], "VAELoader", "vae_name")
    # Prefer the pinned FIBO export over a same-named generic FLUX.2 export.
    vae = next(
        (name for name in vae_names if name.replace("\\", "/").lower() == "fibo-2.0/flux2-vae.safetensors"),
        _filename(vae_names, "flux2-vae.safetensors"),
    )
    if not encoder or not vae:
        raise BackendUnavailable(
            f"{source_label} needs the existing FIBO BF16 Qwen3-VL 4B encoder and FLUX.2 VAE visible to ComfyUI."
        )
    names = _options(info["UNETLoader"], "UNETLoader", "unet_name")
    entries = []
    for mid, name, filename, page in (
        (
            TURBO_ID,
            "FIBO 2.0 Edit Turbo NVFP4",
            "fibo2_edit_turbo_nvfp4.safetensors",
            "https://huggingface.co/BennyDaBall/Fibo-2.0-edit-turbo-NVFP4",
        ),
        (
            DEV_ID,
            "FIBO 2.0 Dev Edit NVFP4",
            "fibo2_dev_nvfp4.safetensors",
            "https://huggingface.co/BennyDaBall/Fibo-2.0-dev-NVFP4",
        ),
    ):
        unet = _filename(names, filename)
        if not unet:
            continue
        entries.append(
            ModelEntry(
                id=mid,
                name=f"{name} · Original · {source_label}",
                tier="edit",
                capabilities=("fill", "edit", "remove") if mid == DEV_ID else ("fill", "edit"),
                arch="fibo2",
                template="fibo2_edit",
                page=page,
                license_name="See FIBO model license",
                license_url="https://huggingface.co/Comfy-Org/Fibo-2.0",
                gated=False,
                source_type="community",
                uploader="BennyDaBall (BRIA FIBO quantization)",
                min_vram_gb=24,
                requires_nodes=(),
                requires_models=(),
                source="remote",
                family="fibo2-edit",
                files=(
                    FileSpec("unet", "", "diffusion_models/" + unet),
                    FileSpec("clip1", "", "text_encoders/" + encoder),
                    FileSpec("vae", "", "vae/" + vae),
                ),
            )
        )
    if not entries:
        raise BackendUnavailable(f"No FIBO 2.0 NVFP4 Dev or Edit Turbo model is visible on {source_label}.")
    if heretic:
        # Original entries stay first, preserving the default when no preference
        # is saved. Removal was verified with Dev and its original encoder.
        from dataclasses import replace

        entries.extend(
            [
                replace(
                    entry,
                    id=entry.id + "-heretic",
                    name=entry.name.replace(" · Original · ", " · Heretic · "),
                    capabilities=("fill", "edit"),
                    files=tuple(
                        FileSpec("clip1", "", "text_encoders/" + heretic) if file.role == "clip1" else file
                        for file in entry.files
                    ),
                )
                for entry in entries
            ]
        )
    return entries


def _discover_qwen21(client: ComfyClient, source_label: str) -> list[ModelEntry]:
    diffusion = _options(client.object_info("UNETLoader"), "UNETLoader", "unet_name")
    full = _filename(diffusion, QWEN21_FULL_FILE)
    lightning = _filename(diffusion, QWEN21_LIGHTNING_FILE)
    if not (full or lightning):
        return []
    for node in ("TextEncodeQwenImage21", "QwenImage21Cache"):
        if not client.object_info(node).get(node):
            raise BackendUnavailable(f"{source_label}'s ComfyUI does not expose {node} for Qwen Image 2.1.")
    clip_info = client.object_info("CLIPLoader")
    if "qwen_image" not in _options(clip_info, "CLIPLoader", "type"):
        raise BackendUnavailable(f"{source_label}'s CLIPLoader does not support Qwen Image.")
    encoder = _filename(_options(clip_info, "CLIPLoader", "clip_name"), QWEN21_ENCODER)
    vae = _filename(_options(client.object_info("VAELoader"), "VAELoader", "vae_name"), QWEN21_VAE)
    if not encoder or not vae:
        raise BackendUnavailable(
            f"{source_label} needs the Qwen3-VL 8B FP8-scaled encoder and Qwen Image 2.1 BF16 VAE."
        )
    entries = []
    for mid, label, unet, uploader, page in (
        (
            QWEN21_FULL_ID,
            "Qwen Image 2.1 BF16",
            full,
            "Comfy-Org (Qwen)",
            "https://huggingface.co/Comfy-Org/Qwen-Image-2.1",
        ),
        (
            QWEN21_LIGHTNING_ID,
            "Qwen Image 2.1 Lightning v2 RL",
            lightning,
            "LightX2V",
            "https://huggingface.co/lightx2v/Qwen-Image-2.1-Lightning-v2-RL",
        ),
    ):
        if unet:
            entries.append(
                ModelEntry(
                    id=mid,
                    name=f"{label} · {source_label}",
                    tier="edit",
                    capabilities=("fill", "edit", "remove") if mid == QWEN21_LIGHTNING_ID else ("fill", "edit"),
                    arch="qwen_image",
                    template=QWEN21_EDIT_TEMPLATE,
                    page=page,
                    license_name="Qwen Research License",
                    license_url="https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE",
                    gated=False,
                    source_type="first-party",
                    uploader=uploader,
                    min_vram_gb=24,
                    requires_nodes=(),
                    requires_models=(),
                    source="remote",
                    family="qwen-edit",
                    files=(
                        FileSpec("unet", "", "diffusion_models/" + unet),
                        FileSpec("clip1", "", "text_encoders/" + encoder),
                        FileSpec("vae", "", "vae/" + vae),
                    ),
                )
            )
    return entries


def _discover_lama(client: ComfyClient, source_label: str) -> list[ModelEntry]:
    node = client.object_info("AILab_LamaRemover").get("AILab_LamaRemover", {})
    required = node.get("input", {}).get("required", {})
    fields = {"images": "IMAGE", "masks": "MASK", "removal_strength": "INT", "edge_smoothness": "INT"}
    if set(required) != set(fields) or any(not required[k] or required[k][0] != t for k, t in fields.items()):
        return []
    if (node.get("output") or [None])[0] != "IMAGE":
        return []
    return [
        ModelEntry(
            id=LAMA_ID,
            name=f"LaMa (Big-LaMa) · {source_label}",
            tier="removal",
            capabilities=("remove",),
            arch="inpaint_model",
            template="lama_rmbg",
            page="https://github.com/advimman/lama",
            license_name="Apache-2.0 (model)",
            license_url="https://github.com/advimman/lama/blob/main/LICENSE",
            gated=False,
            source_type="existing-node",
            uploader="ComfyUI-RMBG",
            min_vram_gb=2,
            requires_nodes=(),
            requires_models=(),
            files=(),
            source="remote",
            family="removal",
        )
    ]


def _discover_rorem(client: ComfyClient, source_label: str) -> list[ModelEntry]:
    def names(node, field):
        return _options(client.object_info(node), node, field)

    unet = _filename(names("UNETLoader", "unet_name"), "RORem-unet.safetensors")
    if not unet:
        return []
    info = client.object_info("DualCLIPLoader")
    if "sdxl" not in _options(info, "DualCLIPLoader", "type"):
        return []
    clip_l = _filename(_options(info, "DualCLIPLoader", "clip_name1"), "clip_l.safetensors")
    clip_g = _filename(_options(info, "DualCLIPLoader", "clip_name2"), "RORem-clip-g.safetensors")
    vae = _filename(names("VAELoader", "vae_name"), "RORem-sdxl-vae.safetensors")
    if not (
        clip_l and clip_g and vae and client.object_info("InpaintModelConditioning").get("InpaintModelConditioning")
    ):
        return []
    return [
        ModelEntry(
            id=ROREM_ID,
            name=f"RORem (SDXL) · {source_label}",
            tier="removal",
            capabilities=("remove",),
            arch="sdxl",
            template="sdxl_eraser",
            page="https://huggingface.co/LetsThink/RORem",
            license_name="Apache-2.0",
            license_url="https://huggingface.co/LetsThink/RORem",
            gated=False,
            source_type="first-party",
            uploader="RORem authors",
            min_vram_gb=8,
            requires_nodes=(),
            requires_models=(),
            source="remote",
            family="removal",
            files=(
                FileSpec("unet", "", "diffusion_models/" + unet),
                FileSpec("clip1", "", "text_encoders/" + clip_l),
                FileSpec("clip2", "", "text_encoders/" + clip_g),
                FileSpec("vae", "", "vae/" + vae),
            ),
        )
    ]


def discover(client: ComfyClient, source_label: str = "Local ComfyUI") -> list[ModelEntry]:
    """Discover each family independently so an unavailable family cannot hide others."""

    def discover_sdxl(selected_client, selected_label):
        # A missing/corrupt optional SDXL registry must not hide FIBO either.
        from .remote_sdxl import discover_sdxl as lookup

        return lookup(selected_client, selected_label)

    entries = []
    problems = []
    for family, lookup in (
        ("FIBO", _discover_fibo),
        ("SDXL", discover_sdxl),
        ("Qwen Image 2.1", _discover_qwen21),
        ("MiniMax H3 Image", _discover_h3_image),
        ("MOSS AnyPaint", _discover_moss_anypaint),
        ("LaMa", _discover_lama),
        ("RORem", _discover_rorem),
    ):
        try:
            entries.extend(lookup(client, source_label))
        except Exception as err:
            problems.append(f"{family}: {err}")
    if not entries:
        details = (
            "; ".join(problems) or "No verified FIBO, SDXL, or Qwen Image 2.1 checkpoint is visible to this server."
        )
        raise BackendUnavailable(f"No supported editing models are available on {source_label}. {details}")
    return entries


class RemoteModels:
    def __init__(self, url: str, client: ComfyClient | None = None, *, source_label: str = "Local ComfyUI") -> None:
        self.url = url
        self.source_label = source_label
        self.client = client or ComfyClient(url, timeout=5)
        self.entries: list[ModelEntry] = []
        self.error = ""
        self.checked = 0.0
        self.lock = threading.RLock()

    def refresh(self, force: bool = False) -> list[ModelEntry]:
        with self.lock:
            if not force and time.monotonic() - self.checked < 10:
                return list(self.entries)
            try:
                if not self.client.is_alive(timeout=2):
                    raise BackendUnavailable(
                        f"{self.source_label} is not answering. Start ComfyUI and use Engine > Check."
                    )
                self.entries = discover(self.client, self.source_label)
                self.error = ""
            except Exception as err:
                self.entries = []
                self.error = f"Cannot use editing models on {self.source_label} at {self.url}: {err}"
            self.checked = time.monotonic()
            return list(self.entries)
