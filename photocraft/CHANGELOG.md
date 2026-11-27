# Changelog

All notable changes to OpenFill for PhotoCraft are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.27.0] - 2026-11-26

This release adds Automatic Edit, removes an object's shadow and reflection with it, and brings an object's attached parts into a selection. Where it says so, the star ratings come from independent AI raters: they saw the results of 0.27.0 and of an earlier build shuffled together without knowing which was which, two raters per result and a third where they differed by two stars or more, after seeing hand ratings of other results for the same pictures to learn the scale. The other figures are hand ratings or by-eye judgments from before-and-after sheets, as noted.

### Edit

- **Edit has an Automatic choice.** With **Automatic** in the Model menu, Edit makes two edits with the two models that rated best together for that kind of edit and names the model under each thumbnail; keep the better one. Qwen goes first for a new colour, material, pattern or lettering, and FIBO Turbo for a new or different object. An edit of the whole picture uses Qwen and H3, never FIBO. On 24 hand-rated edits, the best single model gave a good result (4 or 5 stars) on 11; the two edits Automatic shows gave one on 17. Automatic is offered where the engine has two of Qwen, FIBO and H3, and it is the choice until you pick a model. Reference images still need a model picked by hand.
- **FIBO runs in the mode that suits the edit, and in the other mode as a third result.** Preserve keeps the selected thing's shape and gives it a new look; Replace draws a different object, or one added in an empty box. Until now FIBO ran in whatever mode was saved, which was wrong for about half of all edits. AI-rated: Preserve 3.9 stars against 3.0 for Replace on 12 new looks, and Replace 4.0 against 3.0 on 9 different or added objects.
- **Something added "here" in a box you drew gets two FIBO Replace edits and Qwen told where the box is.** Qwen cannot see the box, so "here" told it nothing and it redrew the spot empty; it is now told "in the middle of the picture" on a crop with little around the box. AI-rated on 11 such adds: a good result on all 11, and two good ones on 8 (1 before). Lettering, an add onto a subject ("on his head") and a look over an area keep the plan they had.
- **Automatic gives the edit room.** A tight object selection is grown so that Auto-feather's soft band lies outside the object instead of leaving its rim with the old look, and a different object (a scooter for a bicycle) gets the hull of the old one's outline, a little enlarged. By eye, 7 of 8 test edits that had failed for lack of room came out clearly better and 1 the same.
- AI-rated on 21 edits of a selection: the best of Automatic's results is good on 19, against 13 for the first version of Automatic Edit (mean 4.4 stars against 3.7; 8 better, 13 the same, none worse).

### Remove

- **Remove takes the object's shadow and reflection with it.** With **With its shadow and reflection** ticked (it is by default), OpenFill asks the selection model for the selected object's own shadow and its reflection, checks each find against the picture and removes them with the object. The panel says when it took one along; untick the box to remove only what you selected. On 101 test selections it took something along on 35, rightly on 34, and missed one that was there on 9 (a shadow that does not touch the object, or a reflection in a mirror beside it). By eye, the better repair was good on 3 of those 35 without the shadow and on 29 with it. The step adds 2 to 12 seconds.
- **Remove takes the object's own edge and contact shadow just outside the selection.** A selection often stops a few pixels inside the object's outline, and every method then left that fringe as a dark line (a thermostat's shaded crescent, a duck's feet on the floor). It is added only on plain surroundings, never more than 40% of the selection, and not on a graded studio backdrop.
- **Shadows of look-alikes stay with their own objects.** Among similar objects (glasses, bottles), only the shadow nearest the selected one is taken.
- **Automatic Remove adds Local's repair on plain surroundings** (a studio backdrop, a wall, a clear sky) as a third choice, because the other methods tend to leave a ghost or a blob there.
- **Every Automatic job runs with the settings its results were rated with**, whatever is saved in the panel (Auto-feather, FIBO's boundary match and context, and the seam and grain corrections for removals). Nothing it chooses is saved.
- The shadow look-up starts the engine when the removal needs it anyway, and never for the look-up alone. When the engine is off or busy, the removal goes ahead and the panel says the shadow was not looked for. A refused connection is reported as an engine that is not running, and a remote engine gets a few seconds and a second try before it is called not running.
- AI-rated on the test removals this release changes (32 of 108): the better repair is good on 21 instead of 15 (10 better, 20 the same, 2 worse: a sign whose ghost every method paints back, and a sticker on a dome).
- Keep and Discard after an Automatic removal also note whether a shadow or reflection was taken along (`removal-choices.jsonl`; no pixels).

### Select

- **Select brings the object's attached parts.** With **With its attached parts** ticked (it is by default), a selection also takes what is attached to the object or held by it, and a string, rope, cable or leash is asked for by name. Of nine test objects whose selection had left a part out (a bin's wheel, a skier's skis and poles, an umbrella in a hand, a kite's string), eight now come back whole; the painted rigging of a watercolour sailboat still does not. Untick the box to pick one part on its own. A looser answer that is only the object again, also at the picture's edge, is not taken for a part.
- **A click that finds no object looks closer**, at two close-ups around the click and then by asking the model what is at that point. The selection model's matte is cleaned of faint haze and unsure holes, and a click a pixel off a thin object still picks it. On 77 test clicks, 73 now select the object (51 in 0.24.3).

