# SPDX-License-Identifier: Apache-2.0
"""The native panel shows what the service adds about a job (JobState.notices), as the browser view does.

The shadow Remove took along, an engine too busy to look for it and Automatic's notes reach the artist only
through these notices, and the native panel is the one the companion opens by default.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import cast, edit_plan, ui  # noqa: E402
from openfill_photocraft.bar import Bar  # noqa: E402
from openfill_photocraft.references import ReferenceImages  # noqa: E402
from openfill_photocraft.service import JobState  # noqa: E402
from openfill_photocraft.web import Api  # noqa: E402

from openfill.config import Settings  # noqa: E402


def bar_for(test, service, profile):
    """A native panel on a withdrawn Tk root, with no PhotoCraft, engine or ticking."""
    import tkinter as tk

    ui._cache.clear()  # images an earlier test's Tk root made cannot be drawn on this one
    ui.FONTS = None
    watcher = Mock()
    watcher.snapshot.return_value = {"connected": True, "document": {"name": "Edit.png"}, "selection": [0, 0, 64, 64]}
    refresher = Mock()
    refresher.get.return_value = {"comfy_ok": True, "models": {"fill": [("qwen", "Qwen · Studio")]}, "rows": [],
                                  "active": "qwen", "active_name": "Qwen · Studio"}
    native_tk = tk.Tk

    def hidden_root(*args, **kwargs):
        root = native_tk(*args, **kwargs)
        root.withdraw()
        test.addCleanup(close_root, root)
        return root

    with patch("tkinter.Tk", side_effect=hidden_root), \
         patch("openfill_photocraft.bar.Refresher", return_value=refresher), \
         patch("openfill_photocraft.tether.Tether"), \
         patch("openfill_photocraft.ui.make_borderless", return_value=False), \
         patch.object(Bar, "_tick", lambda self: None), \
         patch.object(Bar, "_drain", lambda self: None), \
         patch.object(Bar, "_place", lambda self: self.root.withdraw()):
        return Bar(service, watcher, "http://127.0.0.1/test", profile, api=Api(service))


def close_root(root):
    root.update_idletasks()
    for callback in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(callback)
    ui._cache.clear()
    ui.FONTS = None
    root.destroy()


class NativeNoticeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        profile = Path(tmp.name)
        self.service = service = Mock()
        service.app.settings = Settings(remote_fibo=True, active_model="qwen")
        service.app.paths.config = profile / "openfill" / "config.json"
        service.app.source_label.return_value = "Studio"
        from openfill_photocraft.engine_routes import EngineRoutes

        service.routes = EngineRoutes(service.app, profile)
        service.app_for.side_effect = service.routes.app_for
        service.engine = "ready"
        service.busy.return_value = False
        service.layers = []
        service.references = ReferenceImages()
        service.reference_rows.side_effect = service.references.rows
        self.job = service.state = JobState()  # the real snapshot, with the keys the service really sends
        self.bar = bar_for(self, service, profile)

    def finish(self, kind="fill", title="Object Removal", notices=(), **fields):
        job = self.job
        job.state, job.kind, job.title, job.fraction = "done", kind, title, 1.0
        job.started, job.finished = 100.0, 106.0
        job.notices = list(notices)
        for name, value in fields.items():
            setattr(job, name, value)
        self.bar._update()

    def shown(self):
        notes = self.bar.job_notes
        return notes.cget("text") if notes.winfo_manager() else None

    def test_a_removal_says_under_its_done_line_that_the_shadow_went_too_and_how_to_keep_it(self):
        self.finish(removal_summary="Two repairs: FLUX 3 and RORem. Keep the better one.",
                    notices=[cast.notice("shadow")])
        bar = self.bar
        self.assertEqual(bar.job_text.cget("text"), "Two repairs: FLUX 3 and RORem. Keep the better one.")
        self.assertEqual(self.shown(), cast.notice("shadow"))
        self.assertEqual(bar.job_notes.cget("fg"), ui.WARN)
        slaves = bar.job.inner.pack_slaves()
        self.assertEqual(slaves[slaves.index(bar.job_text) + 1], bar.job_notes)  # directly under the done line
        bar.job_notes.update_idletasks()
        self.assertLessEqual(bar.job_notes.winfo_reqwidth(), ui.CW - 24)    # wrapped inside the card

    def test_several_notices_stack_one_per_line(self):
        self.finish(notices=[cast.notice("shadow"), cast.BUSY])
        self.assertEqual(self.shown(), cast.notice("shadow") + "\n" + cast.BUSY)

    def test_every_kind_of_job_shows_its_notices_whatever_its_state(self):
        for kind, title in (("select", "Select"), ("fill", "Generative Fill"), ("fill", "Object Removal"),
                            ("expand", "Generative Expand"), ("txt2img", "Create")):
            for state in ("running", "done", "error", "cancelled"):
                with self.subTest(kind=kind, title=title, state=state):
                    note = f"A note about {title}, {state}."  # a new text each time, so each case draws it afresh
                    self.finish(kind, title, [note], message="Finished.")
                    self.job.state = state
                    self.bar._update()
                    self.assertEqual(self.shown(), note)

    def test_automatic_edit_notes_are_shown_too(self):
        self.finish(title="Generative Fill", notices=[edit_plan.ROOM_NOTICE])
        self.assertEqual(self.shown(), edit_plan.ROOM_NOTICE)

    def test_the_line_is_hidden_while_a_job_has_no_notices(self):
        self.finish(notices=[cast.BUSY])
        self.assertIsNotNone(self.shown())
        self.finish(notices=[])  # the service clears them when the next job starts
        self.assertIsNone(self.shown())
        self.assertEqual(self.bar.job_notes.cget("text"), "")
        self.bar._update_job({**self.job.snapshot(), "notices": None})
        self.assertIsNone(self.shown())
        snapshot = self.job.snapshot()
        del snapshot["notices"]  # an older or a stand-in service sends none
        self.bar._update_job(snapshot)
        self.assertIsNone(self.shown())

    def test_a_notice_before_any_job_shows_too(self):
        # A settings file that could not be read is reported before anything has run.
        self.job.notices = ["Your settings file could not be read and was moved aside."]
        self.bar._update()
        self.assertTrue(self.bar._shown.get("job"))
        self.assertEqual(self.shown(), "Your settings file could not be read and was moved aside.")
        self.job.notices = []
        self.bar._update()
        self.assertFalse(self.bar._shown.get("job"))

    def test_the_results_stay_last_in_the_card_below_the_notices(self):
        from PIL import Image

        image = Image.new("RGBA", (8, 12), "red")
        self.service.layers = [SimpleNamespace(origin=SimpleNamespace(width=8, height=12), rgba=image.tobytes())]
        self.finish(notices=[cast.notice("shadow")], variations=["First"], variation_labels=["RORem"], variation_set=1)
        slaves = self.bar.job.inner.pack_slaves()
        self.assertEqual(slaves[-2:], [self.bar.job_notes, self.bar.thumbs])


if __name__ == "__main__":
    unittest.main()
