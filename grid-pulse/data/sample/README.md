# Sample data

Development-friendly Grid Pulse snapshots belong here. They let the frontend
run locally without an EIA API key or a completed GitHub Actions pipeline.

From the repository root, generate a seven-day snapshot with:

```powershell
$env:EIA_API_KEY = "your-key-here"
python pipeline/fetch_example_data.py
```

For local development, the script also reads an `eia_api_key` file in the
repository root. That file is ignored by Git. GitHub Actions should always use
the `EIA_API_KEY` repository secret instead.

The generated JSON deliberately contains only EIA records and safe metadata;
the API key and EIA's echoed request parameters are never written to disk.

Transform, validate, and build the frontend artifacts with:

```powershell
python -m pipeline.build
```

This writes `grid-pulse/data/manifest.json` and one file per region under
`grid-pulse/data/regions/`. These generated files are ignored by Git; the
deployment workflow will rebuild them before publishing the Pages artifact.
