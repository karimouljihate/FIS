# FIS (Farming Irrigation System)

Version: **0.3.1**

FIS is a Flask irrigation-planning application for organizing farm projects, field geometry, irrigation networks, trees, and engineering validation.

## Features

- Manage projects, boundaries, sectors, zones, rows, and trees.
- Import KML/KMZ geometry, including field boundaries and sector features.
- Draw polygons, lines, and markers with the Geometry map editor.
- Navigate Geometry sections with workflow tabs instead of page-level Back buttons.
- View project geometry and irrigation elements on a Leaflet map.
- Configure main and sub-pipes, valves, driplines, and network components.
- Run engineering validation and review elevation-aware reports.
- Use the bilingual English/Arabic interface.

## Requirements

- Python 3.10 or newer
- MongoDB

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGO_URI = 'mongodb://localhost:27017/irrigation_db'
$env:SECRET_KEY = 'set-a-unique-random-secret-before-use'
python app.py
```

The application is served at `http://127.0.0.1:5000`. If `SECRET_KEY` is unset, a random key is generated for local development and sessions reset when the process restarts. Set a unique, persistent `SECRET_KEY` and configure MongoDB appropriately before deployment.

## Release notes

See [CHANGELOG.md](CHANGELOG.md).