# MuseCell

**MuseCell is an autonomous screening scientist.** Give it a blinded compound and a small budget of microscope wells. It images real cells, measures them, forms mechanism-of-action hypotheses, chooses the next experiment that best separates them, has its conclusion reviewed by a second model, and only then unblinds the compound and checks itself against chemistry databases and literature.

*Research tool for cell-biology screening data. Not for clinical use.*

> Work in progress. Built during the Meta AI Developer Hackathon (Oct 26 – Nov 4, 2026).

## Quickstart (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item .env.example backend/.env   # then fill in MODEL_API_KEY
python scripts/probe_meta_api.py
```

## Data

See [DATA_LICENSE.md](DATA_LICENSE.md). BBBC021v1 (Caie et al., 2010), Broad Bioimage Benchmark Collection; IDR idr0035, CC BY 4.0, images © AstraZeneca.

## License

Code: MIT ([LICENSE](LICENSE)).
