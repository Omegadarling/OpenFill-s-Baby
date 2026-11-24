# SPDX-License-Identifier: Apache-2.0
"""FLUX 3 Image Klein edit contract: discovery, graph wiring, sizing and sampling (no GPU required)."""

import math
import unittest
from dataclasses import replace
from types import SimpleNamespace

from openfill import remote_models, workflow
from openfill.app import App
from openfill.backend import FLUX3_KLEIN_EDIT_TEMPLATE, InpaintRequest, ModelSpec, Sampling
from openfill.workflow import Capabilities, build_fill, flux3_klein_size

UNET = "FLUX-3-Image-Klein/flux3-image-klein-nvfp4.safetensors"
ENCODER = "FLUX-3-Image-Klein/qwen3vl_4b_bf16.safetensors"
VAE = "FLUX-3-Image-Klein/flux2-vae.safetensors"


def request(**changes):
    base = InpaintRequest(
        image_png=b"fixture",
        mask_png=b"mask",
        width=1024,
        height=768,
        work_width=1024,
        work_height=768,
        prompt="Change the t-shirt to bright yellow.",
        negative="",
        model=ModelSpec(arch="flux3_klein", template=FLUX3_KLEIN_EDIT_TEMPLATE, unet=UNET, clip1=ENCODER, vae=VAE),
        sampling=Sampling(steps=16, cfg=1.0),
    )
    return replace(base, **changes)


def graph(req):
    return build_fill(req, "openfill-test/source.png", "openfill-test/selection.png", 42, Capabilities())


class FakeEngine:
    """The object_info answers a server gives when it has the FLUX 3 Klein nodes and files."""

    def __init__(self, unet=UNET, clip_types=("flux3", "fibo2"), vae=VAE, nodes=True):
        self.unet, self.clip_types, self.vae, self.nodes = unet, clip_types, vae, nodes

    def object_info(self, node):
        if node in remote_models.FLUX3_KLEIN_NODES:
            return {node: {"input": {"required": {}}}} if self.nodes else {}
        fields = {
            "UNETLoader": {"unet_name": [[self.unet, "fibo2_dev_nvfp4.safetensors"]]},
            "CLIPLoader": {"clip_name": [[ENCODER]], "type": [list(self.clip_types)]},
            "VAELoader": {"vae_name": [[self.vae]]},
        }
        return {node: {"input": {"required": fields.get(node, {})}}}


class FluxKleinWorkflowTests(unittest.TestCase):
    def test_graph_follows_the_official_wiring(self):
        g = graph(request())
        self.assertEqual(g["unet"]["inputs"], {"unet_name": UNET, "weight_dtype": "default"})
        self.assertEqual(g["clip"]["inputs"], {"clip_name": ENCODER, "type": "flux3", "device": "default"})
        self.assertEqual(g["vae"]["inputs"]["vae_name"], VAE)
        self.assertEqual(g["sampler"]["inputs"]["steps"], 16)
        self.assertEqual(g["sampler"]["inputs"]["cfg"], 1.0)
        self.assertEqual(
            (g["sampler"]["inputs"]["sampler_name"], g["sampler"]["inputs"]["scheduler"]), ("euler", "simple")
        )
        self.assertEqual(g["shift"]["inputs"]["max_shift"], round(math.log(16), 5))
        self.assertEqual(g["shift"]["inputs"]["base_shift"], round(math.log(16), 5))
        self.assertEqual(g["reference"]["inputs"]["images.image0"], ["selection_guide", 0])
        self.assertEqual(g["sampler"]["inputs"]["positive"], ["reference", 0])
        self.assertEqual(g["sampler"]["inputs"]["negative"], ["reference", 1])
        self.assertFalse(any(node["class_type"] == "TextEncodeFibo2" for node in g.values()))
        width, height = g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]
        self.assertEqual((width % 16, height % 16), (0, 0))

    def test_only_the_nvfp4_diffusion_model_is_accepted(self):
        for name in (
            "flux3-image-klein-bf16.safetensors",
            "flux3-image-klein-nvfp4.safetensors.bak",
            "other_nvfp4.safetensors",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "NVFP4"):
                graph(request(model=replace(request().model, unet=name)))

    def test_expand_references_and_empty_instructions_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Expand"):
            graph(request(mode="expand"))
        with self.assertRaisesRegex(ValueError, "Write an edit instruction"):
            graph(request(prompt="   "))
        with self.assertRaisesRegex(ValueError, "reference images"):
            build_fill(request(), "a.png", "b.png", 1, Capabilities(), reference_refs=("ref.png",))

    def test_size_is_about_four_megapixels_in_multiples_of_16(self):
        for width, height in ((2496, 1664), (1024, 768), (600, 400)):
            with self.subTest(width=width, height=height):
                w, h = flux3_klein_size(width, height)
                self.assertEqual((w % 16, h % 16), (0, 0))
                self.assertGreaterEqual(min(w, h), 752)
        w, h = flux3_klein_size(2496, 1664)
        self.assertLess(abs(w * h - 4_000_000) / 4_000_000, 0.05)

    def test_narrow_strips_are_capped_near_the_normal_pixel_count(self):
        for width, height in ((6000, 500), (8000, 400), (4000, 500)):
            with self.subTest(width=width, height=height):
                w, h = flux3_klein_size(width, height)
                self.assertEqual((w % 16, h % 16), (0, 0))
                self.assertLessEqual(w * h, 4.6e6)

    def test_removal_sends_its_instruction_unwrapped_and_still_composites_once(self):
        g = graph(request(prompt=workflow.FLUX3_REMOVAL_PROMPT))
        self.assertEqual(g["prompt_positive"]["inputs"]["text"], workflow.FLUX3_REMOVAL_PROMPT)
        self.assertEqual(g["selection_composite"]["inputs"]["mask"], ["selection_support", 0])
        self.assertIn("Regenerate the gray selected area", graph(request())["prompt_positive"]["inputs"]["text"])

    def test_removal_holds_the_exterior_latent_and_edits_start_empty(self):
        for size in ((1024, 768), (480, 654)):  # native size, and a crop scaled up to the 768 floor
            with self.subTest(size=size):
                w, h = flux3_klein_size(*size)
                g = graph(
                    request(
                        prompt=workflow.FLUX3_REMOVAL_PROMPT, width=size[0], height=size[1], work_width=w, work_height=h
                    )
                )
                self.assertEqual(g["guide_latent"]["class_type"], "VAEEncode")
                self.assertEqual(g["guide_latent"]["inputs"], {"pixels": ["selection_guide", 0], "vae": ["vae", 0]})
                self.assertEqual(g["latent"]["class_type"], "SetLatentNoiseMask")
                self.assertEqual(g["latent"]["inputs"]["samples"], ["guide_latent", 0])
                self.assertEqual(g["latent"]["inputs"]["mask"], g["selection_guide"]["inputs"]["mask"])
                self.assertEqual(g["sampler"]["inputs"]["latent_image"], ["latent", 0])
                self.assertEqual(g["sampler"]["inputs"]["denoise"], 1.0)
        batch = graph(request(prompt=workflow.FLUX3_REMOVAL_PROMPT, sampling=Sampling(steps=16, cfg=1.0, batch=2)))
        self.assertEqual(batch["latent_batch"]["inputs"]["samples"], ["latent", 0])
        edit = graph(request())
        self.assertEqual(edit["latent"]["class_type"], "EmptyFlux2LatentImage")
        self.assertNotIn("guide_latent", edit)

    def test_selection_edit_templates_include_flux3(self):
        self.assertIn(FLUX3_KLEIN_EDIT_TEMPLATE, workflow.SELECTION_EDIT_TEMPLATES)
        self.assertIs(workflow.BUILDERS[FLUX3_KLEIN_EDIT_TEMPLATE], workflow.build_flux3_klein_edit)


