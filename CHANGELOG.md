# Changelog

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