### Engine and panels

- **The engine's picture loader recovers by itself.** ComfyUI 0.46 can run out of address space after a few hundred different pictures in one session, and every new picture then failed until the server was restarted. OpenFill now clears the server's picture cache (models stay loaded) and repeats the job once.
- The native panel shows a job's notices, and both panels share the Remove and Select options.
- Known limits: a metal rail under a glass table can be taken as an object's reflection; FLUX 3 and RORem can paint a removed object's outline back on a plain backdrop; a small patch of an object's own shadow can come along as an attached part (2 of 77 test objects).

## [0.24.3] - 2026-11-25

- **Automatic Remove shows FLUX 3's repair first from 1% of the picture up**, now also on selections over 5%, with RORem beside it. With the edge correction, FLUX 3 rated 3.9 stars on the 46 rated pictures of that size, against 3.2 for RORem, and seam and colour complaints fell from 21 to 4. Replayed on all 54 rated pictures, the better of Automatic's two repairs rated 4.2 stars, and three pictures in four got a 4 or 5. Under 1% both repairs are still RORem variations.
- The tooltips for Automatic, FLUX 3, RORem and Qwen quote these ratings.

## [0.24.2] - 2026-11-25

- **FLUX 3 removals on patterned surfaces keep their colour.** On a strong pattern such as red and white gingham, the thin bands just outside and just inside the selection hold different parts of the pattern, so the edge correction added in 0.24.1 could read the pattern as a colour difference and tint a fill that already matched (white checks turned mint). Edge stretches whose colours vary too much to measure are now left out, so patterned edges are not corrected. Smooth walls, skies, vignettes and illustrations are corrected as before.

## [0.24.1] - 2026-11-24

- **FLUX 3 removals no longer leave a lighter patch.** The model could fill the selection a few levels lighter or warmer than the surface around it, which showed as a faint rectangle on flat walls and illustrations. After a FLUX 3 removal, OpenFill compares the original just outside the selection with the fill just inside it and evens out the difference. Only a smooth colour offset is applied, so the generated detail is unchanged.

## [0.24.0] - 2026-11-24

- Edit and Remove can use **FLUX 3 Image Klein** (NVFP4, 16 steps) on a ComfyUI server that has its model files in a `FLUX-3-Image-Klein` folder. The selection is greyed in the picture the model sees. It edits one picture at a time, without reference attachments. Its FLUX Kommunity License limits commercial output to companies under US$5M annual revenue.
- In Remove, FLUX 3 fills the selection while the original surroundings stay fixed, so the repair matches them instead of leaving a lighter or darker outline. Rated on 54 pictures it scored 3.4 stars, level with RORem and best on selections covering 1 to 5% of the picture; it takes about 28 s.
- **Automatic Remove pairs RORem with FLUX 3** once a selection covers 1% of the picture: from 1 to 5% FLUX 3's repair is shown first with RORem beside it, and over 5% RORem's is shown first with FLUX 3 beside it. Under 1% both repairs are still RORem variations. Replayed on the rated pictures, the better of the two rated 4.1 stars instead of 3.9. On a server without FLUX 3, Automatic shows two RORem variations from 1 to 5% and RORem with Qwen over 5%.

## [0.23.0] - 2026-11-23

- **Tooltips on every control.** Rest the pointer on a button, menu, slider or field in the panel or the browser view to see what it does.
- **Pros and cons for every model and removal method.** The Model and Method menus show, for each entry and for the current choice, what it is, what it is good at and where it falls short. Removal methods quote how they rated (1 to 5 stars) and roughly how long they take; a model that can also remove says how it rated for removal. Kind of removal entries say which method they pick. The same tips are on each row of Manage Models.

## [0.22.0] - 2026-11-23

