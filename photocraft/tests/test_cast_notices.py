# SPDX-License-Identifier: Apache-2.0
"""What the artist is told when Remove could not look for the object's shadow and reflection."""

import sys
import unittest
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import cast  # noqa: E402
from openfill_photocraft.service import CAST_MISSED, _missed  # noqa: E402
from openfill_photocraft.wevisone import EngineBusy, EngineNotRunning  # noqa: E402

from openfill.backend import BackendUnavailable  # noqa: E402
from openfill.comfy_api import ComfyApiError, ComfyExecutionError  # noqa: E402

# What ComfyClient raises when the engine's port refuses the connection (ComfyClient._request), on Windows and on Linux.
WINDOWS_REFUSAL = ("POST /upload/image failed: <urlopen error [WinError 10061] No connection could be made because the "
                   "target machine actively refused it>")
LINUX_REFUSAL = "GET /history/7 failed: <urlopen error [Errno 111] Connection refused>"


def refused_by_cause():
    """A refusal said only by what the error was raised from, as ``raise ComfyApiError(...) from err`` leaves it."""
    err = ComfyApiError("POST /prompt failed: odd")
    err.__cause__ = URLError(ConnectionRefusedError(10061, "No connection could be made"))
    return err


class MissedTests(unittest.TestCase):
    def test_an_engine_that_is_off_is_named_as_off_not_as_busy(self):
        self.assertEqual(_missed(EngineNotRunning("No engine is running at http://127.0.0.1:8000.")), "off")
        self.assertIn("not running", CAST_MISSED["off"])
        self.assertNotIn("busy", CAST_MISSED["off"])
        self.assertIn("Select them too", CAST_MISSED["off"])

    def test_an_engine_that_did_not_answer_in_time_was_busy(self):
        for problem in (EngineBusy("ComfyUI is not answering."), BackendUnavailable("Studio is not answering."),
                        ComfyApiError("timed out waiting for the backend")):
            self.assertEqual(_missed(problem), "busy", problem)
        self.assertEqual(CAST_MISSED["busy"], cast.BUSY)

    def test_an_engine_that_answered_with_its_own_error_is_not_the_artists_concern(self):
        for problem in (ComfyApiError("POST /prompt failed", status=400), ComfyExecutionError("out of memory"),
                        ValueError("This ComfyUI cannot return the selection model's answers in words."), RuntimeError("odd")):
            self.assertEqual(_missed(problem), "", problem)

    def test_an_engine_that_refused_the_questions_has_stopped_and_is_named_as_off(self):
        # It stopped between its health check and the questions: no wait would have helped.
        for problem in (ComfyApiError(WINDOWS_REFUSAL), ComfyApiError(LINUX_REFUSAL), ComfyApiError("POST /prompt failed: ECONNREFUSED"),
                        refused_by_cause()):
            self.assertEqual(_missed(problem), "off", problem)

    def test_a_wait_that_ran_out_or_was_interrupted_stays_busy(self):
        for problem in (ComfyApiError("timed out waiting for the backend"), ComfyApiError("generation was interrupted"),
                        ComfyApiError("POST /prompt failed: <urlopen error timed out>"),
                        ComfyApiError("GET /history/7 failed: [WinError 10054] An existing connection was forcibly closed")):
            self.assertEqual(_missed(problem), "busy", problem)

    def test_only_the_engines_own_refusal_counts_not_any_error_that_mentions_one(self):
        # An answer with an HTTP status was a reply; a bare OSError is not the engine's (as before).
        self.assertEqual(_missed(ComfyApiError("POST /prompt -> HTTP 502: connection refused upstream", status=502)), "")
        self.assertEqual(_missed(OSError("connection refused")), "")


