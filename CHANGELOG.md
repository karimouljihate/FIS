# Changelog

## 0.3.24 - 2026-10-08

### Added

- **Water Sources card** on the project page: lists all declared water sources with a type badge (Well / Basin / Reservoir / Pump / Canal / Other, bilingual EN/AR), name, coordinates, flow rate (L/min) and notes; shows an empty-state row when none exist.
- **Water Sources section** in the Edit Project modal: dynamic add/remove rows for multiple sources, each with type, name, lat, lng, flow (L/min) and notes. Saved into `spec.water_sources`.
- Legacy single `project.water_source` Point is kept in sync with the first water source that has coordinates, so regeneration, hydrology, elevation and KML export continue to work unchanged.

### Changed

- The settings route now builds one `$set` update (spec + optional location + optional water source Point) instead of branching per field.

## 0.3.23 - 2026-10-08

### Changed

- **AI Generate Sectors** now opens a parameter modal instead of regenerating immediately. Inputs: number of sectors, min/max sector area (pre-filled from project rules), edge buffer (inward boundary inset in meters), EN/AR name prefixes, water-source mode (project water source / custom lat-lng point / ignore), elevation-data toggle (Open-Meteo), and optimization priority (balanced / water proximity / elevation uniformity / area balance / compactness). The chosen parameters are stored on each generated sector as `ai_config`.
- The regeneration engine (`regenerate_sectors`) accepts a `config` dict for all the above; scoring weights are now selectable presets (all normalized to 1.0). Min/max sector area soft-adjusts the sector count so each sector plausibly fits the bounds.

### Fixed

- AI regeneration no longer wipes existing sectors/zones/network/trees before checking that generation can proceed: the pre-clear in the route was removed (the engine already clears internally only after a valid partition is found), and a failure now flashes an error instead of a false success.

## 0.3.22 - 2026-10-08

### Added

- **Estimation tab** in the project workflow (after Hydrology 2): a whole-project quote page that auto-computes quantities from live project data — pipe and dripline lengths per diameter (m), valve and fitting counts by type, and tree count — plus KPI cards for sectors, total area, pipe length and trees. Unit prices are editable and persisted per project in `spec.estimation`, with additional custom line items, notes, currency, discount % and VAT %; subtotals and grand total recalculate live and the page is print-friendly.
- **Land Documents**: the Edit Project modal now supports multiple land/owner/other document records (name, category, reference, notes, optional file upload). Files are stored in the uploads folder and served through an authenticated download route; the project page's Land Documents card lists them with category badges and download buttons. The settings form now posts as `multipart/form-data`.

### Removed

- Min Sectors / Max Sectors fields from the project Rules card, the Edit Project modal and the create-project form; the sector-count rule is no longer part of the project spec.

## 0.3.21 - 2026-10-08

### Changed

- Project page redesign: the separate Info / Progress / Owner / Configuration / Rules / Tree Plan cards are reorganized into a single **Project** container with bilingual split headers (English — icon — Arabic) and trilingual table rows (EN label / value / AR label).
- New placeholder **Land Documents / وثائق الأراضي** card on the project page (empty table, ready for future content).
- Edit Project modal header is now a centered bilingual title bar with the close button in the middle; configuration and rules inputs use a compact 4-column grid.
- Replaced the global `.modal` padding-bottom hack and `.project-settings-modal` rules with scoped `#editSpecModal` CSS: the dialog is capped at `calc(100dvh - 120px)`, `modal-content` is a flex column so header/footer stick while the body scrolls, and a bottom margin keeps the footer above the app's fixed footer.

### Fixed

- Edit Project modal no longer silently resets **Min/Max Sectors** rules to defaults on save: the Min Sectors and Max Sectors inputs were accidentally dropped from the modal and have been restored.
- Rules card again shows the **Zones / Sector** range, which had been lost in the redesign.

## 0.3.20 - 2026-10-08

### Fixed

- Modal footer buttons still inaccessible: previous fix used `max-height` on `.modal-dialog-scrollable` which Bootstrap 5 ignores (the class uses `height: calc(100% - margin*2)` on the dialog element). Replaced with `padding-bottom: 52px !important` on `.modal` itself — this correctly shrinks Bootstrap's internal scroll area upward, keeping the modal-footer buttons always visible above the app's fixed footer.

## 0.3.19 - 2026-10-08

### Fixed

- Edit Project modal footer buttons (Cancel / Save) were hidden behind the app's fixed footer and the modal body was not scrollable: removed the conflicting `modal-dialog-centered` class and added CSS rules to cap the scrollable modal height accounting for the sticky navbar and fixed footer, raise the modal/backdrop `z-index` above the footer, and explicitly enable `overflow-y: auto` on the modal body.

## 0.3.18 - 2026-10-08

### Fixed

- Edit Project modal (`#editSpecModal`) was never shown when the "Edit / تعديل" button was clicked: the `modal-dialog` div was self-closed on the same line, placing `modal-content` outside the Bootstrap modal structure. Corrected the HTML nesting so the modal opens correctly.

## 0.3.17 - 2026-10-07