- **Automatic Remove makes two repairs and labels them.** On a server that offers RORem, LaMa or Qwen Image Lightning, Automatic runs the methods artists rated best for a selection of that size and shows both results with the method under each thumbnail. Keep the better one. Results from a method chosen by hand are labelled too.
- A repair that leaves the object in place is replaced by the next method, and the panel says so.
- The order comes from ratings of 54 pictures: a selection covering 1-5% of the picture tries Qwen first and shows its repair first, with RORem beside it; smaller and larger selections get two RORem variations.
- Keep and Discard add one line to `removal-choices.jsonl` in the profile: the methods offered and the one kept. No pixels are stored.
- Warnings from one job no longer stay on screen during the next.

## [0.21.0] - 2026-11-16

- Edit can use **MOSS AnyPaint** (MOSS-Image Base with the AnyPaint adapter) on an existing ComfyUI server that has the moss-anypaint nodes and model files. Describe the finished picture; OpenFill repaints the selection in eight steps and keeps everything outside it unchanged. It accepts no reference attachments and appears only on servers that offer every required node and file.
- MOSS AnyPaint is also a manual method in **Remove**. It repaints the selection from a description of an empty background and never sees the object being removed. **Automatic** still prefers MiniMax H3 Image.

## [0.20.3] - 2026-11-12

- Automatic removal prefers MiniMax H3 Image when the selected engine offers it. The removal guide is now labeled **Kind of removal** and **Automatic**.
- Refining a WeVisOne-VL selection with PhotoCraft's tools keeps the canvas in place. The selection preview can display an exact overlay on request.
- The Grow slider has PhotoCraft's round handle, and the native Mac panel uses dark, readable section headers.

## [0.20.2] - 2026-11-12

- Remove keeps the soft edge of a feathered PhotoCraft selection and blends the repair into a small margin of nearby background.
- H3 matches the lighting of the surrounding surface. When a single narrow dark seam enters a smooth surface from both sides, the repair can continue the seam through the removed object.

## [0.20.1] - 2026-11-12

- Fixed MiniMax H3 Image Remove leaving a red copy of the selected object. OpenFill now fills the selection with surrounding background before H3 refines it. The original image supplies all pixels outside the removal mask.
- Fixed H3 Edit and Remove failing when more than one variation was requested. H3 now samples each variation separately.

## [0.20.0] - 2026-11-12

- Added MiniMax H3 Image instruction editing and selection-aware removal through compatible existing ComfyUI servers.
- Both native and browser panels discover H3 when the server has its diffusion model, image VAE and compatible 32B text encoder. The INT8 encoder is preferred when both supported variants are available.
- H3 edits support reference images and preserve pixels outside the selection. Remove uses the highlighted selection to reconstruct the background, with Keep and Undo available as usual.

## [0.19.0] - 2026-11-12

- Use **Engine → Find servers** to find running ComfyUI instances, or **Add server** to save an HTTP(S) address or a trusted SSH alias.
- Named servers are available in the shared engine picker and the Select, Edit, and Remove assignments in both panels. OpenFill remembers them between sessions.
- macOS upgrades preserve an existing native OpenFill launcher and its Local Network permission.

## [0.18.2] - 2026-11-10

- The native Select panel now reserves space for the Grow slider and pixel field at its standard width. You can click into the field, type the amount, and apply it with Enter.

## [0.18.1] - 2026-11-10

- In Select, type an exact Grow amount from 1 to 500 document pixels or use the slider. Press Enter in the field or click Grow to apply it once. The native and browser panels share the last applied amount across sessions.

## [0.18.0] - 2026-11-10

- Drag a box in the Select preview to isolate a small or overlapping object using its actual outline. The picker can inspect the original pixels inside the box on large images. Click, Add, Subtract and Undo work with the same selection.
- All modes use one engine by default. Enable Multi-engine under Engine to assign a server to Select, Edit and Remove. Mode changes preserve server sessions, model inventories and the active edit model on additional servers.

## [0.17.0] - 2026-11-10

- The native and browser panels now have three top-level modes: **Select**, **Edit**, and **Remove**. In managed mode, Fill, Expand, and Create are available under Edit.
- Select an object by clicking its image in the panel, or ask WeVisOne-VL for a selection by description. Replace, add, or subtract real PhotoCraft selections with soft edges.
- **Grow** expands any existing selection by 1–64 document pixels per press, including selections made with a lasso. Each press is a separate Undo step and needs no model.

## [0.16.2] - 2026-11-09

- Fixed Local removal copying distant people or objects into a selection on a subtly patterned background. When the colors just outside the lasso agree, PhotoCraft samples matching background patches. Gradients and boundaries spanning different colors retain their existing repair methods.
- Automatic explains when Local can continue a subtle texture on the selected source.