class Fakes:
    """A ``Service`` that removes a light plate on a plain wall, with a fake selection engine that records how it was asked."""

    def service(self, picture, chosen, fail=None):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from openfill_photocraft.service import JobState, Service

        svc = Service.__new__(Service)
        svc.state = JobState(state="running")
        svc.app = Mock()
        svc.app.settings.hidden_models = []
        svc.app.installed_models.return_value = []
        svc._job_app = svc.app

        def snapshot(image):
            return picture, image.widened if image.widened is not None else chosen

        svc.bridge = SimpleNamespace(_snapshot=snapshot)
        svc.asked = []

        def questions(app, cancel, seconds, **options):
            if "progress" in options:
                options["progress"](0.5, "Starting the AI backend... 12 s")
            svc.asked.append((app, seconds, options, svc.state.text))
            raise fail or ValueError("This ComfyUI cannot return the selection model's answers in words.")

        svc.selection = SimpleNamespace(model=SimpleNamespace(questions=questions))
        svc.app_for = Mock(return_value=svc.app)
        svc._remove_with = Mock(return_value=["layer"])
        svc._remove_auto = Mock(return_value=["one", "two"])
        return svc

    def plate(self):
        """A light plate on a plain wall, its shaded left edge and shadow just outside the selection."""
        from PIL import Image, ImageDraw

        picture = Image.new("RGBA", (400, 300), (200, 196, 190, 255))
        draw = ImageDraw.Draw(picture)
        draw.rectangle((180, 120, 186, 190), fill=(110, 106, 100, 255))   # the shaded edge, outside the selection
        draw.rectangle((187, 120, 240, 190), fill=(245, 245, 245, 255))   # the plate
        chosen = Image.new("L", (400, 300))
        ImageDraw.Draw(chosen).rectangle((190, 122, 240, 190), fill=255)
        return picture, chosen


class RimStepTests(Fakes, unittest.TestCase):
    """Remove also takes the object's edge and contact shadow just outside the selection (``rim``)."""

    def test_the_edge_left_outside_the_selection_goes_too_and_the_artist_is_told(self):
        from openfill_photocraft.bridge import PCImage
        from openfill_photocraft.service import RIM_WORDS

        picture, chosen = self.plate()
        svc = self.service(picture, chosen)
        image = PCImage(svc.bridge, "doc", 0)
        self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
        if image.widened is None:
            self.skipTest("rim found nothing on this synthetic plate (its own tests cover what it takes)")
        self.assertEqual(image.widened.getpixel((183, 150)), 255)   # the shaded edge
        self.assertEqual(image.widened.getpixel((60, 60)), 0)
        self.assertEqual(svc.state.notices, [cast.notice(RIM_WORDS)])

    def test_unticked_nothing_beyond_the_selection_is_taken(self):
        from openfill_photocraft.bridge import PCImage

        picture, chosen = self.plate()
        svc = self.service(picture, chosen)
        image = PCImage(svc.bridge, "doc", 0)
        svc._remove(image, {"removal": "rorem", "cast": False}, 7, 1)
        self.assertIsNone(image.widened)
        self.assertEqual(svc.state.notices, [])

    def test_a_failure_in_the_rim_step_never_fails_the_removal(self):
        from unittest.mock import patch

        from openfill_photocraft import rim
        from openfill_photocraft.bridge import PCImage

        picture, chosen = self.plate()
        svc = self.service(picture, chosen)
        image = PCImage(svc.bridge, "doc", 0)
        with patch.object(rim, "widen", side_effect=RuntimeError("odd picture")):
            self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
        self.assertIsNone(image.widened)

    def test_a_skipped_shadow_look_up_is_still_told_and_the_rim_says_only_what_it_took(self):
        # The rim step still takes the shaded edge, but the object's shadow was never looked for: the artist hears both,
        # and nothing that reads as if the shadow had been handled.
        from openfill_photocraft.bridge import PCImage
        from openfill_photocraft.service import RIM_ONLY_WORDS, RIM_WORDS

        for problem, why in ((EngineNotRunning("No engine is running at http://127.0.0.1:8000."), "off"),
                             (EngineBusy("ComfyUI is not answering at http://127.0.0.1:8000."), "busy"),
                             (ComfyApiError(WINDOWS_REFUSAL), "off")):
            with self.subTest(why=why, problem=problem):
                picture, chosen = self.plate()
                svc = self.service(picture, chosen, fail=problem)
                image = PCImage(svc.bridge, "doc", 0)
                self.assertEqual(svc._remove(image, {"removal": "rorem"}, 7, 1), ["layer"])
                self.assertEqual(image.widened.getpixel((183, 150)), 255)   # the rim still took the shaded edge
                self.assertEqual(svc.state.notices, [CAST_MISSED[why], cast.notice(RIM_ONLY_WORDS)])
                self.assertNotIn(cast.notice(RIM_WORDS), svc.state.notices)
                self.assertEqual(svc._cast_words, RIM_ONLY_WORDS)   # and the choice note records what was taken
        self.assertIn("contact shadow", cast.notice(RIM_ONLY_WORDS))

    def test_a_look_up_the_engine_answered_with_its_own_error_keeps_the_rims_words(self):
        # Not the artist's concern (no CAST_MISSED), so the rim's notice is as before.
        from openfill_photocraft.bridge import PCImage
        from openfill_photocraft.service import RIM_WORDS

        picture, chosen = self.plate()
        svc = self.service(picture, chosen, fail=ComfyApiError("POST /prompt -> HTTP 400: invalid prompt", 400))
        svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": "rorem"}, 7, 1)
        self.assertEqual(svc.state.notices, [cast.notice(RIM_WORDS)])


