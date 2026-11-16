# SPDX-License-Identifier: Apache-2.0
"""MOSS AnyPaint discovery and selection-safe graph contract."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openfill import catalog, remote_models, workflow
from openfill.app import App
from openfill.backend import MOSS_ANYPAINT_TEMPLATE, InpaintRequest, ModelSpec, Sampling, edit_reference_options

NODES = remote_models.MOSS_ANYPAINT_NODES


def client(
    unet=("moss/moss_image_base_int8_convrot.safetensors",),
    clips=("moss/qwen3.5_9b_int8_convrot.safetensors",),
    vaes=("moss/flux2-vae_small.safetensors",),
    loras=("moss/moss_anypaint_rank32.safetensors", "moss/lora_dmd2.safetensors"),
    clip_types=("moss_image",),
    nodes=NODES,
):
    files = {
        "UNETLoader": {"unet_name": list(unet)},
        "CLIPLoader": {"clip_name": list(clips), "type": list(clip_types)},
        "VAELoader": {"vae_name": list(vaes)},
        "LoraLoaderModelOnly": {"lora_name": list(loras)},
    }

    def info(node):
        if node in NODES:
            return {node: {"input": {}}} if node in nodes else {}
        return {node: {"input": {"required": {k: [v] for k, v in files.get(node, {}).items()}}}}

    mock = Mock()
    mock.object_info.side_effect = info
    return mock


def role(entry, name):
    return next(f.dest for f in entry.files if f.role == name)


class DiscoveryTests(unittest.TestCase):
    def test_published_int8_recipe_is_offered_for_edit_and_remove(self):
        (entry,) = remote_models._discover_moss_anypaint(client(), "host")
        self.assertEqual(entry.id, remote_models.MOSS_ANYPAINT_ID)
        self.assertEqual(entry.template, MOSS_ANYPAINT_TEMPLATE)
        self.assertEqual(entry.capabilities, ("fill", "edit", "remove"))
        self.assertEqual(role(entry, "unet"), "diffusion_models/moss/moss_image_base_int8_convrot.safetensors")
        self.assertEqual(role(entry, "lora"), "loras/moss/moss_anypaint_rank32.safetensors")
        self.assertEqual(role(entry, "lora2"), "loras/moss/lora_dmd2.safetensors")

    def test_existing_bf16_base_and_kromoss_named_dmd2_are_reused(self):
        entry = remote_models._discover_moss_anypaint(
            client(
                unet=("MOSS-Image\\moss_image_sft_bf16.safetensors", "MOSS-Image\\moss_image_base_bf16.safetensors"),
                clips=("MOSS-Image\\qwen3.5_9b_bf16.safetensors", "qwen3.5_9b_w4a8_gptq.safetensors"),
                vaes=("MOSS-Image\\flux2-vae_small.safetensors",),
                loras=("moss_anypaint_rank32.safetensors", "MOSS-Image\\kromoss_8-step_dmd2.safetensors"),
            ),
            "host",
        )[0]
        # Server spelling, including Windows separators, reaches the graph unchanged.
        spec = catalog.model_spec_for(entry)
        self.assertEqual(spec.unet, "MOSS-Image\\moss_image_base_bf16.safetensors")
        self.assertEqual(spec.clip1, "MOSS-Image\\qwen3.5_9b_bf16.safetensors")
        self.assertEqual(spec.lora, "moss_anypaint_rank32.safetensors")
        self.assertEqual(spec.lora2, "MOSS-Image\\kromoss_8-step_dmd2.safetensors")

    def test_int8_files_win_when_both_precisions_are_visible(self):
        (entry,) = remote_models._discover_moss_anypaint(
            client(
                unet=("moss_image_base_bf16.safetensors", "moss_image_base_int8_convrot.safetensors"),
                clips=("qwen3.5_9b_bf16.safetensors", "qwen3.5_9b_int8_convrot.safetensors"),
                loras=("kromoss_8-step_dmd2.safetensors", "lora_dmd2.safetensors", "moss_anypaint_rank32.safetensors"),
            ),
            "host",
        )
        self.assertTrue(role(entry, "unet").endswith("int8_convrot.safetensors"))
        self.assertTrue(role(entry, "clip1").endswith("int8_convrot.safetensors"))
        self.assertEqual(role(entry, "lora2"), "loras/lora_dmd2.safetensors")

    def test_hidden_unless_every_node_and_file_is_advertised(self):
        cases = {
            "no nodes": client(nodes=()),
            "missing patch node": client(nodes=NODES[:2]),
            "SFT is not the trained base": client(unet=("moss_image_sft_int8_convrot.safetensors",)),
            "untested encoder only": client(clips=("qwen3.5_9b_w4a8_gptq.safetensors",)),
            "full VAE only": client(vaes=("flux2-vae.safetensors",)),
            "no AnyPaint adapter": client(loras=("lora_dmd2.safetensors",)),
            "older DMD adapter only": client(loras=("moss_anypaint_rank32.safetensors", "lora.safetensors")),
            "loader without MOSS": client(clip_types=("qwen_image",)),
        }
        for label, mock in cases.items():
            with self.subTest(label):
                self.assertEqual(remote_models._discover_moss_anypaint(mock, "host"), [])

    def test_listed_after_existing_editors_and_never_hides_them(self):
        others = {
            name: [SimpleNamespace(id=name)]
            for name in (
                "_discover_fibo",
                "_discover_qwen21",
                "_discover_h3_image",
                "_discover_lama",
                "_discover_rorem",
            )
        }
        with (
            patch.multiple(remote_models, **{name: Mock(return_value=value) for name, value in others.items()}),
            patch("openfill.remote_sdxl.discover_sdxl", Mock(return_value=[])),
        ):
            ids = [entry.id for entry in remote_models.discover(client(), "host")]
            # Appended after H3, so the default edit model of an existing profile is unchanged.
            self.assertEqual(
                ids,
                [
                    "_discover_fibo",
                    "_discover_qwen21",
                    "_discover_h3_image",
                    remote_models.MOSS_ANYPAINT_ID,
                    "_discover_lama",
                    "_discover_rorem",
                ],
            )
            with patch.object(remote_models, "_discover_moss_anypaint", Mock(side_effect=OSError("lost"))):
                ids = [entry.id for entry in remote_models.discover(client(), "host")]
            self.assertNotIn(remote_models.MOSS_ANYPAINT_ID, ids)
            self.assertIn("_discover_h3_image", ids)


class GraphTests(unittest.TestCase):
    spec = ModelSpec(
        arch="moss_image",
        template=MOSS_ANYPAINT_TEMPLATE,
        unet="moss_image_base_int8_convrot.safetensors",
        clip1="qwen3.5_9b_int8_convrot.safetensors",
        vae="flux2-vae_small.safetensors",
        lora="moss_anypaint_rank32.safetensors",
        lora2="lora_dmd2.safetensors",
    )

    def request(self, width=1200, height=800, batch=1, prompt="a red ceramic mug on the desk", refs=()):
        return InpaintRequest(
            b"img",
            b"mask",
            width,
            height,
            width,
            height,
            prompt,
            "",
            self.spec,
            sampling=Sampling(steps=8, cfg=1.0, sampler="euler", scheduler="simple", batch=batch),
            reference_images=refs,
        )

    def test_working_size_is_about_one_megapixel_in_multiples_of_sixteen(self):
        self.assertEqual(workflow.moss_anypaint_size(1024, 1024), (1024, 1024))
        for size in ((1200, 800), (333, 4000), (4096, 1024), (17, 19)):
            with self.subTest(size=size):
                w, h = workflow.moss_anypaint_size(*size)
                self.assertEqual((w % 16, h % 16), (0, 0))
                self.assertAlmostEqual(w * h / 1024**2, 1.0, delta=0.08)
                self.assertAlmostEqual(w / h, size[0] / size[1], delta=0.05 * size[0] / size[1])
        with self.assertRaises(ValueError):
            workflow.moss_anypaint_size(1700, 100)

    def test_graph_follows_the_published_anypaint_recipe(self):
        g = workflow.build_fill(self.request(), "target.png", "mask.png", 7, workflow.Capabilities())
        self.assertEqual(g["anypaint_lora"]["inputs"]["model"], ["unet", 0])
        self.assertEqual(g["dmd2_lora"]["inputs"]["model"], ["anypaint_lora", 0])
        self.assertEqual(
            [g[n]["inputs"]["strength_model"] for n in ("anypaint_lora", "dmd2_lora")],
            [1.0, 1.0],
        )
        self.assertEqual(g["anypaint_patch"]["class_type"], "MOSSAnyPaintModelPatch")
        self.assertEqual(g["anypaint_patch"]["inputs"], {"model": ["dmd2_lora", 0], "kv_cache": True})
        self.assertEqual(g["clip"]["inputs"]["type"], "moss_image")
        prepare = g["prepare"]["inputs"]
        self.assertEqual(
            {k: prepare[k] for k in ("left", "top", "right", "bottom", "reference_max_edge", "boundary_redraw_px")},
            {"left": 0, "top": 0, "right": 0, "bottom": 0, "reference_max_edge": 384, "boundary_redraw_px": 32},
        )
        self.assertEqual(prepare["source"], ["image_scale", 0])
        self.assertEqual(prepare["generated_mask"], ["generated_mask", 0])
        self.assertEqual(g["support_image"]["inputs"]["mask"], ["selection_support", 0])
        self.assertEqual(g["support_scale"]["inputs"]["upscale_method"], "bilinear")
        encode = g["encode"]["inputs"]
        self.assertEqual(
            (encode["semantic_reference"], encode["known_image"], encode["keep_mask"]),
            (["prepare", 0], ["prepare", 1], ["prepare", 3]),
        )
        self.assertEqual(encode["prompt"], "a red ceramic mug on the desk")
        sampler = g["sampler"]["inputs"]
        self.assertEqual(
            (sampler["model"], sampler["latent_image"], sampler["steps"], sampler["cfg"]),
            (["anypaint_patch", 0], ["encode", 1], 8, 1.0),
        )
        self.assertEqual((sampler["sampler_name"], sampler["scheduler"], sampler["denoise"]), ("euler", "simple", 1.0))
        self.assertNotIn("ModelSamplingAuraFlow", {node["class_type"] for node in g.values()})  # native shift 5

    def test_graph_restores_the_exterior_through_hard_selection_support(self):
        g = workflow.build_fill(self.request(), "target.png", "mask.png", 7, workflow.Capabilities())
        w, h = workflow.moss_anypaint_size(1200, 800)
        self.assertEqual((g["image_scale"]["inputs"]["width"], g["image_scale"]["inputs"]["height"]), (w, h))
        self.assertEqual((g["support_scale"]["inputs"]["width"], g["support_scale"]["inputs"]["height"]), (w, h))
        self.assertEqual(g["selection_support"]["inputs"], {"mask": ["selection_mask", 0], "value": 0.0})
        self.assertEqual((g["out_scale"]["inputs"]["width"], g["out_scale"]["inputs"]["height"]), (1200, 800))
        composite = g["selection_composite"]["inputs"]
        self.assertEqual(
            (composite["destination"], composite["source"], composite["mask"]),
            (["load_image", 0], ["out_scale", 0], ["selection_support", 0]),
        )
        self.assertEqual(g["save"]["inputs"]["images"], ["selection_composite", 0])

    def test_variations_use_one_sampler_per_seed(self):
        g = workflow.build_fill(self.request(batch=3), "target.png", "mask.png", 41, workflow.Capabilities())
        self.assertEqual([g[n]["inputs"]["seed"] for n in ("sampler", "sampler_2", "sampler_3")], [41, 42, 43])
        self.assertEqual(g["latents_3"]["inputs"]["samples1"], ["latents_2", 0])
        self.assertEqual(g["decode"]["inputs"]["samples"], ["latents_3", 0])
        self.assertEqual(g["source_batch"]["inputs"], {"image": ["load_image", 0], "amount": 3})
        self.assertEqual(workflow.total_steps(g), 24)

    def test_references_and_empty_descriptions_are_refused(self):
        self.assertEqual(edit_reference_options(MOSS_ANYPAINT_TEMPLATE), (0, ""))
        with self.assertRaisesRegex(ValueError, "does not support reference images"):
            workflow.build_fill(
                self.request(refs=(b"ref",)),
                "target.png",
                "mask.png",
                7,
                workflow.Capabilities(),
                reference_refs=("reference.png",),
            )
        with self.assertRaisesRegex(ValueError, "Describe the finished image"):
            workflow.build_fill(self.request(prompt="  "), "target.png", "mask.png", 7, workflow.Capabilities())
        with self.assertRaisesRegex(ValueError, "DMD2"):
            workflow.build_fill(
                InpaintRequest(b"i", b"m", 64, 64, 64, 64, "x", "", ModelSpec(template=MOSS_ANYPAINT_TEMPLATE)),
                "t.png",
                "m.png",
                1,
                workflow.Capabilities(),
            )

    def test_text_to_image_is_not_offered(self):
        from openfill.backend import Txt2ImgRequest

        with self.assertRaises(ValueError):
            workflow.build_txt2img(Txt2ImgRequest(512, 512, "x", "", self.spec), 1)
        self.assertIn(MOSS_ANYPAINT_TEMPLATE, workflow.SELECTION_EDIT_TEMPLATES)


class AppSettingsTests(unittest.TestCase):
    def test_sampling_and_profile_ignore_saved_general_settings(self):
        host = SimpleNamespace(
            settings=SimpleNamespace(steps=40, cfg=7.0, sampler="dpmpp_2m", scheduler="karras", denoise=0.6)
        )
        spec = GraphTests.spec
        sampling = App.sampling(host, spec, 2, 9)
        self.assertEqual(
            (sampling.steps, sampling.cfg, sampling.sampler, sampling.scheduler, sampling.denoise, sampling.batch),
            (8, 1.0, "euler", "simple", 1.0, 2),
        )
        profile = App.profile_for(host, spec)
        self.assertEqual((profile.name, profile.multiple, profile.max_side), ("moss_anypaint", 16, 1024))


if __name__ == "__main__":
    unittest.main()
