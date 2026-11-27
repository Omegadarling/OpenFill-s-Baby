# SPDX-License-Identifier: Apache-2.0
"""ComfyUI's image loader runs out of address space after many pictures; OpenFill clears its cache and tries once more."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import wevisone  # noqa: E402

from openfill.backend import OutOfMemory, WorkflowRejected  # noqa: E402
from openfill.backends.comfy import ComfyBackend  # noqa: E402
from openfill.comfy_api import ComfyClient, ComfyExecutionError  # noqa: E402

RESHAPE = "cannot reshape array of size 117876 into shape (517,304,3)"
GRAPH = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}


def exhausted(message=RESHAPE, node="LoadImage"):
    return ComfyExecutionError(message, "ValueError", node, "video_types.py, line 165, in append")


class SignTests(unittest.TestCase):
    def test_the_loaders_two_messages_are_recognised(self):
        self.assertTrue(exhausted().is_loader_exhausted)
        self.assertTrue(exhausted("ValueError: buffer size must be a multiple of element size").is_loader_exhausted)
        self.assertTrue(exhausted(node="LoadImageOutput").is_loader_exhausted)
        self.assertTrue(ComfyExecutionError(RESHAPE).is_loader_exhausted)   # read from the history: the node is not named

    def test_other_failures_are_not_mistaken_for_it(self):
        self.assertFalse(exhausted(node="KSampler").is_loader_exhausted)
        self.assertFalse(exhausted("CUDA out of memory").is_loader_exhausted)
        self.assertFalse(exhausted("Invalid image file: a.png").is_loader_exhausted)

    def test_clearing_the_cache_keeps_the_models_loaded(self):
        client = ComfyClient("http://engine")
        client._json = Mock()
        client.clear_cache(settle=0)
        client._json.assert_called_once_with("POST", "/free", {"unload_models": False, "free_memory": True})


class BackendTests(unittest.TestCase):
    def backend(self, *outcomes):
        client = Mock()
        client.queue_prompt.side_effect = [f"prompt-{n}" for n in range(1, len(outcomes) + 1)]
        client.wait_for.side_effect = list(outcomes)
        client.output_images.return_value = [b"png"]
        return ComfyBackend(client=client, request_timeout=30), client

    def test_a_job_that_hits_it_clears_the_cache_and_runs_again(self):
        backend, client = self.backend(exhausted(), {"outputs": {}})
        said = []
        self.assertEqual(backend._run(GRAPH, 4, 1, 1_000_000, lambda fraction, text: said.append(text)), [b"png"])
        client.clear_cache.assert_called_once_with()
        self.assertEqual(client.queue_prompt.call_count, 2)
        self.assertIn("Clearing the engine's picture cache...", said)

    def test_it_is_tried_once_more_and_no_more(self):
        backend, client = self.backend(exhausted(), exhausted())
        with self.assertRaisesRegex(WorkflowRejected, "LoadImage: cannot reshape"):
            backend._run(GRAPH, 4, 1, 1_000_000, None)
        self.assertEqual(client.queue_prompt.call_count, 2)
        client.clear_cache.assert_called_once_with()

    def test_other_failures_are_reported_as_before(self):
        backend, client = self.backend(ComfyExecutionError("CUDA out of memory", "OutOfMemoryError", "KSampler"))
        with self.assertRaises(OutOfMemory):
            backend._run(GRAPH, 4, 1, 1_000_000, None)
        backend, client = self.backend(exhausted(node="KSampler"))
        with self.assertRaisesRegex(WorkflowRejected, "KSampler"):
            backend._run(GRAPH, 4, 1, 1_000_000, None)
        client.clear_cache.assert_not_called()
        self.assertEqual(client.queue_prompt.call_count, 1)

    def test_a_cache_that_cannot_be_cleared_reports_the_first_failure(self):
        from openfill.comfy_api import ComfyApiError

        backend, client = self.backend(exhausted())
        client.clear_cache.side_effect = ComfyApiError("connection refused")
        with self.assertRaisesRegex(WorkflowRejected, "cannot reshape"):
            backend._run(GRAPH, 4, 1, 1_000_000, None)
        self.assertEqual(client.queue_prompt.call_count, 1)


class SelectionTests(unittest.TestCase):
    def client(self, *outcomes):
        client = Mock()
        client.base_url = "http://engine"
        client.object_info.return_value = {"PreviewAny": {}}
        client.upload_image.return_value = "openfill/selection/a.png"
        client.queue_prompt.side_effect = [f"prompt-{n}" for n in range(1, len(outcomes) + 1)]
        client.wait_for.side_effect = list(outcomes)
        return client

    def test_a_question_that_hits_it_is_asked_again(self):
        client = self.client(exhausted(), {"outputs": {"6": {"text": ["<ref>A cup</ref>"]}}})
        ask, _segment = wevisone.questions(client, "enc")
        self.assertEqual(ask(Image.new("RGB", (40, 30)), "What is this?"), "<ref>A cup</ref>")
        client.clear_cache.assert_called_once_with()
        client.cancel_prompt.assert_not_called()
        self.assertEqual(client.upload_image.call_count, 1)

    def test_a_question_that_hits_it_twice_fails(self):
        client = self.client(exhausted(), exhausted())
        ask, _segment = wevisone.questions(client, "enc")
        with self.assertRaises(ComfyExecutionError):
            ask(Image.new("RGB", (40, 30)), "What is this?")
        self.assertEqual(client.queue_prompt.call_count, 2)

    def test_object_selection_clears_the_cache_and_asks_again(self):
        client = self.client(exhausted(), {"outputs": {"5": {"images": []}}})
        helper = wevisone.WeVisOne.__new__(wevisone.WeVisOne)
        helper.cache_key, helper.cached_masks = None, ()
        masks = helper._instances(client, "enc", Image.new("RGB", (40, 30)), "the cup", None, lambda: False)
        self.assertEqual(masks, [])
        client.clear_cache.assert_called_once_with()
        self.assertEqual(client.queue_prompt.call_count, 2)

    def test_object_selection_reports_any_other_failure_at_once(self):
        client = self.client(exhausted(node="WeVisOneDetect"))
        helper = wevisone.WeVisOne.__new__(wevisone.WeVisOne)
        helper.cache_key, helper.cached_masks = None, ()
        with self.assertRaises(ComfyExecutionError):
            helper._instances(client, "enc", Image.new("RGB", (40, 30)), "the cup", None, lambda: False)
        client.clear_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
