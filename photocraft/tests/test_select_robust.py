# SPDX-License-Identifier: Apache-2.0
"""Select keeps working when the artist's input or the engine is not what it expected: a description left in the
field, Subtract with attached parts, an engine that is off or busy (also behind an SSH tunnel), a hazy matte, a hole's
soft edge, a click beside a thin wire or just outside a large object."""

import socket
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.error import URLError

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import comfy_connection, engine_routes, wevisone  # noqa: E402
from openfill_photocraft.engine_routes import ExternalServer, RoutedSettings  # noqa: E402
from openfill_photocraft.selection import Selection  # noqa: E402
from openfill_photocraft.wevisone import (  # noqa: E402
    DEFAULT_QUERY,
    EngineBusy,
    EngineNotRunning,
    NothingFound,
    WeVisOne,
    choose_matte,
    clean_matte,
)

from openfill.backend import BackendUnavailable, GenerationCancelled  # noqa: E402
from openfill.comfy_api import ComfyApiError, ComfyExecutionError  # noqa: E402
from openfill.config import Paths, Settings  # noqa: E402
from openfill.server import ServerManager  # noqa: E402
from openfill.sources import Source  # noqa: E402

STUDIO = Source("studio", "Studio", "http://127.0.0.1:8000")   # a saved server reached at its own address
SIZE = (400, 300)
CUP = (60, 60, 140, 160)
SPOON = (250, 120, 300, 140)


def matte(*boxes, size=SIZE, fill=255):
    mask = Image.new("L", size)
    for box in boxes:
        ImageDraw.Draw(mask).rectangle(box, fill=fill)
    return mask


def selection_run(model, params, has_selection=False):
    """``Selection.run`` with ``model`` and a fake PhotoCraft; the message and the alpha it loads."""
    doc = {"name": "doc", "width": SIZE[0], "height": SIZE[1], "revision": 3, "hasSelection": has_selection, "history": []}
    selection = Selection.__new__(Selection)
    selection.source = Image.new("RGB", SIZE)
    selection.key, selection.matte_key, selection.matte = "key", "", None
    selection.model = model
    loaded = {}
    bridge = Mock()
    bridge._open.side_effect = lambda guide, stem: (loaded.update(alpha=guide.getchannel("A")), (7, 70))[1]
    bridge.session.return_value = {"active": 7}
    bridge.client.call.return_value = {"document": doc, "session": {"active": 0}}
    bridge.client.run.return_value = doc
    selection.bridge = bridge
    from openfill_photocraft import selection as module

    said = []
    with patch.object(module, "key_for", lambda index, d: "key"):
        message = selection.run(Mock(), {"key": "key", "operation": "replace", **params}, lambda _f, text: said.append(text), lambda: False, lambda: None)
    return message, loaded["alpha"], said