### Added

- Project information/specification cards on the project page: **Owner** (name/e-mail/phone), **Configuration / Layout** (zones per sector, row/tree spacing, emitter flow, emitters per tree), **Rules** (min/max sectors, sector area range, zones-per-sector range) and **Tree Plan** (variety, AR name, percentage, scope per zone/sector/project).
- "Edit" modal on the project page to update these settings (dynamic tree-plan rows), and an optional "Project Settings" section on the create-project page. Settings are stored per project in `project.spec` and re-merge onto safe defaults when loading.

## 0.3.16 - 2026-10-07

### Changed

- Enforced the app's default fonts application-wide so no other font appears: English text uses **Comfortaa**, Arabic text uses **VIP-Rawy** (each falls back to the other). The Bootswatch theme's fonts are overridden through `--bs-body-font-family`/`--bs-font-sans-serif` and `!important` rules across all text elements.

### Fixed

- Corrected the 0.3.15 typography pass: the bundled `VIP-Rawy` and `Comfortaa` fonts were wrongly removed; they are restored as the defaults and their `preload` links are back in `base.html`.

## 0.3.15 - 2026-10-07

### Changed

- Replaced the Bootswatch theme font with the app's default fonts (Comfortaa for English, VIP-Rawy for Arabic) across the whole app.

## 0.3.14 - 2026-10-07

### Added

- Geometry - Sectors: "Remove Sector" button (next to "Add Sector") that removes the selected sectors and their related zones, tree rows, trees, and network elements, with a confirmation prompt.

### Fixed

- Zones page no longer raises `'zone' is undefined` (500) after "AI Generate Zones"; removed the leftover per-row AI button that referenced an undefined variable.
- Global loading spinner now stays visible during navigation (animated GIF now plays) instead of being hidden immediately by `beforeunload`/`pagehide`.

## 0.3.13 - 2026-10-07

### Fixed

- Geometry - Zones: the "Validate All" button is now disabled when there are no zones.

## 0.3.12 - 2026-10-07

### Added

- Version badge in the footer (right side), sourced from `__version__` via an app context processor.

## 0.3.11 - 2026-10-07

### Removed

- Dead per-sector zone regeneration endpoint (`/sectors/<id>/zones/ai_regenerate`) and its placeholder helper `regenerate_zones_for_sector`, which imported a non-existent `irrigation.db` / ORM models and would have failed at runtime.

## 0.3.10 - 2026-10-07

### Fixed

- AI regeneration no longer crashes with `unproject_shape() takes 2 positional arguments but 3 were given`; `unproject_shape` now accepts both `(shape, utm_crs)` and `(shape, lng, lat)`.
- Updated all `project_geometry` call sites (regeneration, elevation, validation) to the `(shapely_geometry, crs)` return contract instead of a GeoJSON dict.

## 0.3.9 - 2026-10-07

### Changed

- Loading spinner now appears only after the confirmation modal is accepted for confirm-protected actions, and immediately for all other navigation and form submissions.

## 0.3.8 - 2026-10-07

### Added

- Global centered animated loading overlay on every navigation and form submission.

### Fixed

- Projection utilities now accept 3D coordinates and `boundary`/`polygon` geometry fields.
- `projected_shape` returns a shapely shape by default and supports `return_crs=True`.

## 0.3.7 - 2026-10-05

### Changed

- Replace native browser confirmation dialogs with a shared Bootstrap confirmation modal.

## 0.3.6 - 2026-10-05

### Added

- Map selection mode for Geometry sectors and zones, synchronized with table checkboxes.

## 0.3.5 - 2026-10-05

### Changed

- Apply the default English and Arabic fonts to Geometry map titles, controls, legends, and popups.

## 0.3.4 - 2026-10-05

### Changed

- Place Merge and Swap beside Add Sector in the Geometry action row.

## 0.3.3 - 2026-10-05

### Changed

- Center the Geometry Sectors and Zones tabs as a group.

## 0.3.2 - 2026-10-05

### Changed

- Split the Geometry Sectors title into English, centered icon, and Arabic columns with explicit fonts.

## 0.3.1 - 2026-10-05

### Changed

- Remove the redundant Back buttons from the Geometry Sectors and Zones pages.

## 0.3.0 - 2026-10-05

### Added

- In-map Geometry editor controls for polygons, lines, markers, and removing draft shapes.
- Polygon drawings open the existing Sector or Zone form with coordinates filled in.

## 0.2.0 - 2026-10-05

### Added

- Bilingual project workflow tabs with section-specific navigation.
- Login and registration forms in switchable tabs.
- Top-right flash toasts that dismiss after five seconds.
- Local English and Arabic fonts, including Arabic-safe numeric display.
- KML map overlays and sector import support.

### Changed

- Reworked bilingual headings, form labels, buttons, and navigation layouts.
- Added a centered Create Project action to the Projects page.

### Fixed

- Updated KML parsing for supported FastKML APIs.
- Select the largest KML polygon as the field boundary.
- Include imported KML overlays in the map API and show imported sectors in project progress.
- Remove the fixed fallback Flask session key.