## [0.16.1] - 2026-11-09

- Remove now honors the 1–5 Variations setting in the native and browser panels, including automatic suggestions and manually selected LaMa or MAT. The original repair is first; extra orientations can continue the background differently.
- Local can now offer different repairs on smooth lighting as well as texture. Identical visible repairs are shown once, with the requested and distinct counts.

## [0.16.0] - 2026-11-08

- Remove now suggests an available model from the active selection and its surrounding composite pixels. Both panels show the reason and support manual override.
- The local sampler reads PhotoCraft directly. Switching documents, revising a selection or changing sources invalidates stale suggestions before a removal can run.

## [0.15.0] - 2026-11-08

- Added selectable Big-LaMa, RORem and Qwen Image 2.1 Lightning removal on compatible existing servers. Both panels offer scene-based suggestions and manual choice.
- Repair layers carry the document's RGB profile, fixing a visible seam on Adobe RGB images. Neural removals apply the prepared selection edge once.
- FIBO's input guide preserves the original source outside the prepared mask, including the texture sampler's extra donor margin.

## [0.14.0] - 2026-11-08

- Local removal automatically reconstructs smooth backgrounds with continuous lighting instead of copying texture into large holes. Textured backgrounds continue to use PhotoCraft's content-aware fill.
- Added a small, softly blended edge margin to clear object-selection fringes. Include cast shadows in the selection when they should disappear too.
- Smooth backgrounds produce one deterministic repair. The original selection remains available, and Keep, Discard and appended choices use the prepared removal mask.

## [0.13.0] - 2026-11-08

- Added a prompt-free Remove mode to the existing-ComfyUI panels. Nearby texture samples the surrounding image in PhotoCraft and works offline. Rebuild background uses FIBO Dev and its original encoder to retouch a clean plate inside the selection.
- Both methods use the captured selection for the result layer and support variation review, Keep, and Discard. The managed engine also offers Nearby texture alongside its removal models.

## [0.12.1] - 2026-11-04

- Publish the first installable release of this PhotoCraft fork.
- Restore Qwen Image Edit 2511 Q4 downloads by pinning the file revision that matches the existing checksum.
- Skip reverse hostname lookups when starting the loopback browser panel, using the approach from the Patchy companion.

## [0.12.0-portable.1]

- Drop image files into Reference Images from the Windows native panel or the browser view.

## [0.11.2-portable.1]

- Halve the section disclosure arrows in the native panel and browser view.

## [0.11.1-portable.1]

- Make the section disclosure arrows three times larger in the native panel and browser view.

## [0.11.0-portable.1]

- Add new Image Edit and Fill results to the pending choices when you generate again on the same document.
- Put Keep, Keep all, and Discard below the thumbnails, including when there is only one result.
- Keep earlier thumbnails and their selection visible while another batch runs.

## [0.10.1-portable.1]

- Apply edited Generation controls to the next job and save them when the job is accepted.

## [0.10.0-portable.1]

- Add ordered image attachments for Qwen Image 2.1 and FIBO 2.0 edits.
- Add model visibility switches in Manage Models.
- Move settings into the main panel and remember section order and collapsed state.

## [0.9.0-portable.1]

- Add native Qwen Image 2.1 and four-step Lightning v2 RL image editing.
- Add FIBO 2.0 image editing with Preserve and Replace controls.
- Soften selection edges with configurable Auto-feather and match FIBO boundary colors.
- Discover compatible edit models from an existing local ComfyUI server.

## [0.2.4] - 2026-10-26

- Fix Mac prompt typing by keeping the expanded panel keyboard-focusable.
- Use the native Mac model menu and restore focus after selection or cancellation.
- Open the no-Tk panel in the default browser on Windows, macOS and Linux.
- Let an explicitly selected ComfyUI Python run OpenFill without installing another Python.
- Fix Linux/macOS interpreter fallback and paths containing spaces; document setup and PhotoCraft update discovery.

## [0.2.3] - 2026-10-26

- Detect long Windows paths before ComfyUI startup or download and explain the shorter-folder fix.
- Add PhotoCraft installation on Apple Silicon, tested with all four tools on an M3 MacBook Air.
- Fix Finder Python architecture, Aqua panel startup and PhotoCraft discovery on macOS.
- Add uninstall launchers on Windows, Linux and macOS, preserving settings and models.
- Document downloads, model sizes, existing-engine limits and tested Mac setup.

## [0.2.2] - 2026-10-25

### Added