class StaleDescriptionTests(unittest.TestCase):
    """The description field keeps its text after Enter. A later click or box on something else still selects it."""

    def model(self, first=None):
        calls = []

        def mask(app, source, query, point, progress, cancel, *, box=None):
            calls.append((query, point, box))
            if query == "the cup":
                if first is not None:
                    raise first
                return matte(CUP)
            return matte(SPOON)

        return SimpleNamespace(mask=mask, whole=lambda app, source, chosen, described, cancel: chosen), calls

    def test_a_click_on_something_else_selects_it_and_says_the_description_was_not_used(self):
        model, calls = self.model(first=NothingFound("No object found at that point. Click inside it or describe what to select."))
        message, alpha, said = selection_run(model, {"action": "click", "point": [0.69, 0.43], "query": "the cup"})
        self.assertEqual(calls, [("the cup", [0.69, 0.43], None), (DEFAULT_QUERY, [0.69, 0.43], None)])
        self.assertEqual((alpha.getpixel((270, 130)), alpha.getpixel((100, 100))), (255, 0))
        self.assertEqual(message, "Object selected. Nothing there matched the description, so it was not used.")
        self.assertIn("Nothing there matches the description. Looking for any object…", said)

    def test_a_box_around_something_else_selects_it(self):
        box = [0.6, 0.35, 0.8, 0.5]
        model, calls = self.model(first=NothingFound("No object found in that box. Draw a box around the whole object or describe it."))
        message, alpha, _said = selection_run(model, {"action": "box", "box": box, "query": "the cup"})
        self.assertEqual(calls, [("the cup", None, box), (DEFAULT_QUERY, None, box)])
        self.assertEqual(alpha.getpixel((270, 130)), 255)
        self.assertIn("not used", message)

    def test_a_click_on_the_described_thing_keeps_to_the_description(self):
        model, calls = self.model()
        message, alpha, _said = selection_run(model, {"action": "click", "point": [0.25, 0.35], "query": "the cup"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(alpha.getpixel((100, 100)), 255)
        self.assertEqual(message, "Object selected.")

    def test_a_description_alone_that_matches_nothing_says_so(self):
        model, calls = self.model(first=NothingFound("No matching object found. Try a more specific description."))
        with self.assertRaisesRegex(ValueError, "No matching object found"):
            selection_run(model, {"action": "describe", "query": "the cup"})
        self.assertEqual(len(calls), 1)

    def test_an_engine_error_is_not_retried_without_the_description(self):
        model, calls = self.model(first=ValueError("The selection mask does not match the image dimensions."))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            selection_run(model, {"action": "click", "point": [0.69, 0.43], "query": "the cup"})
        self.assertEqual(len(calls), 1)

    def test_the_selection_model_says_nothing_found_for_a_description_that_is_not_at_the_click(self):
        # The real WeVisOne.mask, with a model that finds only the cup for "the cup" and both things for the list.
        helper = WeVisOne.__new__(WeVisOne)
        helper._client = Mock(return_value=Mock())
        helper._instances = lambda client, name, frame, query, progress, cancel, *, cache=True: (
            [matte(CUP)] if query == "the cup" else [matte(CUP), matte(SPOON)]
        )
        with patch.object(wevisone, "encoder", lambda client: "enc"):
            with self.assertRaises(NothingFound):
                helper.mask(Mock(), Image.new("RGB", SIZE), "the cup", (0.69, 0.43), lambda *a: None, lambda: False)
            found = helper.mask(Mock(), Image.new("RGB", SIZE), DEFAULT_QUERY, (0.69, 0.43), lambda *a: None, lambda: False)
        self.assertEqual(found.getpixel((270, 130)), 255)


class AttachedPartsTests(unittest.TestCase):
    def model(self, missed=False):
        asked = []

        def whole(app, source, chosen, described, cancel):
            asked.append(described)
            return chosen

        return SimpleNamespace(mask=lambda *a, **k: matte(SPOON), whole=whole, parts_missed=missed), asked

    def test_subtract_leaves_the_parts_out_of_the_trim(self):
        model, asked = self.model()
        message, _alpha, _said = selection_run(model, {"action": "click", "point": [0.69, 0.43], "operation": "subtract"}, has_selection=True)
        self.assertEqual(asked, [])
        self.assertEqual(message, "Subtracted from the selection.")

    def test_add_and_replace_still_look_for_the_parts(self):
        for operation in ("add", "replace"):
            model, asked = self.model()
            selection_run(model, {"action": "click", "point": [0.69, 0.43], "operation": operation}, has_selection=True)
            self.assertEqual(asked, [""])

    def test_a_busy_engine_is_mentioned_in_the_done_line(self):
        model, _asked = self.model(missed=True)
        message, _alpha, _said = selection_run(model, {"action": "click", "point": [0.69, 0.43]})
        self.assertEqual(message, "Object selected. The engine was too busy to look for attached parts.")

    def engine(self, fail):
        helper = WeVisOne.__new__(WeVisOne)
        helper._courtesy_client = Mock(side_effect=fail)
        return helper

    def test_a_look_up_that_fails_is_logged_and_the_selection_stands(self):
        chosen = matte(SPOON)
        for problem, busy in (
            (EngineBusy("Studio is not answering at http://engine."), True),
            (ComfyApiError("timed out waiting for the backend"), True),
            (EngineNotRunning("No engine is running at http://127.0.0.1:8000."), False),
            (ComfyApiError("POST /prompt -> HTTP 400: invalid prompt", 400), False),
            (ComfyExecutionError("Allocation on device", exception_type="torch.OutOfMemoryError"), False),
            (ValueError("This ComfyUI cannot return the selection model's answers in words."), False),
        ):
            with self.subTest(problem=problem):
                app = Mock()
                helper = self.engine(problem)
                self.assertIs(helper.whole(app, Image.new("RGB", SIZE), chosen, "", lambda: False), chosen)
                self.assertIs(helper.parts_missed, busy)
                app.log.info.assert_called_once_with("parts: skipped (%s)", problem)

    def test_an_engine_that_refused_the_questions_was_not_busy(self):
        # It stopped between its health check and the questions; a wait that ran out or was interrupted stays busy.
        caused = ComfyApiError("POST /prompt failed: odd")
        caused.__cause__ = URLError(ConnectionRefusedError(111, "Connection refused"))
        for problem, busy in (
            (ComfyApiError("POST /upload/image failed: <urlopen error [WinError 10061] No connection could be made because "
                           "the target machine actively refused it>"), False),
            (ComfyApiError("GET /history/7 failed: <urlopen error [Errno 111] Connection refused>"), False),
            (caused, False),
            (ComfyApiError("generation was interrupted"), True),
            (ComfyApiError("POST /prompt failed: <urlopen error timed out>"), True),
        ):
            with self.subTest(problem=problem):
                helper = self.engine(problem)
                helper.whole(Mock(), Image.new("RGB", SIZE), matte(SPOON), "", lambda: False)
                self.assertIs(helper.parts_missed, busy)

    def test_a_refusal_is_told_by_what_an_error_was_raised_from_not_by_what_was_being_handled(self):
        self.assertTrue(wevisone.refused(ConnectionRefusedError(10061, "refused")))
        try:
            try:
                raise ConnectionRefusedError(10061, "refused")
            except ConnectionRefusedError:
                raise ComfyApiError("timed out waiting for the backend")  # noqa: B904 - the implicit context is the point
        except ComfyApiError as err:
            self.assertFalse(wevisone.refused(err))

    def test_a_look_up_that_runs_is_not_missed(self):
        helper = WeVisOne.__new__(WeVisOne)
        helper.parts_missed = True   # from the selection before
        helper._client = Mock(return_value=Mock())
        with patch.object(wevisone, "questions", lambda *a: ("ask", "segment")), patch.object(wevisone, "encoder", lambda c: "enc"), \
                patch("openfill_photocraft.whole.complete", lambda *a: None):
            helper.whole(Mock(), Image.new("RGB", SIZE), matte(SPOON), "", lambda: False)
        self.assertIs(helper.parts_missed, False)


class CourtesyEngineTests(unittest.TestCase):
    """A courtesy question (a shadow, attached parts) never starts an engine and never waits for one that is not
    running; one that is running but slow is asked again for a few seconds."""

    def ask(self, app, taken=True, seconds=30.0, **options):
        tries, slept, now = [], [], [0.0]

        def monotonic():
            now[0] += 5.0
            return now[0]

        clock = SimpleNamespace(monotonic=monotonic, sleep=slept.append)
        real_client = wevisone.WeVisOne._client

        def counted(helper, *args, **kwargs):
            tries.append(kwargs)
            return real_client(helper, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder, patch.object(wevisone, "time", clock), \
                patch.object(wevisone, "_taken", lambda url: taken), patch.object(wevisone.WeVisOne, "_client", counted):
            try:
                return wevisone.WeVisOne(folder)._courtesy_client(app, lambda: False, seconds, **options), tries, slept
            except BackendUnavailable as err:
                return err, tries, slept

    def app(self, ensure_running=None, healthy=False, **settings):
        server = Mock()
        server.url.return_value = "http://127.0.0.1:8000"
        server.is_healthy.return_value = healthy
        server.ensure_running.side_effect = ensure_running or AssertionError("a courtesy question must not start an engine")
        values = {"backend": "comfyui", "remote_fibo": True, "autostart": False, **settings}
        return SimpleNamespace(selection_helper=False, settings=SimpleNamespace(**values), server=server)

    def test_an_engine_that_is_not_running_is_given_up_at_once(self):
        app = self.app(ensure_running=BackendUnavailable("Studio is not answering at http://127.0.0.1:8000."))
        result, tries, slept = self.ask(app, taken=False)
        self.assertIsInstance(result, EngineNotRunning)
        self.assertEqual((len(tries), slept), (1, []))
        app.server.ensure_running.assert_not_called()

    def test_a_busy_engine_is_asked_again_then_given_up_as_busy(self):
        app = self.app(ensure_running=BackendUnavailable("Studio is not answering at http://127.0.0.1:8000."))
        result, tries, slept = self.ask(app, taken=True)
        self.assertIsInstance(result, EngineBusy)
        self.assertNotIsInstance(result, EngineNotRunning)
        self.assertEqual((len(tries), slept), (3, [1.0, 1.0]))

    def test_a_managed_comfyui_is_never_started_for_a_courtesy(self):
        app = self.app(remote_fibo=False, autostart=True, healthy=True)
        (client, _left), _tries, _slept = self.ask(app)
        self.assertEqual(client.base_url, "http://127.0.0.1:8000")
        app.server.ensure_running.assert_not_called()
        app = self.app(remote_fibo=False, autostart=True, healthy=False)
        result, _tries, _slept = self.ask(app, taken=True)
        self.assertIsInstance(result, EngineBusy)
        app.server.ensure_running.assert_not_called()
        result, _tries, _slept = self.ask(app, taken=False)
        self.assertIsInstance(result, EngineNotRunning)

    def test_an_engine_behind_a_tunnel_that_does_not_answer_is_busy_not_broken(self):
        app = self.app(ensure_running=RuntimeError("The SSH connection to Studio is open. Start ComfyUI there and use Engine > Check."))
        result, tries, _slept = self.ask(app, taken=True)
        self.assertIsInstance(result, EngineBusy)
        self.assertIn("Studio", str(result))
        self.assertEqual(len(tries), 3)

    def test_the_local_selection_engine_not_started_is_not_running_and_a_silent_one_is_busy(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "selection-engine.json").write_text('{"port": 9, "comfy_dir": ".", "data_dir": ".", "python": "."}', encoding="utf-8")
            helper = wevisone.WeVisOne(folder)
            app = SimpleNamespace(selection_helper=True)
            with patch.object(wevisone.ComfyClient, "is_alive", lambda self, timeout=3.0: False):
                with patch.object(wevisone, "_taken", lambda url: False), self.assertRaises(EngineNotRunning) as raised:
                    helper._courtesy_client(app, lambda: False, 30.0)
                self.assertIsInstance(raised.exception, ValueError)   # as the helper's "not running" always was
                clock = SimpleNamespace(monotonic=iter(range(0, 100, 5)).__next__, sleep=lambda s: None)
                with patch.object(wevisone, "_taken", lambda url: True), patch.object(wevisone, "time", clock), \
                        self.assertRaises(EngineBusy):
                    helper._courtesy_client(app, lambda: False, 30.0)
            self.assertIsNone(helper.process)

    def test_both_are_an_unavailable_engine_to_callers_that_ask_no_more(self):
        self.assertTrue(issubclass(EngineNotRunning, BackendUnavailable))
        self.assertTrue(issubclass(EngineBusy, BackendUnavailable))

    def test_a_port_is_taken_only_while_something_listens_on_it(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(wevisone._taken(f"http://127.0.0.1:{port}"))
        self.assertFalse(wevisone._taken(f"http://127.0.0.1:{port}"))
        self.assertTrue(wevisone._taken("not a url"))   # cannot tell: ask the server as before

    def test_a_plain_engine_on_this_computer_is_still_given_up_at_once(self):
        app = self.app(fibo_source="studio", comfy_servers=[asdict(STUDIO)])
        result, tries, slept = self.ask(app, taken=False)
        self.assertIsInstance(result, EngineNotRunning)
        self.assertEqual((len(tries), slept), (1, []))
        app.server.ensure_running.assert_not_called()

    def test_a_managed_comfyui_the_idle_supervisor_stopped_is_started_for_a_removal_that_starts_it_anyway(self):
        # The default local install: autostart on, this computer's own ComfyUI. The removal on it is next, so the
        # look-up starts it, with the usual start and its progress, and the questions get their whole time after.
        progress = Mock()
        app = self.app(remote_fibo=False, autostart=True, ensure_running=lambda progress, cancel: "http://127.0.0.1:8000")
        (client, left), tries, slept = self.ask(app, taken=False, start=True, progress=progress)
        self.assertEqual(client.base_url, "http://127.0.0.1:8000")
        app.server.ensure_running.assert_called_once()
        self.assertIs(app.server.ensure_running.call_args.args[0], progress)
        self.assertEqual([try_["start"] for try_ in tries], [False, True])   # asked as a courtesy first: started only when off
        self.assertEqual((left, slept), (30.0, []))

    def test_start_never_starts_an_engine_the_courtesy_only_asks(self):
        # Another computer, or a tunnel to one: nothing here starts it, so start changes nothing.
        app = self.app(fibo_source="studio", comfy_servers=[asdict(STUDIO)])
        result, tries, _slept = self.ask(app, taken=False, start=True)
        self.assertIsInstance(result, EngineNotRunning)
        self.assertEqual(len(tries), 1)
        app.server.ensure_running.assert_not_called()
        # The local selection engine is Select's alone; a removal never runs on it.
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "selection-engine.json").write_text('{"port": 9, "comfy_dir": ".", "data_dir": ".", "python": "."}', encoding="utf-8")
            helper = wevisone.WeVisOne(folder)
            with patch.object(wevisone.ComfyClient, "is_alive", lambda self, timeout=3.0: False), \
                    patch.object(wevisone, "_taken", lambda url: False), self.assertRaises(EngineNotRunning):
                helper._courtesy_client(SimpleNamespace(selection_helper=True), lambda: False, 30.0, start=True)
            self.assertIsNone(helper.process)

    def test_a_busy_managed_comfyui_is_waited_on_as_before_not_started_again(self):
        app = self.app(remote_fibo=False, autostart=True, healthy=False)
        result, tries, _slept = self.ask(app, taken=True, start=True)
        self.assertIsInstance(result, EngineBusy)
        self.assertEqual(len(tries), 3)
        app.server.ensure_running.assert_not_called()

    def test_a_managed_comfyui_that_cannot_be_started_is_not_running_and_a_cancel_cancels(self):
        app = self.app(remote_fibo=False, autostart=True,
                       ensure_running=BackendUnavailable("Port 8000 on 127.0.0.1 is taken by a program that is not ComfyUI."))
        result, _tries, _slept = self.ask(app, taken=False, start=True)
        self.assertIsInstance(result, EngineNotRunning)
        self.assertIn("taken by a program", str(result))
        app = self.app(remote_fibo=False, autostart=True, ensure_running=GenerationCancelled("Cancelled while starting."))
        with self.assertRaises(GenerationCancelled) as raised:
            self.ask(app, taken=False, start=True)
        self.assertNotIsInstance(raised.exception, BackendUnavailable)

    def test_the_removals_questions_pass_start_on(self):
        helper = WeVisOne.__new__(WeVisOne)
        helper._courtesy_client = Mock(return_value=("client", 30.0))
        progress = Mock()
        with patch.object(wevisone, "encoder", lambda client: "enc"), \
                patch.object(wevisone, "questions", lambda client, name, cancel, seconds: (client, name, seconds)):
            self.assertEqual(helper.questions("app", seconds=30.0, start=True, progress=progress), ("client", "enc", 30.0))
            helper.questions("app")
        self.assertEqual(helper._courtesy_client.call_args_list[0].kwargs, {"start": True, "progress": progress})
        self.assertEqual(helper._courtesy_client.call_args_list[1].kwargs, {"start": False, "progress": None})

    def knock(self, url, answers):
        """``_taken(url)`` where each connection attempt gets the next of ``answers`` (an exception, or None for a
        connection taken); the timeouts it knocked with."""
        timeouts, answers = [], iter(answers)

        def connect(address, timeout):
            timeouts.append(timeout)
            answer = next(answers)
            if answer is not None:
                raise answer
            return MagicMock()   # a socket, closed by the ``with``

        with patch.object(wevisone.socket, "create_connection", connect):
            return wevisone._taken(url), timeouts

    def test_another_computer_that_lost_one_connection_request_is_still_running(self):
        # Over Wi-Fi or a VPN one request can be lost: knocked on longer, and once more.
        far = wevisone.FAR_KNOCK
        for url in ("http://192.168.1.20:8188", "http://studio:8188", "https://engine.example:443"):
            with self.subTest(url=url):
                self.assertEqual(self.knock(url, [TimeoutError("timed out"), None]), (True, [far, far]))
                self.assertEqual(self.knock(url, [TimeoutError("timed out")] * 2), (False, [far, far]))
                # A refusal is the computer's answer that nothing listens there: no second knock.
                self.assertEqual(self.knock(url, [ConnectionRefusedError(10061, "refused")]), (False, [far]))
        self.assertGreater(far, wevisone.KNOCK)
        self.assertLess(far * wevisone.FAR_KNOCKS, wevisone.PATIENCE)

    def test_this_computer_is_knocked_on_once_and_briefly_as_before(self):
        for url in ("http://127.0.0.1:8188", "http://127.4.5.6:8188", "http://localhost:8188", "http://LocalHost:8188",
                    "http://[::1]:8188"):
            with self.subTest(url=url):
                self.assertEqual(self.knock(url, [TimeoutError("timed out"), None]), (False, [wevisone.KNOCK]))
                self.assertEqual(self.knock(url, [None]), (True, [wevisone.KNOCK]))


class TunnelTests(unittest.TestCase):
    """An engine behind an SSH tunnel (a saved server with an SSH alias, or one routed to Select): the port on this
    computer is the tunnel's. The courtesy asks as a selection would, which may open the tunnel, and tells a stopped
    engine from a busy one by how the health check failed."""

    TUNNEL = Source("faraway", "Faraway", "http://127.0.0.1:9", "faraway", 8188)
    BENCH = Source("bench", "Bench", "http://127.0.0.1:18197", "bench", 8197)
    SAVED = (asdict(TUNNEL), asdict(BENCH), asdict(STUDIO))

    def ask(self, app, taken, seconds=30.0):
        """``_courtesy_client`` with a clock that moves only when the engine is asked (``app.took``) or slept on."""
        tries, slept, now = [], [], [0.0]
        app.took = lambda spent: now.__setitem__(0, now[0] + spent)
        clock = SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: (slept.append(s), app.took(s)))
        real_client = wevisone.WeVisOne._client

        def counted(helper, *args, **kwargs):
            tries.append(kwargs)
            return real_client(helper, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder, patch.object(wevisone, "time", clock), \
                patch.object(wevisone, "_taken", lambda url: taken), patch.object(wevisone.WeVisOne, "_client", counted):
            try:
                return wevisone.WeVisOne(folder)._courtesy_client(app, lambda: False, seconds), tries, slept
            except BackendUnavailable as err:
                return err, tries, slept

    def shared(self, spent, source="faraway"):
        """The shared engine set to the saved server ``source``; its health check fails after ``spent`` seconds."""
        app = SimpleNamespace(selection_helper=False, server=Mock(spec=["url", "ensure_running"]),
                              settings=SimpleNamespace(backend="comfyui", remote_fibo=True, fibo_source=source, autostart=False,
                                                       comfy_servers=list(self.SAVED)))
        app.server.url.return_value = "http://127.0.0.1:9"

        def ensure_running(progress, cancel):
            app.took(spent)
            raise BackendUnavailable("The SSH connection to Faraway is open. Start ComfyUI there and use Engine > Check.")

        app.server.ensure_running.side_effect = ensure_running
        return app

    def routed(self, folder, connect):
        settings = RoutedSettings(SimpleNamespace(active_model="m"), self.TUNNEL, Path(folder) / "config.json")
        server = ExternalServer(settings, SimpleNamespace(), None, self.TUNNEL, Path(folder))
        app = SimpleNamespace(selection_helper=False, settings=settings, server=server)
        return app, patch.object(engine_routes, "ensure_connection", connect)

    def test_a_tunnel_this_session_has_not_opened_is_opened_as_a_selection_would(self):
        opened = []
        with tempfile.TemporaryDirectory() as folder:
            app, connection = self.routed(folder, lambda url, profile, *, source_id=None: opened.append((url, source_id)))
            with connection:
                (client, _left), tries, slept = self.ask(app, taken=False)   # nothing on the port: the tunnel is closed
        self.assertEqual(client.base_url, self.TUNNEL.url)
        self.assertEqual((opened, len(tries), slept), ([(self.TUNNEL.url, "faraway")], 1, []))

    def test_a_tunnel_that_cannot_be_opened_is_not_running(self):
        def connect(url, profile, *, source_id=None):
            app.took(8.0)   # ssh's ConnectTimeout, then the tunnel it started is gone
            raise RuntimeError("Cannot connect to Faraway. Check that Faraway and its ComfyUI service are running; see faraway-ssh.log.")

        with tempfile.TemporaryDirectory() as folder:
            app, connection = self.routed(folder, connect)
            with connection:
                result, tries, slept = self.ask(app, taken=False)
        self.assertIsInstance(result, EngineNotRunning)
        self.assertIn("Cannot connect to Faraway", str(result))
        self.assertEqual((len(tries), slept), (1, []))

    def test_a_tunnel_opened_for_the_question_that_waited_its_whole_wait_is_busy(self):
        # ssh stayed up and the far engine never answered (a model swap); the tunnel was then closed again, so the port
        # is free before and after, as it is for a computer that cannot be reached.
        def connect(url, profile, *, source_id=None):
            app.took(wevisone.TUNNEL_WAIT + 2.0)   # the first health check, then the tunnel's whole wait
            raise RuntimeError("Cannot connect to Faraway. Check that Faraway and its ComfyUI service are running; see faraway-ssh.log.")

        with tempfile.TemporaryDirectory() as folder:
            app, connection = self.routed(folder, connect)
            with connection:
                result, tries, slept = self.ask(app, taken=False)
        self.assertIsInstance(result, EngineBusy)
        self.assertNotIsInstance(result, EngineNotRunning)
        self.assertEqual((len(tries), slept), (1, []))

    def test_the_whole_wait_rule_is_only_for_a_tunnel_opened_for_the_question(self):
        # A tunnel that was already open and failed slowly is left to the courtesy's own patience, as before.
        knocks = iter((True, True))
        app = self.shared(spent=wevisone.TUNNEL_WAIT + 1.0)
        app.took = lambda spent: None
        clock = SimpleNamespace(monotonic=iter((0.0, wevisone.TUNNEL_WAIT + 1.0)).__next__)
        with patch.object(wevisone, "_taken", lambda url: next(knocks)), patch.object(wevisone, "time", clock), \
                self.assertRaises(BackendUnavailable) as raised:
            wevisone._through_tunnel(app, "http://127.0.0.1:9", None, lambda: False)
        self.assertNotIsInstance(raised.exception, (EngineBusy, EngineNotRunning))

    def test_an_engine_that_refuses_through_an_open_tunnel_is_not_running(self):
        for source in ("faraway", "bench"):
            with self.subTest(source=source):
                app = self.shared(spent=0.2, source=source)   # ssh takes the connection and closes it at once
                result, tries, slept = self.ask(app, taken=True)
                self.assertIsInstance(result, EngineNotRunning)
                self.assertEqual((len(tries), slept), (1, []))

    def test_an_engine_that_runs_out_the_health_check_through_a_tunnel_is_busy(self):
        app = self.shared(spent=3.0)
        result, tries, slept = self.ask(app, taken=True)
        self.assertIsInstance(result, EngineBusy)
        self.assertNotIsInstance(result, EngineNotRunning)
        # Asked at 0, 4, 8 and 12 s on this clock, each check running out its 3 s: the same patience as before.
        self.assertEqual((len(tries), slept), (4, [1.0, 1.0, 1.0]))

    def test_a_stopped_engine_behind_a_real_open_tunnel_is_given_up_in_well_under_a_second(self):
        # A loopback listener that takes each connection and closes it, as ssh does when the far ComfyUI is stopped,
        # and openfill's own server manager for the shared engine set to a saved server behind that tunnel, which
        # this companion opened earlier (its ssh is still running).
        with socket.socket() as listener, tempfile.TemporaryDirectory() as folder:
            listener.bind(("127.0.0.1", 0))
            listener.listen(16)
            listener.settimeout(5)
            port = listener.getsockname()[1]
            stop = threading.Event()

            def close_each():
                while not stop.is_set():
                    try:
                        listener.accept()[0].close()
                    except OSError:
                        return

            threading.Thread(target=close_each, daemon=True).start()
            tunnel = Source("faraway", "Faraway", f"http://127.0.0.1:{port}", "faraway", 8188)
            paths = Paths.for_profile(folder)
            settings = Settings(remote_fibo=True, backend="comfyui", fibo_source="faraway", autostart=False,
                                comfy_servers=[asdict(tunnel)])
            settings.save(paths.config)
            app = SimpleNamespace(selection_helper=False, settings=settings, server=ServerManager(settings, paths, None))
            ssh = SimpleNamespace(poll=lambda: None)   # the forward this companion opened is still up
            started = time.monotonic()
            with patch.dict(comfy_connection._owned, {port: (("faraway", 8188), ssh)}), \
                    self.assertRaises(EngineNotRunning):
                wevisone.WeVisOne(folder)._courtesy_client(app, lambda: False, 30.0)
            stop.set()
        self.assertLess(time.monotonic() - started, wevisone.QUICK)

    def test_which_engines_are_behind_a_tunnel(self):
        def shared(source, remote=True):
            settings = SimpleNamespace(remote_fibo=remote, fibo_source=source, comfy_servers=list(self.SAVED))
            return SimpleNamespace(server=SimpleNamespace(), settings=settings)

        for source in ("faraway", "bench"):
            self.assertTrue(wevisone._tunnelled(shared(source)), source)
        self.assertFalse(wevisone._tunnelled(shared("studio")))   # a saved server reached at its own address
        self.assertFalse(wevisone._tunnelled(shared("local")))
        self.assertFalse(wevisone._tunnelled(shared("faraway", remote=False)))   # this computer's own ComfyUI
        self.assertFalse(wevisone._tunnelled(shared("http://studio:8188")))      # a source this build does not list
        self.assertFalse(wevisone._tunnelled(SimpleNamespace(server=Mock(), settings=SimpleNamespace())))
        with tempfile.TemporaryDirectory() as folder:
            app, _connection = self.routed(folder, None)
            self.assertTrue(wevisone._tunnelled(app))
            plain = Source("http://studio:8188", "studio:8188", "http://studio:8188")
            server = ExternalServer(app.settings, SimpleNamespace(), None, plain, Path(folder))
            self.assertFalse(wevisone._tunnelled(SimpleNamespace(server=server, settings=app.settings)))

class CleanMatteTests(unittest.TestCase):
    def test_faint_things_on_a_sure_plate_become_part_of_it_and_empty_holes_stay_empty(self):
        size = (1400, 1000)   # large enough that holes are looked for on a smaller copy
        plate = matte((200, 100, 1200, 900), size=size)
        ImageDraw.Draw(plate).rectangle((300, 200, 1100, 800), fill=40)   # the cookies, faint
        ImageDraw.Draw(plate).rectangle((650, 450, 750, 550), fill=0)     # a truly empty hole
        cleaned = clean_matte(plate)
        self.assertEqual(cleaned.getpixel((320, 220)), 255)
        # Far inside: the haze cut alone would have dropped it; firming it first keeps it.
        self.assertEqual(cleaned.getpixel((500, 350)), 255)
        self.assertEqual(cleaned.getpixel((700, 500)), 0)
        self.assertEqual(cleaned.getpixel((100, 50)), 0)
        # Beside the empty hole, within RIM of it, the faint pixels are the hole's edge and stay; a little farther they are cookies.
        self.assertEqual((cleaned.getpixel((647, 500)), cleaned.getpixel((645, 500))), (40, 255))

    def ring(self, blur):
        """A ring whose hole is truly empty, its edges blurred ``blur`` px; and the same after specks and core alone."""
        ring = Image.new("L", (600, 600))
        ImageDraw.Draw(ring).ellipse((100, 100, 500, 500), fill=255)
        ImageDraw.Draw(ring).ellipse((220, 220, 380, 380), fill=0)
        ring = ring.filter(ImageFilter.GaussianBlur(blur))
        return ring, ring.point(lambda alpha: 0 if alpha < 6 else 255 if alpha >= 250 else alpha)

    def test_the_soft_edge_of_an_empty_hole_stays_soft(self):
        # As soft as the model's usual edge: the faint part of the inner edge is 3 px wide (measured on the stored mattes:
        # the drone's landing gear, the mugs' handles).
        ring, mapped = self.ring(1.5)
        self.assertEqual([mapped.getpixel((x, 300)) for x in range(220, 225)], [96, 41, 12, 0, 0])   # the inner ramp's faint part
        cleaned = clean_matte(ring)
        hole = (200, 200, 400, 400)
        self.assertEqual(cleaned.crop(hole).tobytes(), mapped.crop(hole).tobytes())
        self.assertEqual(cleaned.getpixel((160, 300)), 255)

    def test_a_softer_edge_than_usual_stays_soft_all_the_way(self):
        # The faint part of the inner edge is 6 px wide, twice RIM (a mug's handle and a dog on a beach came back so).
        ring, mapped = self.ring(3)
        self.assertEqual([mapped.getpixel((x, 300)) for x in range(220, 227)], [111, 81, 54, 32, 18, 8, 0])
        cleaned = clean_matte(ring)
        hole = (200, 200, 400, 400)
        self.assertEqual(cleaned.crop(hole).tobytes(), mapped.crop(hole).tobytes())

    def test_a_long_hole_edge_is_followed_up_to_the_sure_part(self):
        # A hole in a solid plate whose edge rises over 8 px (14, 28, ... 112), then the plate.
        size = (1400, 1000)
        plate = matte((200, 100, 1200, 900), size=size)
        for away in range(8, 0, -1):
            ImageDraw.Draw(plate).rectangle((650 - away, 450 - away, 750 + away, 550 + away), fill=14 * away)
        ImageDraw.Draw(plate).rectangle((650, 450, 750, 550), fill=0)
        cleaned = clean_matte(plate)
        self.assertEqual([cleaned.getpixel((650 - away, 500)) for away in range(1, 10)], [14 * away for away in range(1, 9)] + [255])
        self.assertEqual(cleaned.getpixel((700, 500)), 0)

    def test_the_plates_own_soft_step_down_to_the_cookies_is_made_solid(self):
        # Only a hole's edge stays soft: the plate falls to the faint cookies it closes in over 4 px, and that is the object.
        size = (1400, 1000)
        plate = matte((200, 100, 1200, 900), size=size)
        for inward, alpha in enumerate((200, 150, 100, 60, 40)):
            ImageDraw.Draw(plate).rectangle((300 + inward, 200 + inward, 1100 - inward, 800 - inward), fill=alpha)
        cleaned = clean_matte(plate)
        # The step's faint end (60, then the cookies' 40) becomes solid. Its 100 lies on the line where the sure part is
        # looked for on the smaller copy, so it is left as it was.
        self.assertEqual([cleaned.getpixel((300 + inward, 500)) for inward in (0, 1, 3, 4, 5)], [200, 150, 255, 255, 255])

    def test_faint_texture_around_a_gap_is_made_solid_without_specks(self):
        # Faint cookies, their texture alternating 30 and 40 from pixel to pixel, with an empty gap among them: around the
        # gap the matte rises and falls at random, never up to the sure plate. Beyond the gap's own soft edge, all solid.
        size = (1400, 1000)
        plate = matte((200, 100, 1200, 900), size=size)
        ImageDraw.Draw(plate).rectangle((300, 200, 1100, 800), fill=30)
        cookies = plate.load()
        for y in range(200, 801):
            for x in range(300 + y % 2, 1101, 2):
                cookies[x, y] = 40
        ImageDraw.Draw(plate).rectangle((650, 450, 750, 550), fill=0)
        cleaned = clean_matte(plate)
        self.assertEqual([cleaned.getpixel((650 - away, 500)) for away in (1, 2, 3)], [plate.getpixel((650 - away, 500)) for away in (1, 2, 3)])
        self.assertEqual({cleaned.getpixel((650 - away, y)) for away in range(4, 30) for y in range(480, 520)}, {255})

    def test_faint_haze_far_from_the_object_goes_and_its_soft_edge_stays(self):
        hazy = matte((0, 0, 399, 299), fill=30)
        ImageDraw.Draw(hazy).rectangle((100, 100, 140, 140), fill=255)
        ImageDraw.Draw(hazy).rectangle((300, 240, 310, 250), fill=100)   # half strength, far off: kept
        cleaned = clean_matte(hazy)
        self.assertEqual(cleaned.getpixel((120, 120)), 255)
        self.assertEqual(cleaned.getpixel((142, 120)), 30)    # next to the object: its soft edge
        self.assertEqual(cleaned.getpixel((250, 60)), 0)
        self.assertEqual(cleaned.getpixel((305, 245)), 100)
        self.assertEqual(cleaned.getbbox(), (95, 95, 311, 251))

    def test_a_faint_open_area_beside_the_object_is_not_closed_in(self):
        open_side = matte((150, 150, 399, 180), fill=40)   # runs out to the picture's edge
        ImageDraw.Draw(open_side).rectangle((100, 100, 300, 200), fill=255)
        cleaned = clean_matte(open_side)
        self.assertEqual(cleaned.getpixel((303, 165)), 40)    # beside the object: its soft edge
        self.assertEqual(cleaned.getpixel((390, 165)), 0)     # far haze, never closed in

    def test_the_darkest_and_brightest_around_each_pixel_are_minfilters_and_maxfilters(self):
        noise = Image.effect_noise((61, 47), 90).point(lambda value: max(0, min(255, value)))
        self.assertEqual(wevisone._around(noise, lowest=True).tobytes(), noise.filter(ImageFilter.MinFilter(3)).tobytes())
        self.assertEqual(wevisone._around(noise, lowest=False).tobytes(), noise.filter(ImageFilter.MaxFilter(3)).tobytes())

    def test_a_matte_with_no_sure_part_stays_as_it_is(self):
        glass = matte((50, 50, 350, 250), fill=90)
        ImageDraw.Draw(glass).rectangle((0, 0, 20, 20), fill=30)
        self.assertEqual(clean_matte(glass).tobytes(), glass.tobytes())

    def test_specks_go_and_an_opaque_core_is_fully_editable_as_before(self):
        mask = matte((100, 100, 200, 200), fill=252)
        mask.putpixel((150, 150), 4)
        cleaned = clean_matte(mask)
        self.assertEqual(cleaned.getpixel((120, 120)), 255)
        self.assertEqual(cleaned.getpixel((150, 150)), 0)   # a speck inside is no faint pixel: not firmed


class NearClickTests(unittest.TestCase):
    FRAME = (1536, 1024)

    def wire(self, x):
        return matte((x, 100, x, 900), size=self.FRAME)

    def at(self, x, y=500):
        return (x + 0.5) / self.FRAME[0], (y + 0.5) / self.FRAME[1]

    def test_a_click_just_beside_a_thin_wire_selects_it(self):
        self.assertEqual(choose_matte([self.wire(1000)], self.at(1002)).getpixel((1000, 500)), 255)

    def test_a_click_farther_off_finds_nothing_as_before(self):
        with self.assertRaises(NothingFound):
            choose_matte([self.wire(1000)], self.at(1005))

    def test_the_nearest_wire_wins(self):
        chosen = choose_matte([self.wire(1000), self.wire(1004)], self.at(1003))
        self.assertEqual((chosen.getpixel((1004, 500)), chosen.getpixel((1000, 500))), (255, 0))

    def test_an_object_under_the_click_beats_one_beside_it(self):
        under = matte((900, 400, 1100, 600), size=self.FRAME)
        chosen = choose_matte([self.wire(1003), under], self.at(1001))
        self.assertEqual(chosen.getpixel((950, 450)), 255)

    def test_a_click_just_outside_a_large_object_is_not_on_it(self):
        dog = matte((600, 400, 900, 700), size=self.FRAME)
        for x, y in ((902, 500), (902, 702)):   # beside its side, and off its corner: both within NEAR_CLICK of it
            with self.subTest(x=x, y=y), self.assertRaises(NothingFound):
                choose_matte([dog], self.at(x, y))

    def test_a_soft_edged_strap_as_wide_as_a_leash_is_still_thin(self):
        # The stored leash, cable and hose are 8 to 20 px wide at the hit level once their soft edges are counted.
        strap = matte((1000, 100, 1009, 900), size=self.FRAME).filter(ImageFilter.GaussianBlur(1.5))
        level = max(8, round(strap.getextrema()[1] * 0.25))
        edge = max(x for x in range(990, 1020) if strap.getpixel((x, 500)) >= level)
        self.assertGreaterEqual(edge - min(x for x in range(980, 1020) if strap.getpixel((x, 500)) >= level) + 1, 12)
        self.assertEqual(choose_matte([strap], self.at(edge + 2)).getpixel((1004, 500)), 255)

    def test_large_starts_at_two_percent_of_the_frame(self):
        side = 2 * round(wevisone.THICK * max(self.FRAME) / 2) + 1
        self.assertEqual(side, 31)
        bar = matte((1000, 100, 999 + side - 1, 900), size=self.FRAME)   # one pixel narrower: still thin
        self.assertEqual(choose_matte([bar], self.at(999 + side + 1)).getpixel((1000, 500)), 255)
        bar = matte((1000, 100, 999 + side, 900), size=self.FRAME)
        with self.assertRaises(NothingFound):
            choose_matte([bar], self.at(1000 + side + 1))

    def test_a_strap_along_the_picture_edge_is_as_thin_as_anywhere(self):
        strap = matte((0, 100, 19, 900), size=self.FRAME)   # 20 px, against the left edge
        self.assertEqual(choose_matte([strap], self.at(21)).getpixel((10, 500)), 255)

    def test_a_thin_thing_the_list_left_out_beside_a_listed_object_is_found_close_up(self):
        # The list holds only the dog; a 4 px leash touches its right side. The click is on the leash, 2 px from the dog.
        dog, leash = (600, 400, 900, 700), (901, 300, 904, 560)
        point = self.at(902, 480)
        passes = []

        def instances(client, name, frame, query, progress, cancel, *, cache=True):
            passes.append(frame.size)
            if frame.size == self.FRAME:
                return [matte(dog, size=self.FRAME)]
            x1, y1, _x2, _y2 = wevisone.close_up(self.FRAME, point, wevisone.CLOSE_UPS[len(passes) - 2])
            return [matte((b[0] - x1, b[1] - y1, b[2] - x1, b[3] - y1), size=frame.size) for b in (dog, leash)]

        helper = WeVisOne.__new__(WeVisOne)
        helper._client = Mock(return_value=Mock())
        helper._instances = instances
        with patch.object(wevisone, "encoder", lambda client: "enc"):
            found = helper.mask(Mock(), Image.new("RGB", self.FRAME), DEFAULT_QUERY, point, lambda *a: None, lambda: False)
        self.assertEqual((found.getpixel((902, 400)), found.getpixel((750, 550))), (255, 0))
        self.assertEqual(passes, [self.FRAME, (614, 614)])


if __name__ == "__main__":
    unittest.main()
