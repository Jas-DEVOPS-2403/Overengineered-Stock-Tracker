from fastapi import FastAPI, Response
from app.config import TICKERS
from app.exporters import snapshot_to_prometheus
import json 
from pathlib import Path

app = FastAPI()
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "snapshot.json"

@app.get("/metrics")
def metrics():
    file_snap = _load_snapshot_from_file()
    if file_snap is not None:
        snapshot = file_snap
    else:
        return Response(
            content="snapshot.json not found\n",
            media_type="text/plain",
            status_code=503,
        )
        
    body, content_type = snapshot_to_prometheus(snapshot, TICKERS)
    return Response(content=body, media_type=content_type)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

def _load_snapshot_from_file():
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text())   