- AMD graphics cards. I don't have an AMD card, so this is untested on real
  AMD hardware so far; please tell me how it goes.
  - On Windows, **Download ComfyUI** gets ComfyUI's own version for AMD cards
    (ROCm 7.2.1, 1.6 GB) for a Radeon RX 7000 or RX 9000, Radeon PRO W7000,
    or Ryzen AI 300 or Max chip, from the same ComfyUI release and checked the
    same way as the NVIDIA one. The setup card says which one it gets. For an
    older Radeon it says to follow the AMD steps in the install guide and
    choose the folder.
  - On Linux, a ComfyUI with PyTorch for ROCm works through **Choose folder**,
    and **How to install** opens the AMD steps when the card is AMD.
  - Models shows an AMD card's name and graphics memory
    ("RX 7900 XT Â· 20 GB VRAM Â· ..."), and on an AMD card the GGUF models say
    that they have been reported to give only noise on some Radeon cards.
- The README says where the model manager is: the gear icon, Models.

## [0.2.1] - 2026-10-25

### Fixed

- Keep could lose the variations. Clicking in PhotoCraft (to deselect, say)
  while Keep was still working made it fail with "select.modify.feather is not
  available right now: no selection", and the variations were gone. Keep now
  makes the kept layer from the job's own selection first and only then takes
  the variations away, a click on the way is tried again, and if Keep still
  fails the variations stay as they were and Keep can be pressed again. The
  same goes for the result a Fill, Remove or Expand adds at the end.
- Remove with LaMa failed the first time on a ComfyUI you already had, with
  "needs the custom node pack(s) openfill-nodes; press Install nodes in the
  Models tab", until another job had started ComfyUI. OpenFill now puts its
  own node pack in place before ComfyUI starts, as it does for the ComfyUI it
  downloads for you.

## [0.2.0] - 2026-10-25

First public release.

### Added

- Generative Fill, Remove object, Generative Expand and Text to Image for
  PhotoCraft 0.12 to 0.16, running on a local ComfyUI with OpenFill's engine (the
  same models, model manager, seam correction and variations as OpenFill for
  GIMP).
- The OpenFill panel next to PhotoCraft: Fill, Remove, Expand and Create, a
  live preview, variations with thumbnails, and Models and settings behind the
  gear. It folds into a small strip, and on Windows it stays with PhotoCraft's
  window (it hides when PhotoCraft is minimized and comes back with it).
- Filter > Plug-ins > OpenFill in PhotoCraft brings the panel up.
- Remove object at the image's own resolution with LaMa, through OpenFill's
  own ComfyUI node. MAT, RORem, the FLUX.1 Fill object removal LoRA and every
  fill model can be picked for Remove too.
- Windows installer (`Install OpenFill for PhotoCraft.cmd`): "PhotoCraft with
  OpenFill" in the Start menu and on the desktop. It adds Pillow to a
  python.org Python that has Tk but not Pillow.
- Windows first run: **Download ComfyUI** in the panel downloads the same
  checked portable ComfyUI and inpaint nodes OpenFill for GIMP installs, in the
  background, and uses it right away.
- Linux installer (`install.sh`): hands PhotoCraft's menu entry to OpenFill, or
  adds a separate one, and can download PhotoCraft itself.

### Changed

- Every result is one step in PhotoCraft's history, an expand included (it
  crops the canvas and adds its layers, then the steps are made into one named
  Generative Expand). Keep after picking a variation leaves it one step, with
  the kept variation alone; Discard takes the result back completely.
- Create as a layer with a selection makes a layer the size of the selection,
  placed on it, as in OpenFill for GIMP.
- A removal's layer is called Object Removal, whichever model made it.

### Fixed

- The FLUX.1 Fill removal LoRA asked for the FLUX.1 Fill Q4 GGUF (another
  6.8 GB) even when the Q6 GGUF or the full FLUX.1 Fill was installed. It now
  runs on whichever FLUX.1 Fill is there.
- Picking the active fill model in the Remove menu ran LaMa instead.
- A busy ComfyUI that did not answer one status request for a moment failed
  the job with "The AI backend is not available".
- Starting a fill without a selection from the page named GIMP's tools.
- PhotoCraft is started without its own control and automation variables
  (`PHOTOCRAFT_CONTROL_TOKEN` and the others) in the environment. A token
  exported next to OpenFill's token file stopped PhotoCraft from starting.
- Image names that start with `-` reach PhotoCraft as files, not as options
  (PhotoCraft refuses an option it does not know).

### Known limits

- Fill with FLUX or Qwen, Expand with every model, and Create work at about
  1024 px on the long side and are scaled up on large images (see the README).
  A second Expand pass at full resolution for SD 1.5 and SDXL is in the code
  but off: it has not been tested on real photos yet.