class FluxKleinDiscoveryTests(unittest.TestCase):
    def test_offered_only_with_its_nodes_its_clip_type_and_all_three_files(self):
        entries = remote_models._discover_flux3_klein(FakeEngine(), "Studio")
        self.assertEqual([e.id for e in entries], [remote_models.FLUX3_KLEIN_ID])
        entry = entries[0]
        self.assertEqual(entry.template, FLUX3_KLEIN_EDIT_TEMPLATE)
        self.assertEqual(entry.capabilities, ("fill", "edit", "remove"))
        self.assertFalse(entry.gated)
        self.assertEqual(
            {f.role: f.dest for f in entry.files},
            {"unet": "diffusion_models/" + UNET, "clip1": "text_encoders/" + ENCODER, "vae": "vae/" + VAE},
        )

    def test_not_offered_without_nodes_clip_type_or_the_exact_files(self):
        self.assertEqual(remote_models._discover_flux3_klein(FakeEngine(nodes=False), "Studio"), [])
        self.assertEqual(remote_models._discover_flux3_klein(FakeEngine(clip_types=("fibo2",)), "Studio"), [])
        # A root-level flux2-vae.safetensors is another file; the FLUX 3 VAE is in its own folder.
        self.assertEqual(remote_models._discover_flux3_klein(FakeEngine(vae="flux2-vae.safetensors"), "Studio"), [])
        # A bare file name without its folder does not match.
        self.assertEqual(
            remote_models._discover_flux3_klein(FakeEngine(unet="flux3-image-klein-nvfp4.safetensors"), "Studio"), []
        )

    def test_the_family_is_in_the_full_discovery_list(self):
        ids = [entry.id for entry in remote_models.discover(FakeEngine(), "Studio")]
        self.assertIn(remote_models.FLUX3_KLEIN_ID, ids)


class FluxKleinSettingsTests(unittest.TestCase):
    def test_profile_and_sampling_follow_the_published_recipe(self):
        model = ModelSpec(arch="flux3_klein", template=FLUX3_KLEIN_EDIT_TEMPLATE, unet=UNET, clip1=ENCODER, vae=VAE)
        app_like = SimpleNamespace(settings=SimpleNamespace(steps=30, cfg=5.0, sampler="dpmpp_2m", scheduler="karras"))
        profile = App.profile_for(app_like, model)
        self.assertEqual((profile.name, profile.multiple, profile.max_side), ("flux3_klein", 16, 2048))
        sampling = App.sampling(app_like, model, 1, 42)
        self.assertEqual(
            (sampling.steps, sampling.cfg, sampling.sampler, sampling.scheduler), (16, 1.0, "euler", "simple")
        )


if __name__ == "__main__":
    unittest.main()
