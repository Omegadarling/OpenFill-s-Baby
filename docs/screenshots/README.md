# Screenshots

## PhotoCraft 0.20.3

Captured on November 15, 2026 from the unchanged 0.20.3 browser panel
(`photocraft/openfill_photocraft/static/index.html`). These are browser screenshots
at a 480 CSS-pixel panel width and 2× resolution. Open an image for readable controls.

| File | Shows |
| --- | --- |
| `photocraft-select-0.20.3.png` | Click/box/description selection, Replace/Add/Subtract, Show selection overlay, and Grow with a slider and numeric pixel field. The visible description is an example, not a newly submitted selection. |
| `photocraft-edit-0.20.3.png` | A reference prompt, attachment labels, and the saved Qwen Lightning result with Keep, Keep all and Discard. |
| `photocraft-remove-0.20.3.png` | Kind of removal → Automatic, MiniMax H3 Image as Method, and 1–5 variations. This shows configuration, not a new removal result. |
| `photocraft-engine-0.20.3.png` | Find servers, Add server and Multi-engine with separate Select/Edit/Remove assignments. Server names and addresses are portable examples. |

The current HTML and JavaScript run against an isolated loopback fixture server.
Document metadata, model inventory, references, jobs and engine settings are example
or saved data. No editor session, server discovery, model download or GPU generation
ran during these captures. The Edit thumbnail replays an actual saved Qwen Lightning
edit, also shown in the earlier GIF. The coffee preview is from the existing selection
test fixture; it does not display a selection matte.

Image sources: the [scikit-image coffee example](https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.coffee),
and [LightX2V's Qwen Lightning multi-reference demo inputs](https://huggingface.co/lightx2v/Qwen-Image-2.1-Lightning-v2-RL#multi-reference-generation-1).
The walkthrough GIF `photocraft-demo.gif` predates these screenshots; its panel layout
is retained as an example of the reference-edit workflow.

## Original GIMP captures

Captured in GIMP 3.2.6 on Windows 11 with an isolated profile by
`tests/gimp_batch_ui_screenshots.py`. No generation, backend check or download
ran for them: the progress window and the variations picker are driven by
test hooks (scripted steps, tinted copies of the selection as stand-in
variations), so the preview and thumbnails show the test scene, not a result.

| File | Shows |
|---|---|
| `filters-ai-menu.png` | `Filters > OpenFill` with Generative Expand..., Generative Fill Settings..., Generative Fill..., Stop AI Backend and Text to Image... (GIMP sorts them alphabetically). |
| `fill-dialog.png` | Generative Fill in Fill with a prompt mode: the pre-filled prompt "a red door with brass handles", Recent prompts, the Enter / Shift+Enter hint, collapsed Advanced, "Selection: 260 x 200 px in a 1024 x 768 px image" and the shortcut tip. |
| `fill-dialog-advanced.png` | The same dialog with Advanced open: Model, Avoid, Variations, Change amount, Seed. |
| `fill-dialog-recent-prompts.png` | The Recent prompts menu. |
| `fill-dialog-remove.png` | Remove object mode: "Erases the selection and continues the background. No prompt needed: just press Generate." |
| `fill-dialog-removal-model-note.png` | The note shown when the active model only removes objects (MAT, LaMa). |
| `expand-dialog.png` | Generative Expand: optional prompt, drag-to-expand canvas editor, ratio presets, anchor grid and pixel boxes. |
| `expand-editor-huge.png` | Generative Expand with 4096 px on both sides: the "Over 25% per side" warning. |
| `text-to-image-dialog.png` | Text to Image: prompt, Size, Result as. |
| `progress-window-start.png` | Progress window at the first sampling step ("Generating, step 1 of 25", 9 %), before a remaining-time estimate exists. |
| `progress-window.png` | Progress window during sampling: "Generating, step 12 of 25", 48 %, "Elapsed 0:09, about 0:10 left", preview, Cancel. |
| `progress-window-cancel-clicked.png` | Progress window after Cancel was clicked. |
| `variations-picker.png` | "3 variations are ready": three thumbnails, Discard, Keep all, Keep selected. |
| `settings-general.png` | Settings, General tab: AI backend folder with Browse..., Start backend automatically, Idle timeout (minutes), Show the floating progress window, Check backend, and the collapsed Advanced generation and Connection and troubleshooting sections. |
| `settings-general-advanced.png` | The General tab with the collapsed sections open. |
| `settings-models.png` | Settings, Models tab: downloaded models first, the active model tagged, one primary button per row (Active, Set active, Downloaded), Open models folder. |
| `settings-models-token.png` | The Models tab scrolled to "AVAILABLE TO DOWNLOAD" with the gated FLUX.1 Fill GGUF entry, the Hugging Face token field, Save token and Get a token. |

`openfill-demo.gif` (the README hero, 960 x 600, 20 fps, 17.2 s, loops) is a
screen recording of GIMP 3.2.6 (Flatpak, PhotoGIMP layout) on Linux with an RTX
2070 Max-Q: Filters > OpenFill > Generative Expand turns a 1448 x 1086 photo into
1448 x 1931 (4:3 to 3:4, no prompt, Stable Diffusion 1.5 Inpainting, two
rounds), then a lasso around the car and Generative Fill > Remove object (LaMa)
erase it. Every frame is a captured frame. The waits are sped up (about 30 s of
expand and 4 s of removal), the clicks run at about 1.8 times real speed, and
the pointer is drawn larger than the system cursor.
