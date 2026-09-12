import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import urllib.request
import json
from app.middleware.auth import create_access_token

token = create_access_token(user_id="00000000-0000-0000-0000-000000000001", org_id="00000000-0000-0000-0000-000000000001", role="admin")

base_url = "http://localhost:8000/api/v1"
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# List Persons
list_req = urllib.request.Request(f"{base_url}/faces/persons", headers=headers)
with urllib.request.urlopen(list_req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    items = res.get("data", {}).get("items", [])
    print(f"ENROLLED PERSONS COUNT: {len(items)}")

# Test thumbnail for first person
if items:
    p = items[0]
    thumb_url = f"http://localhost:8000{p['thumbnail_url']}"
    print("Testing thumbnail URL:", thumb_url)
    with urllib.request.urlopen(thumb_url) as resp:
        content = resp.read()
        print(f"THUMBNAIL FETCH SUCCESS! Content length: {len(content)} bytes, Content-Type: {resp.headers.get('Content-Type')}")
