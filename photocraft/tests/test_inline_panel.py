# SPDX-License-Identifier: Apache-2.0
"""Exercise the native sections and attachments without connecting to PhotoCraft or ComfyUI."""

import gc
import io
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

try:
    import tkinter as tk
except ImportError as err:
    raise unittest.SkipTest("Tk is unavailable") from err

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "photocraft"), str(ROOT / "gimp-openfill")]

from openfill_photocraft import ui  # noqa: E402
from openfill_photocraft.bar import Bar  # noqa: E402
from openfill_photocraft.references import ReferenceImages  # noqa: E402
from openfill_photocraft.web import Api  # noqa: E402

from openfill.config import Settings  # noqa: E402


class InlinePanelTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self.free_widgets)  # runs last, after close_root
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name)
        self.qwen, self.sdxl = "qwen-model", "sdxl-model"
        self.qwen_name, self.sdxl_name = "Qwen Lightning · Local ComfyUI", "SDXL · Local ComfyUI"
        self.service = service = Mock()
        service.app.settings = Settings(remote_fibo=True, active_model=self.qwen)
        from openfill_photocraft.engine_routes import EngineRoutes

        service.routes = EngineRoutes(service.app, self.profile)
        service.app_for.side_effect = service.routes.app_for
        service.configure_engines.side_effect = service.routes.save
        service.app.paths.config = self.profile / "openfill" / "config.json"
        service.app.source_label.return_value = "Local ComfyUI"
        service.engine = "ready"
        service.busy.return_value = False
        service.removal_advice.return_value = {
            "status": "ready",
            "method": "photocraft-texture",
            "displayMethod": "photocraft-texture",
            "key": "test-key",
            "source": "local",
            "tip": "Gradual lighting.",
        }
        service.state.snapshot.return_value = {"state": "idle", "variations": []}
        service.references = ReferenceImages()
        service.reference_rows.side_effect = service.references.rows
        service.add_reference_files.side_effect = service.references.add_files
        watcher = Mock()
        watcher.snapshot.return_value = {
            "connected": True,
            "document": {"name": "Edit.png"},
            "selection": [0, 0, 64, 64],
        }
        rows = [
            {
                "id": model_id,
                "name": name,
                "runnable": True,
                "installed": True,
                "active": model_id == self.qwen,
                "partial": [],
                "remote": True,
                "max_references": 15 if model_id == self.qwen else 0,
                "reference_syntax": "<imageN>" if model_id == self.qwen else "",
            }
            for model_id, name in ((self.qwen, self.qwen_name), (self.sdxl, self.sdxl_name))
        ]
        self.watcher, self.refresher = watcher, Mock()
        self.refresher.get.return_value = {
            "comfy_ok": True,
            "models": {"fill": [(r["id"], r["name"]) for r in rows]},
            "rows": rows,
            "active": self.qwen,
            "active_name": self.qwen_name,
        }
        self.api = Api(service)
        self.bar = self.make_bar(self.api)
        self.bar.refresh_model_visibility()

    def make_bar(self, api):
        """A panel on a withdrawn root, as the companion would open it with ``api``."""
        native_tk = tk.Tk

        def hidden_root(*args, **kwargs):
            try:
                root = native_tk(*args, **kwargs)
            except tk.TclError as err:
                self.skipTest(f"Tk cannot open a test window: {err}")
            root.withdraw()
            self.addCleanup(self.close_root, root)
            return root

        with (
            patch("tkinter.Tk", side_effect=hidden_root),
            patch("openfill_photocraft.bar.Refresher", return_value=self.refresher),
            patch("openfill_photocraft.tether.Tether"),
            patch("openfill_photocraft.ui.make_borderless", return_value=False),
            patch.object(Bar, "_tick", lambda self: None),
            patch.object(Bar, "_drain", lambda self: None),
            patch.object(Bar, "_place", lambda self: self.root.withdraw()),
        ):
            return Bar(self.service, self.watcher, "http://127.0.0.1/test", self.profile, api=api)

    def restart(self, api):
        """Close this panel and open it again: one Tk interpreter at a time, as ui's image cache expects."""
        self.close_root(self.bar.root)
        self.bar = self.make_bar(api)
        return self.bar

    @staticmethod
    def close_root(root):
        if getattr(root, "closed", False):
            return
        root.closed = True
        root.update_idletasks()
        for callback in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(callback)
        ui._cache.clear()
        ui.FONTS = None
        root.destroy()

    def free_widgets(self):
        """Free the bar and its root here, on the main thread. A Tk interpreter that the garbage
        collector frees on another thread (a later test's server) aborts the whole run with
        "Tcl_AsyncDelete: async handler deleted by the wrong thread"."""
        for name in ("bar", "api", "service", "watcher", "refresher"):  # the mocks keep the bar's callbacks
            vars(self).pop(name, None)
        gc.collect()

    def test_engine_finder_saves_into_both_native_pickers(self):
        panel = self.bar.settings_view.engine
        finder = panel.finder
        routes = self.service.routes

        def add(name, address):
            source = routes.add_server(name, address)
            return {"id": source.id, "name": source.label, "address": source.address}

        self.service.add_server.side_effect = add
        finder.show_results([{"address": "http://studio.local:8190", "name": "studio.local:8190", "saved": False}])
        add_button = next(w for w in finder.results.winfo_children()[0].winfo_children() if isinstance(w, ui.Button))
        add_button.invoke()
        self.assertEqual(finder.address.get(), "http://studio.local:8190")
        finder.name.set("Room two")
        finder.save()
        self.assertIn("Room two", panel.source.values)
        self.assertIn("Room two", panel.assignments.pickers["edit"].values)
        self.assertEqual(self.api.sources()["sources"][-1]["name"], "Room two")
        self.assertEqual(Settings.load(self.service.app.paths.config).comfy_servers[0]["label"], "Room two")
        self.assertEqual(finder.form.winfo_manager(), "")

    def test_every_control_in_the_native_panel_has_a_tooltip(self):
        from types import SimpleNamespace

        from openfill_photocraft import tips

        from openfill.remote_models import ROREM_ID

        bar = self.bar

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        kinds = (ui.Button, ui.IconButton, ui.Seg, ui.Select, ui.Checkbox, ui.Field, ui.PromptBox)
        untipped = [widget for widget in walk(bar.root) if isinstance(widget, kinds) and not hasattr(widget, "tip_text")]
        self.assertEqual(untipped, [])
        self.assertEqual(ui.tip_of(bar.modes, SimpleNamespace(x=ui.CW - 10)), tips.CONTROLS["mode.remove"])
        self.assertEqual(ui.tip_of(bar.go), tips.CONTROLS["go.fill"])
        bar.tab.set("remove")
        self.assertEqual(ui.tip_of(bar.go), tips.CONTROLS["go.remove"])
        self.assertTrue(ui.tip_of(bar.model_box).startswith(tips.CONTROLS["method"]))
        bar._model_ids = {"RORem · Engine": ROREM_ID}
        self.assertEqual(bar.model_box.item_tip("RORem · Engine"), tips.MODELS[ROREM_ID])
        self.assertEqual(bar.model_box.item_tip(ui.MANAGE), tips.MANAGE)
        self.assertIn("two repairs", bar.remove_scene_box.item_tip("Automatic"))
        for key in ("engine", "generation"):
            self.assertEqual(ui.tip_of(bar.sections.toggles[key]), tips.CONTROLS["section"])

    def test_the_native_panel_offers_the_shadow_and_parts_options_ticked(self):
        bar = self.bar
        bar.tab.set("remove")
        bar._layout_main()
        self.assertIn(bar.remove_cast, bar._shown["parts"])       # under Method, in Remove only
        self.assertTrue(bar.remove_cast.winfo_manager())
        self.assertTrue(bar.remove_cast.get())
        bar.remove_cast.set(False)
        self.assertIs(bar.prefs["removeCast"], False)
        bar.tab.set("fill")
        bar._layout_main()
        self.assertNotIn(bar.remove_cast, bar._shown["parts"])
        self.assertFalse(bar.remove_cast.winfo_manager())
        view = bar.select_view_panel
        self.assertTrue(view.whole.get())
        view.whole.set(False)
        self.assertIs(bar.prefs["selectWhole"], False)
        row = bar.remove_cast
        row.update_idletasks()
        self.assertLessEqual(row.winfo_reqwidth(), ui.CW)         # the label fits the panel's width

    def test_both_panels_share_the_shadow_and_parts_choices(self):
        bar, view = self.bar, self.bar.select_view_panel
        bar.remove_cast.set(False)
        view.whole.set(False)
        self.assertEqual((self.api.option("removeCast"), self.api.option("selectWhole")), (False, False))
        self.api.post("/api/options", {"removeCast": True, "selectWhole": True})  # ticked again in the browser view
        bar._update()
        self.assertEqual((bar.remove_cast.get(), view.whole.get()), (True, True))
        self.assertEqual((bar.prefs["removeCast"], bar.prefs["selectWhole"]), (True, True))
        self.api.post("/api/options", {"selectWhole": False})
        bar._update()
        self.assertEqual((bar.remove_cast.get(), view.whole.get()), (True, False))

    def test_a_restart_keeps_what_either_panel_chose(self):
        from openfill_photocraft.removal_advice import SELECTION_HINT_ALONE

        self.service.bridge.profile = self.profile
        Api(self.service).set_options({"removeCast": False, "selectWhole": False})  # unticked in the browser view
        bar = self.restart(Api(self.service))  # the next start
        self.assertEqual((bar.remove_cast.get(), bar.select_view_panel.whole.get()), (False, False))
        self.assertEqual(bar.remove_line.cget("text"), SELECTION_HINT_ALONE)

    def test_what_this_panel_saved_before_both_shared_it_holds_in_both(self):
        import json

        self.service.bridge.profile = self.profile
        (self.profile / "bar.json").write_text(json.dumps({"removeCast": False, "variations": 2}), encoding="utf-8")
        api = Api(self.service)
        bar = self.restart(api)
        self.assertEqual((bar.remove_cast.get(), bar.select_view_panel.whole.get()), (False, True))
        self.assertEqual((api.option("removeCast"), api.option("selectWhole")), (False, True))  # the browser view's

    def test_removes_hint_promises_the_shadow_look_up_only_while_the_box_is_ticked(self):
        from openfill_photocraft.removal_advice import SELECTION_HINT, SELECTION_HINT_ALONE

        bar = self.bar
        bar.tab.set("remove")
        bar.remove_scene = ""  # Manual method: the line shows the hint itself
        bar._fill_models()
        self.assertEqual(bar.remove_line.cget("text"), SELECTION_HINT)
        bar.remove_cast.set(False)
        bar._fill_models()
        self.assertEqual(bar.remove_line.cget("text"), SELECTION_HINT_ALONE)
        self.assertNotIn("looked for", SELECTION_HINT_ALONE)
        bar.remove_cast.set(True)
        bar._fill_models()
        self.assertEqual(bar.remove_line.cget("text"), SELECTION_HINT)

    def test_grow_number_and_slider_fit_at_the_native_panel_width(self):
        view = self.bar.select_view_panel
        row = view.grow.master
        row.update_idletasks()
        self.assertLessEqual(row.winfo_reqwidth(), ui.CW)
        self.assertGreater(view.grow_entry.winfo_reqwidth(), self.bar.fonts.body.measure("500"))

    def test_select_click_uses_image_area_and_never_submits_edit_settings(self):
        bar, view = self.bar, self.bar.select_view_panel
        self.service.layers = []
        bar.tab_buttons["select"].invoke()
        pc = bar.watcher.snapshot.return_value
        pc.update(documentKey="canvas", document={"name": "portrait", "width": 100, "height": 200})
        view._ready("canvas", Image.new("RGB", (80, 160)), "")
        left, top, width, height = view.rect
        self.assertTrue(view.winfo_manager())
        self.assertFalse(bar.options.winfo_manager() or bar.prompt.winfo_manager())
        view._click(SimpleNamespace(x=left - 1, y=top))
        self.service.start.assert_not_called()
        view._operation("add")
        view._click(SimpleNamespace(x=left + width // 2, y=top + height // 4))
        self.service.start.assert_called_once_with(
            "select",
            {"key": "canvas", "action": "click", "query": "", "operation": "add", "whole": True, "point": [0.5, 0.25]},
        )
        bar.tab_buttons["remove"].invoke()
        self.assertFalse(view.winfo_manager())
        self.assertTrue(bar.options.winfo_manager())
        self.assertEqual(list(bar.tab_buttons), ["select", "fill", "remove"])

    def test_drag_and_collapse_persist_and_restore(self):
        from openfill_photocraft.panels import SectionStack

        bar = self.bar
        self.assertIs(bar.api, self.api)
        root, stack = bar.root, bar.sections
        root.attributes("-alpha", 0.0)
        root.attributes("-topmost", False)
        root.deiconify()
        root.update()
        self.assertIn("engine", stack.collapsed)
        engine, edit = stack.grips["engine"], stack.headers["edit"]
        stack._start_drag("engine", SimpleNamespace(widget=engine, y_root=engine.winfo_rooty() + 10))
        stack._drop(SimpleNamespace(y_root=edit.winfo_rooty() + 2))
        self.assertEqual(stack.order[0], "engine")
        self.assertIn("engine", stack.collapsed)
        stack.toggles["edit"].invoke()
        self.assertFalse(stack.bodies["edit"].winfo_manager())
        loaded = Settings.load(self.service.app.paths.config).panel_layout
        new_stack = SectionStack(bar.inner, bar.scroller, loaded, Mock(), Mock())
        self.assertEqual(new_stack.order[0], "engine")
        self.assertIn("edit", new_stack.collapsed)
        self.assertFalse(new_stack.bodies["edit"].winfo_manager())
        self.assertTrue(new_stack.bodies["references"].winfo_manager())

    def test_disclosure_arrows_keep_the_same_size_in_both_states(self):
        stack = self.bar.sections
        toggle = stack.toggles["edit"]
        header = stack.headers["edit"]
        self.assertEqual(int(header.cget("height")), 32)
        expanded = toggle.cget("image")
        self.assertEqual(int(toggle.tk.call("image", "width", expanded)), 12)
        toggle.invoke()
        collapsed = toggle.cget("image")
        self.assertNotEqual(collapsed, expanded)
        self.assertEqual(int(toggle.tk.call("image", "width", collapsed)), 12)
        self.assertFalse(stack.bodies["edit"].winfo_manager())
        toggle.invoke()
        self.assertEqual(toggle.cget("image"), expanded)
        self.assertTrue(stack.bodies["edit"].winfo_manager())
        self.assertEqual(int(header.cget("height")), 32)

    def test_model_visibility_changes_only_the_dropdown(self):
        bar = self.bar
        self.assertEqual(bar.model_box.values, [self.qwen_name, self.sdxl_name])
        bar.models_view._rows[self.qwen]["visibility"].set(False)
        bar.refresh_model_visibility()
        self.assertEqual(bar.model_box.values, [self.sdxl_name])
        self.assertEqual(bar._model(), self.sdxl)
        self.assertIn(self.qwen, bar.models_view._rows)
        self.assertFalse(bar.models_view._rows[self.qwen]["visibility"].get())
        loaded = Settings.load(self.service.app.paths.config)
        self.assertEqual(loaded.hidden_models, [self.qwen])
        self.assertEqual(loaded.active_model, self.qwen)
        bar.models_view._rows[self.sdxl]["visibility"].set(False)
        bar.refresh_model_visibility()
        self.assertTrue(bar.go.disabled)
        self.assertEqual(bar.model_box.get(), "No models shown")
        bar.models_view._rows[self.qwen]["visibility"].set(True)
        bar.refresh_model_visibility()
        self.assertEqual(bar.model_box.values, [self.qwen_name])
        self.assertFalse(bar.go.disabled)

    def test_edit_offers_automatic_first_until_a_model_is_picked(self):
        from openfill_photocraft.edit_plan import AUTO_EDIT

        from openfill.remote_models import QWEN21_LIGHTNING_ID, TURBO_ID

        bar = self.bar
        self.assertNotIn("Automatic", bar.model_box.values)  # this engine's two models are not Automatic's
        names = {QWEN21_LIGHTNING_ID: "Qwen Lightning · Local ComfyUI", TURBO_ID: "FIBO Edit Turbo · Local ComfyUI"}
        rows = [{"id": model_id, "name": name, "runnable": True, "installed": True, "active": model_id == QWEN21_LIGHTNING_ID,
                 "partial": [], "remote": True, "max_references": 15 if model_id == QWEN21_LIGHTNING_ID else 0,
                 "reference_syntax": ""} for model_id, name in names.items()]
        bar.refresher.get.return_value = {"comfy_ok": True, "models": {"fill": list(names.items())}, "rows": rows,
                                          "active": QWEN21_LIGHTNING_ID, "active_name": names[QWEN21_LIGHTNING_ID]}
        bar.model_choice.set("")
        bar._fill_models()
        self.assertEqual(bar.model_box.values, ["Automatic", *names.values()])
        self.assertEqual((bar.model_box.get(), bar._model()), ("Automatic", AUTO_EDIT))
        self.assertEqual(bar._reference_options(bar.refresher.get()), (0, ""))
        self.assertIn("two edits", bar.model_box.item_tip("Automatic"))
        bar.prompt.set("Make the sofa mustard yellow.")
        bar.generate()
        kind, body = self.service.start.call_args.args
        self.assertEqual((kind, body["model"]), ("fill", AUTO_EDIT))
        self.assertNotIn("useReferences", body)
        bar._model_picked(names[TURBO_ID])
        self.assertEqual((bar._model(), bar.prefs["editModel"]), (TURBO_ID, ""))
        bar.model_choice.set("")
        bar._fill_models()
        self.assertEqual(bar._model(), QWEN21_LIGHTNING_ID)  # the active model: a picked model ended Automatic
        bar._model_picked("Automatic")
        self.assertEqual((bar._model(), bar.prefs["editModel"]), (AUTO_EDIT, "Automatic"))

    def test_kind_of_removal_fits_the_platform_font_and_remains_aligned(self):
        self.bar.tab_buttons["remove"].invoke()
        row = self.bar.remove_scene_row.winfo_children()[0]
        label_cell, _ = row.winfo_children()
        label = label_cell.winfo_children()[0]
        self.assertEqual(label.cget("text"), "Kind of removal")
        self.assertGreaterEqual(int(label_cell.cget("width")), self.bar.fonts.small.measure(label.cget("text")))
        self.assertEqual(int(label_cell.cget("width")) + 12 + int(self.bar.remove_scene_box.cget("width")), ui.CW)

    def test_remove_defaults_to_texture_offline_and_preserves_pending_edit_controls(self):
        bar = self.bar
        bar.prompt.set("An edit I am still writing")
        bar.refresher.get.return_value = {"error": "ComfyUI is offline", "comfy_ok": False}
        bar.tab_buttons["remove"].invoke()
        self.assertEqual(bar.model_box.values, ["Local background repair"])
        self.assertFalse(bar.go.disabled)
        self.assertFalse(bar.prompt.winfo_manager())
        self.assertEqual(bar.model_label.get(), "Method")
        with patch.object(bar.settings_view, "generation_options", side_effect=AssertionError("editing controls read")):
            bar.generate()
        kind, request = self.service.start.call_args.args
        self.assertEqual((kind, request["removal"], request["prompt"]), ("fill", "photocraft-texture", ""))
        self.assertTrue(request["remove"])
        self.assertNotIn("useReferences", request)
        bar.tab_buttons["fill"].invoke()
        self.assertEqual(bar.prompt.get(), "An edit I am still writing")
        self.assertTrue(bar.go.disabled)

    def test_remove_only_offers_visible_reconstruction_models_and_keeps_the_edit_model(self):
        bar = self.bar
        facts = bar.refresher.get.return_value
        facts["models"]["remove"] = [("fibo", "FIBO Dev"), ("hidden", "Hidden FIBO")]
        self.service.app.settings.hidden_models = ["hidden"]
        bar.select_tab("remove")
        self.assertEqual(bar.model_box.values, ["Local background repair", "FIBO Dev"])
        bar._model_picked("FIBO Dev")
        bar.generate()
        self.assertEqual(self.service.start.call_args.args[1]["removal"], "fibo")
        self.service.app.set_active.assert_not_called()
        bar.select_tab("fill")
        self.assertEqual(bar._model(), self.qwen)

    def test_scene_suggestion_then_manual_override_uses_the_chosen_model(self):
        bar = self.bar
        facts = bar.refresher.get.return_value
        facts["models"]["remove"] = [
            ("remote-lama-rmbg", "LaMa (Big-LaMa)"),
            ("remote-qwen-image-2.1-lightning-v2-rl", "Qwen Image 2.1 Lightning"),
        ]
        bar.select_tab("remove")
        bar._scene_picked("Wire or repeating pattern")
        self.assertEqual(bar._model(), "remote-lama-rmbg")
        self.assertIn("LaMa", bar.remove_line.cget("text"))
        bar._model_picked("Qwen Image 2.1 Lightning")
        self.assertEqual(bar.remove_scene, "")
        bar.generate()
        self.assertEqual(self.service.start.call_args.args[1]["removal"], "remote-qwen-image-2.1-lightning-v2-rl")
        self.service.app.set_active.assert_not_called()
        bar._scene_picked("Covered lettering")
        self.assertIn("reference", bar.remove_line.cget("text"))
        self.assertEqual(bar._model(), "remote-qwen-image-2.1-lightning-v2-rl")

    def test_auto_advice_updates_method_and_keeps_a_manual_override(self):
        from openfill_photocraft.removal_advice import AUTO

        bar = self.bar
        self.service.removal_advice.return_value = {"status": "sampling", "tip": "Reading colors…"}
        facts = bar.refresher.get.return_value
        facts["models"]["remove"] = [("remote-lama-rmbg", "Big-LaMa")]
        bar.select_tab("remove")
        self.assertTrue(bar.go.disabled)
        self.assertEqual(bar.remove_scene_box.get(), AUTO)
        self.service.removal_advice.return_value = {
            "status": "ready",
            "method": "remote-lama-rmbg",
            "displayMethod": "remote-lama-rmbg",
            "source": "local",
            "key": "correct-selection",
            "tip": "Nearby texture.",
        }
        bar._fill_models()
        self.assertEqual(bar._model(), "remote-lama-rmbg")
        bar.pick_variations(3)
        bar.generate()
        self.assertEqual(self.service.start.call_args.args[1]["adviceKey"], "correct-selection")
        self.assertEqual(self.service.start.call_args.args[1]["variations"], 3)
        bar._model_picked("Big-LaMa")
        bar.pick_variations(5)
        bar.generate()
        self.assertNotIn("adviceKey", self.service.start.call_args.args[1])
        self.assertEqual(self.service.start.call_args.args[1]["variations"], 5)
        bar._model_picked("Local background repair")
        bar._fill_models()
        self.assertEqual(bar._model(), "photocraft-texture")
        bar.generate()
        self.assertNotIn("adviceKey", self.service.start.call_args.args[1])
        bar._scene_picked(AUTO)
        self.assertEqual(bar._model(), "remote-lama-rmbg")

    def test_attach_reorder_and_send_only_to_a_supported_model(self):
        bar = self.bar
        files = [self.profile / "first.png", self.profile / "second.png"]
        for path, color in zip(files, ("red", "blue"), strict=False):
            Image.new("RGB", (16, 24), color).save(path)
        with patch("tkinter.filedialog.askopenfilenames", return_value=tuple(map(str, files))):
            bar.references.add.invoke()
            callback, args = bar.calls.get(timeout=5)
            callback(*args)
        self.assertEqual([r["name"] for r in bar.references.rows], ["first.png", "second.png"])
        bar.references._move(1, -1)
        self.assertEqual([r["name"] for r in bar.references.rows], ["second.png", "first.png"])
        top_row = bar.references.list.winfo_children()[0]
        self.assertIn("<image2>  second.png", top_row.winfo_children()[-1].cget("text"))
        bar.prompt.set("Add the object from <image2>.")
        bar.generate()
        self.assertTrue(self.service.start.call_args.args[1]["useReferences"])
        bar.model_choice.set(self.sdxl_name)
        bar._fill_models()
        self.assertTrue(bar.references.add.disabled)
        self.assertIn("runs without them", bar.references.help.cget("text"))
        bar.generate()
        self.assertNotIn("useReferences", self.service.start.call_args.args[1])
        bar.references._remove(bar.references.rows[0]["id"])
        self.assertEqual([r["name"] for r in bar.references.rows], ["first.png"])

    @unittest.skipUnless(sys.platform == "win32", "Windows shell file drops")
    def test_native_windows_file_drop_adds_ordered_unicode_paths(self):
        import ctypes as ct
        from ctypes import wintypes as wt

        class DropFiles(ct.Structure):
            _fields_ = [("pFiles", wt.DWORD), ("pt", wt.POINT), ("fNC", wt.BOOL), ("fWide", wt.BOOL)]

        user = ct.WinDLL("user32", use_last_error=True)
        memory = ct.WinDLL("kernel32", use_last_error=True)
        post = user.PostMessageW
        post.argtypes, post.restype = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM), wt.BOOL
        user.GetWindowLongW.argtypes, user.GetWindowLongW.restype = (wt.HWND, ct.c_int), wt.LONG
        memory.GlobalAlloc.argtypes, memory.GlobalAlloc.restype = (wt.UINT, ct.c_size_t), wt.HGLOBAL
        memory.GlobalLock.argtypes, memory.GlobalLock.restype = (wt.HGLOBAL,), ct.c_void_p
        memory.GlobalUnlock.argtypes, memory.GlobalUnlock.restype = (wt.HGLOBAL,), wt.BOOL
        memory.GlobalFree.argtypes, memory.GlobalFree.restype = (wt.HGLOBAL,), wt.HGLOBAL

        bar = self.bar
        target = bar.references.add.winfo_id()
        self.assertEqual(bar.references.add.cget_text(), "Drop images here or click to add")
        self.assertTrue(user.GetWindowLongW(target, -20) & 0x10)  # WS_EX_ACCEPTFILES
        files = [self.profile / "参考 2.png", self.profile / "café 1.png"]
        for path, color in zip(files, ("red", "blue"), strict=False):
            Image.new("RGB", (16, 24), color).save(path)

        def drop(paths):
            header = DropFiles(ct.sizeof(DropFiles), wt.POINT(4, 4), False, True)
            payload = bytes(header) + ("\0".join(map(str, paths)) + "\0\0").encode("utf-16-le")
            handle = memory.GlobalAlloc(2, len(payload))  # GMEM_MOVEABLE
            self.assertTrue(handle)
            address = memory.GlobalLock(handle)
            if not address:
                memory.GlobalFree(handle)
                raise ct.WinError(ct.get_last_error())
            ct.memmove(address, payload, len(payload))
            memory.GlobalUnlock(handle)
            if not post(target, 0x0233, handle, 0):  # WM_DROPFILES; receiver owns the handle after posting
                memory.GlobalFree(handle)
                raise ct.WinError(ct.get_last_error())
            bar.root.update()

        drop(files)
        for _ in range(2):  # native handoff, then the background image decoder's completion
            callback, args = bar.calls.get(timeout=5)
            callback(*args)
        self.assertEqual([row["name"] for row in bar.references.rows], [path.name for path in files])
        self.service.add_reference_files.assert_called_once_with(tuple(map(str, files)))

        bar.model_choice.set(self.sdxl_name)
        bar._fill_models()
        drop(files[:1])
        callback, args = bar.calls.get(timeout=5)
        callback(*args)
        self.service.add_reference_files.assert_called_once()
        self.assertIn("Choose a Qwen or FIBO", bar.references.note.cget("text"))

    def test_generate_and_prompt_enter_submit_latest_controls(self):
        bar = self.bar
        view = bar.settings_view.generation
        bar.prompt.set("Make the object blue.")
        view.fibo_edit_mode.pick("Replace selected content")
        view.auto_feather_percent.set(28)
        # Polling can refresh backend status while the settings are still staged.
        bar._update()
        self.service.start.assert_not_called()
        bar.go.invoke()
        self.assertEqual(
            self.service.start.call_args.args[1]["generation_settings"],
            {
                "fibo_edit_mode": "replace",
                "auto_feather_percent": 28,
            },
        )
        self.assertEqual(bar.settings_view.generation_options(), {})
        view.fibo_boundary_strength.set(72)
        self.service.start.side_effect = RuntimeError("Connection check in progress")
        bar.prompt.on_submit()  # PromptBox binds both Return keys to this callback.
        self.assertEqual(bar.settings_view.generation_options(), {"fibo_boundary_strength": 72})
        self.service.start.side_effect = None
        bar.prompt.on_submit()
        self.assertEqual(self.service.start.call_args.args[1]["generation_settings"], {"fibo_boundary_strength": 72})
        self.assertEqual(bar.settings_view.generation_options(), {})

    def test_pending_results_stay_in_view_and_generate_remains_available(self):
        bar, service = self.bar, self.service
        image = Image.new("RGBA", (8, 12), "red")
        layer = SimpleNamespace(origin=SimpleNamespace(width=8, height=12), rgba=image.tobytes())
        service.layers = [layer]
        st = {
            "state": "done",
            "title": "Generative Fill",
            "kind": "fill",
            "model": "Qwen",
            "elapsed": 6,
            "text": "Done",
            "fraction": 1.0,
            "variations": ["First"],
            "shown": 0,
            "variationSet": 1,
        }
        service.state.snapshot.return_value = st
        bar._update()
        self.assertEqual(len(bar.thumb_cells), 1)
        first = bar.thumb_cells[0]
        self.assertEqual(first.itemcget("selected", "state"), "normal")
        self.assertEqual(bar.thumbs.pack_slaves()[-2:], [bar.thumb_row, bar.keep_button.master])
        self.assertEqual(
            bar.keep_button.master.pack_slaves(), [bar.discard_button, bar.keep_all_button, bar.keep_button]
        )
        self.assertEqual(bar.actions.pack_slaves(), [bar.go])
        self.assertFalse(bar.go.disabled)

        def start(_kind, _params):
            service.busy.return_value = True
            st.update(state="running", elapsed=3, text="Sampling", fraction=0.5, previewSeq=1)

        preview = io.BytesIO()
        image.save(preview, format="PNG")
        service.state.lock, service.state.preview = Lock(), preview.getvalue()
        service.start.side_effect = start
        bar.prompt.set("Make it blue.")
        bar.go.invoke()
        service.start.assert_called_once()
        service.keep.assert_not_called()
        self.assertEqual(bar.actions.pack_slaves(), [bar.cancel_button])
        self.assertIs(bar.thumb_cells[0], first)
        self.assertTrue(bar.keep_button.disabled)
        self.assertEqual(bar.progress.value, 0.5)
        self.assertEqual(bar.job.inner.pack_slaves()[-2:], [bar.preview, bar.thumbs])

        service.layers += [layer] * 5
        service.busy.return_value = False
        service.removal_advice.return_value = {
            "status": "ready",
            "method": "photocraft-texture",
            "displayMethod": "photocraft-texture",
            "key": "test-key",
            "source": "local",
            "tip": "Gradual lighting.",
        }
        st.update(state="done", fraction=1.0, variations=["First", *map(str, range(2, 7))])
        bar._update()
        self.assertEqual(len(bar.thumb_cells), 6)
        self.assertEqual(int(bar.thumb_cells[5].grid_info()["row"]), 1)
        self.assertEqual(bar.thumb_cells[0].itemcget("selected", "state"), "normal")
        self.assertEqual(bar.actions.pack_slaves(), [bar.go])
        self.assertFalse(bar.keep_button.disabled)

        last = bar.thumb_cells[5]
        service.layers.append(layer)
        st["variations"].append("7")
        bar._update()
        self.assertEqual(len(bar.thumb_cells), 7)
        self.assertIs(bar.thumb_cells[5], last)
        st.update(state="cancelled", message="Cancelled", shown=5)
        bar._update()
        self.assertIs(bar.thumb_cells[5], last)
        self.assertEqual(last.itemcget("selected", "state"), "normal")
        self.assertEqual(bar.actions.pack_slaves(), [bar.go])


if __name__ == "__main__":
    unittest.main()