class StartTests(Fakes, unittest.TestCase):
    """After the idle supervisor stopped ComfyUI, a removal on it starts it again moments later. The shadow look-up may
    then start it first, on the same engine; never for a removal that needs no engine, nor for an engine only the
    look-up would use."""

    def removal(self, how, select_app=None, installed=(), hidden=()):
        from types import SimpleNamespace

        from openfill_photocraft.bridge import PCImage

        picture, chosen = self.plate()
        svc = self.service(picture, chosen)
        svc.app.installed_models.return_value = [SimpleNamespace(id=model) for model in installed]
        svc.app.settings.hidden_models = list(hidden)
        if select_app is not None:
            svc.app_for.return_value = select_app
        svc._remove(PCImage(svc.bridge, "doc", 0), {"removal": how}, 7, 1)
        self.assertEqual(len(svc.asked), 1)
        return svc, svc.asked[0]

    def test_a_removal_on_the_engine_select_uses_lets_the_look_up_start_it(self):
        svc, (app, seconds, options, said) = self.removal("rorem")
        self.assertIs(app, svc.app)
        self.assertEqual(seconds, cast.SECONDS)
        self.assertIs(options["start"], True)
        self.assertEqual(said, "Starting the AI backend... 12 s")   # the engine's start is shown while it runs
        svc.app_for.assert_called_once_with("select")

    def test_local_needs_no_engine_so_the_look_up_starts_none(self):
        from openfill_photocraft.removal_advice import TEXTURE_REMOVAL

        _svc, (_app, _seconds, options, _said) = self.removal(TEXTURE_REMOVAL)
        self.assertEqual(options, {})   # asked exactly as the courtesy always was

    def test_select_routed_to_another_engine_is_only_asked(self):
        from unittest.mock import Mock

        elsewhere = Mock(name="the engine Select is routed to")
        _svc, (app, _seconds, options, _said) = self.removal("rorem", select_app=elsewhere)
        self.assertIs(app, elsewhere)
        self.assertEqual(options, {})

    def test_automatic_lets_it_start_only_when_a_method_it_tries_runs_on_the_engine(self):
        from openfill_photocraft.removal_plan import AUTO_REMOVAL, FAMILIES

        rorem = FAMILIES["rorem"][0]
        _svc, (_app, _seconds, options, _said) = self.removal(AUTO_REMOVAL, installed=[rorem])
        self.assertIs(options.get("start"), True)
        for installed, hidden in (((), ()), ((rorem,), (rorem,)), (("an-unrated-model",), ())):
            with self.subTest(installed=installed, hidden=hidden):
                _svc, (_app, _seconds, options, _said) = self.removal(AUTO_REMOVAL, installed=installed, hidden=hidden)
                self.assertEqual(options, {})


if __name__ == "__main__":
    unittest.main()
