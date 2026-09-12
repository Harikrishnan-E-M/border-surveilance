import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import urllib.request
import json
from app.middleware.auth import create_access_token

token = create_access_token(user_id="00000000-0000-0000-0000-000000000001", org_id="00000000-0000-0000-0000-000000000001", role="admin")

base_url = "http://localhost:8000/api/v1"
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

formats = ["pdf", "xlsx", "csv"]

for fmt in formats:
    req_data = json.dumps({
        "report_type": "weekly_analytics",
        "start_date": "2026-09-01",
        "end_date": "2026-09-12",
        "format": fmt
    }).encode('utf-8')

    req = urllib.request.Request(f"{base_url}/reports/generate", data=req_data, headers=headers)
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode('utf-8'))
        r_id = body.get("data", {}).get("report_id")

    download_url = f"{base_url}/reports/{r_id}/download?token={token}"
    dl_req = urllib.request.Request(download_url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(dl_req) as resp:
        content = resp.read()
        print(f"Format {fmt.upper()} -> GENERATED & DOWNLOADED SUCCESS! ({len(content)} bytes, Content-Type: {resp.headers.get('Content-Type')})")
