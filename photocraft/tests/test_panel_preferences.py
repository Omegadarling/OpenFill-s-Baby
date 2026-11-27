# SPDX-License-Identifier: Apache-2.0
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import removal_advice  # noqa: E402
from openfill_photocraft.references import ReferenceImages  # noqa: E402
from openfill_photocraft.service import JobState, Service  # noqa: E402
from openfill_photocraft.web import NATIVE_FILE, OPTIONS_FILE, Api  # noqa: E402

from openfill.config import PANEL_SECTIONS, Settings  # noqa: E402

PAGE = ROOT / "photocraft/openfill_photocraft/static/index.html"


def png(color):
    buffer = io.BytesIO()
    Image.new("RGBA", (12, 8), color).save(buffer, "PNG")
    return buffer.getvalue()


class PanelPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.app = SimpleNamespace(settings=Settings(active_model="qwen"), paths=SimpleNamespace(config=self.path))
        self.service = SimpleNamespace(app=self.app, references=ReferenceImages())
        self.api = Api(self.service)

    def test_layout_and_visibility_are_independent_preferences(self):
        self.api.post("/api/layout", {"order": ["models", "edit", "models", "stale", 9], "collapsed": ["edit"]})
        self.api.post("/api/models/visibility", {"id": "qwen", "visible": False})
        saved = Settings.load(self.path)
        self.assertEqual(
            saved.panel_layout,
            {
                "order": ["models", "edit", "references", "engine", "generation"],
                "collapsed": ["edit"],
            },
        )
        self.assertEqual(set(saved.panel_layout["order"]), set(PANEL_SECTIONS))
        self.assertEqual(saved.hidden_models, ["qwen"])
        self.assertEqual(saved.active_model, "qwen")
        self.api.post("/api/models/visibility", {"id": "qwen", "visible": True})
        self.assertEqual(Settings.load(self.path).hidden_models, [])

    def test_failed_preference_write_restores_state(self):
        self.path.mkdir()  # rename onto a directory fails
        with self.assertRaises(OSError):
            self.api.post("/api/models/visibility", {"id": "qwen", "visible": False})
        self.assertEqual(self.app.settings.hidden_models, [])

    def test_web_attachments_can_be_reordered_and_removed(self):
        def attach(name, color):
            return self.api.post("/api/references", {"name": name, "data": base64.b64encode(png(color)).decode()})

        attach("C:\\private\\first.png", "red")
        first, second = attach("second.png", "blue")
        self.assertEqual(first["name"], "first.png")
        frozen = self.service.references.snapshot()
        with self.assertRaises(ValueError):
            self.api.post("/api/references/order", {"ids": [second["id"], second["id"]]})
        self.api.post("/api/references/order", {"ids": [second["id"], first["id"]]})
        self.assertEqual(self.service.references.snapshot(), frozen[::-1])
        self.api.post("/api/references/remove", {"id": second["id"]})
        self.assertEqual(self.service.references.snapshot(), frozen[:1])
        self.assertFalse(self.path.exists())

    def test_native_multi_file_attachment_is_atomic(self):
        good = Path(self.tmp.name) / "valid.png"
        bad = Path(self.tmp.name) / "invalid.png"
        good.write_bytes(png("red"))
        bad.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            self.service.references.add_files([good, bad])
        self.assertEqual(self.service.references.rows(), [])
        self.assertEqual(self.service.references.add_files([good])[0]["width"], 12)

    def service_with_job(self):
        service = Service.__new__(Service)
        service.app = self.app
        service.state = JobState()
        service._operation_lock = threading.Lock()
        service._thread = None
        service._job = Mock()
        return service

    def test_next_job_uses_and_persists_pending_generation_values(self):
        service = self.service_with_job()
        observed = []
        service._job.side_effect = lambda *_: observed.append(self.app.settings.auto_feather_percent)
        Api(service).post(
            "/api/fill",
            {
                "prompt": "edit",
                "generation_settings": {"auto_feather": False, "auto_feather_percent": 38},
            },
        )
        service._thread.join(3)
        self.assertFalse(service._thread.is_alive())
        self.assertEqual(observed, [38])
        service._job.assert_called_once_with("fill", {"prompt": "edit", "_reference_images": ()})
        saved = Settings.load(self.path)
        self.assertFalse(saved.auto_feather)
        self.assertEqual(saved.auto_feather_percent, 38)
        self.assertEqual(saved.active_model, "qwen")

    def test_generation_values_are_rejected_before_start_or_during_active_job(self):
        service = self.service_with_job()
        for invalid in ({"comfy_dir": "another server"}, {"auto_feather_percent": float("nan")}):
            with self.assertRaises(ValueError):
                service.start("fill", {"prompt": "edit", "generation_settings": invalid})
        service._thread = Mock()
        service._thread.is_alive.return_value = True
        with self.assertRaisesRegex(RuntimeError, "job is already running"):
            service.start("fill", {"prompt": "edit", "generation_settings": {"auto_feather": False}})
        self.assertTrue(self.app.settings.auto_feather)
        self.assertFalse(self.path.exists())
        service._job.assert_not_called()

    def test_generation_write_failure_restores_previous_values(self):
        service = self.service_with_job()
        with (
            patch.object(self.app.settings, "save", side_effect=OSError("write failed")),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            service.start("fill", {"prompt": "edit", "generation_settings": {"auto_feather": False}})
        self.assertTrue(self.app.settings.auto_feather)
        self.assertEqual(service.state.state, "idle")
        service._job.assert_not_called()


class SharedOptionTests(unittest.TestCase):
    """Remove's "With its shadow and reflection" and Select's "With its attached parts": one choice that both
    panels show and store, kept in the profile for the next start."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name)
        self.service = Mock()
        self.service.bridge.profile = self.profile

    def shown(self, api):
        state = api.state("select")  # what the browser view reads on every look
        return state["removeCast"], state["selectWhole"]

    def test_both_are_on_until_unticked(self):
        self.assertEqual(self.shown(Api(self.service)), (True, True))

    def test_a_change_in_one_panel_holds_in_the_other_and_after_a_restart(self):
        api = Api(self.service)
        self.assertEqual(api.post("/api/options", {"removeCast": False}), {"removeCast": False, "selectWhole": True})
        self.assertEqual(self.shown(api), (False, True))
        self.assertIs(api.option("removeCast"), False)  # what the native panel reads
        api.set_options({"selectWhole": False})
        self.assertEqual(self.shown(Api(self.service)), (False, False))
        saved = json.loads((self.profile / OPTIONS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved, {"removeCast": False, "selectWhole": False})

    def test_what_the_native_panel_saved_before_both_shared_it_still_counts(self):
        (self.profile / NATIVE_FILE).write_text(
            json.dumps({"removeCast": False, "selectWhole": False, "variations": 3}), encoding="utf-8"
        )
        self.assertEqual(self.shown(Api(self.service)), (False, False))
        Api(self.service).set_options({"selectWhole": True})
        self.assertEqual(self.shown(Api(self.service)), (False, True))  # the shared file wins over the panel's own

    def test_only_the_two_options_and_only_on_or_off_are_taken(self):
        api = Api(self.service)
        for body in (
            {},
            {"removeCast": "no"},
            {"removeCast": 0},
            {"selectWhole": None},
            {"grow": False},
            {"removeCast": False, "other": True},
            ["removeCast"],
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                api.post("/api/options", body)
        self.assertFalse((self.profile / OPTIONS_FILE).exists())
        self.assertEqual(self.shown(api), (True, True))

    def test_a_broken_or_odd_file_counts_as_nothing_saved(self):
        (self.profile / OPTIONS_FILE).write_text("{not json", encoding="utf-8")
        (self.profile / NATIVE_FILE).write_text(json.dumps({"removeCast": "false", "selectWhole": 0}), encoding="utf-8")
        self.assertEqual(self.shown(Api(self.service)), (True, True))

    def test_a_choice_that_cannot_be_written_still_holds_for_both_panels_until_the_companion_closes(self):
        (self.profile / OPTIONS_FILE).mkdir()  # the rename onto a folder fails
        api = Api(self.service)
        self.assertEqual(api.set_options({"removeCast": False}), {"removeCast": False, "selectWhole": True})
        self.assertEqual(self.shown(api), (False, True))

    def test_a_service_without_a_profile_keeps_them_for_the_session(self):
        api = Api(SimpleNamespace(app=None))
        api.set_options({"selectWhole": False})
        self.assertEqual((api.option("removeCast"), api.option("selectWhole")), (True, False))


class BrowserOptionTests(unittest.TestCase):
    """The browser view shows what is saved, saves a change at once and lets Remove's hint follow its box."""

    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        self.script = re.search(r"<script>(.*?)</script>", self.html, re.S).group(1)

    def test_the_hint_texts_are_the_ones_the_native_panel_shows(self):
        self.assertIn(f'id="remove-text" hidden>{removal_advice.SELECTION_HINT}</div>', self.html)
        self.assertIn(f'"{removal_advice.SELECTION_HINT_ALONE}"', self.script)
        self.assertIn("syncSavedOptions(s,seq)", self.script)
        self.assertNotIn('$("remove-text").textContent = scene ? scene.tip : selectionHint;', self.script)

    def test_saved_choices_changes_and_the_remove_hint(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for JavaScript behavior checks")
        options = self.script[self.script.index("const OPTION_BOXES") : self.script.index('$("vars").onclick')]
        hint = self.script[self.script.index("const HINT_CAST") : self.script.index("let removalScenes")]
        cast, alone = json.dumps(removal_advice.SELECTION_HINT), json.dumps(removal_advice.SELECTION_HINT_ALONE)
        code = (
            f"""
const assert=require('assert');
const CAST={cast}, ALONE={alone};
"""
            + """const els={'remove-cast':{checked:true},'select-whole':{checked:true},'remove-text':{textContent:CAST}};
const $=id=>els[id];
let mode='remove', stateSequence=0, filled=0;
const posted=[], said=[], waiting=[];
const post=(path,body)=>{posted.push([path,body]);return new Promise((ok,fail)=>waiting.push({ok,fail}))};
const say=message=>said.push(message);
function fillSelect(){filled++}
"""
            + options
            + hint
            + """
(async()=>{
 assert.equal(selectionHint(),CAST);
 // the other panel unticked the shadow box: this view follows, and Remove's hint with it
 syncSavedOptions({removeCast:false,selectWhole:true},++stateSequence);
 assert.equal(els['remove-cast'].checked,false); assert.equal(filled,1); assert.equal(selectionHint(),ALONE);
 // unticking here is saved at once
 els['select-whole'].checked=false; const saving=els['select-whole'].onchange();
 assert.deepEqual(posted,[['/api/options',{selectWhole:false}]]);
 syncSavedOptions({removeCast:false,selectWhole:true},++stateSequence);   // a look while it is on its way
 assert.equal(els['select-whole'].checked,false);
 const sentBefore=++stateSequence;                                         // a look sent before it was saved
 waiting.shift().ok({}); await saving;
 syncSavedOptions({removeCast:false,selectWhole:true},sentBefore);
 assert.equal(els['select-whole'].checked,false);
 syncSavedOptions({removeCast:false,selectWhole:false},++stateSequence);  // the first look after it
 assert.equal(els['select-whole'].checked,false);
 syncSavedOptions({removeCast:true,selectWhole:true},++stateSequence);    // later ticked in the other panel
 assert.equal(els['select-whole'].checked,true); assert.equal(els['remove-cast'].checked,true);
 assert.equal(selectionHint(),CAST);
 // ticking the shadow box here redraws Remove's hint before the save is back; a failed save says why
 els['remove-cast'].checked=false; const failing=els['remove-cast'].onchange();
 assert.equal(selectionHint(),ALONE); assert.equal(filled,3);
 waiting.shift().fail(new Error("Each option must be on or off.")); await failing;
 assert.deepEqual(said,["Each option must be on or off."]);
 mode='select'; els['remove-cast'].checked=true; const quiet=els['remove-cast'].onchange();
 assert.equal(filled,3);  // the hint is only drawn in Remove
 waiting.shift().ok({}); await quiet;
})().catch(error=>{console.error(error);process.exit(1)});
"""
        )
        run = subprocess.run([node, "-e", code], text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
