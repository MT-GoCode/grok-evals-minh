from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
VENDOR_DIR = ROOT / "vendor"

for _d in (DATA_DIR, RESULTS_DIR, VENDOR_DIR):
    _d.mkdir(exist_ok=True)
