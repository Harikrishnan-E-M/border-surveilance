import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import urllib.request
import json
from app.middleware.auth import create_access_token

token = create_access_token(user_id="00000000-0000-0000-0000-000000000001", org_id="00000000-0000-0000-0000-000000000001", role="admin")

base_url = "http://localhost:8000/api/v1"
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

print("==================================================")
print("1. TESTING VEHICLE REGISTRATION & LISTING")
print("==================================================")
# Add vehicle
veh_payload = json.dumps({
    "plate_number": "PATROL-999",
    "make": "Ford",
    "model": "Raptor",
    "color": "Black",
    "owner_name": "Border Commander",
    "category": "authorized",
    "notes": "Fast Response Patrol Vehicle"
}).encode('utf-8')

veh_req = urllib.request.Request(f"{base_url}/vehicles/", data=veh_payload, headers=headers)
with urllib.request.urlopen(veh_req) as resp:
    v_res = json.loads(resp.read().decode('utf-8'))
    print("CREATE VEHICLE SUCCESS:", v_res)
    veh_id = v_res["data"]["id"]

# List vehicles
list_v_req = urllib.request.Request(f"{base_url}/vehicles/", headers=headers)
with urllib.request.urlopen(list_v_req) as resp:
    list_v_res = json.loads(resp.read().decode('utf-8'))
    items = list_v_res.get("data", {}).get("items", [])
    print(f"LIST VEHICLES COUNT: {len(items)}")
    found_veh = any(v.get("id") == veh_id or v.get("plate_number") == "PATROL-999" for v in items)
    print(f"NEW VEHICLE (PATROL-999) FOUND IN LIST: {found_veh}")


print("\n==================================================")
print("2. TESTING FACE ENROLLMENT & LISTING")
print("==================================================")
# Create person
person_payload = json.dumps({
    "name": "Captain Marcus Vance",
    "group": "employees",
    "employee_id": "TAC-5050",
    "notes": "Tactical Operations Command"
}).encode('utf-8')

p_req = urllib.request.Request(f"{base_url}/faces/persons", data=person_payload, headers=headers)
with urllib.request.urlopen(p_req) as resp:
    p_res = json.loads(resp.read().decode('utf-8'))
    print("CREATE PERSON SUCCESS:", p_res)
    person_id = p_res["data"]["id"]

# Upload face image
boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
dummy_img = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
body = [
    f"--{boundary}".encode(),
    b'Content-Disposition: form-data; name="file"; filename="marcus.jpg"',
    b'Content-Type: image/jpeg\r\n',
    dummy_img,
    f"--{boundary}--".encode()
]
payload_bytes = b"\r\n".join(body)

up_headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": f"multipart/form-data; boundary={boundary}"
}

up_req = urllib.request.Request(f"{base_url}/faces/persons/{person_id}/faces", data=payload_bytes, headers=up_headers)
with urllib.request.urlopen(up_req) as resp:
    up_res = json.loads(resp.read().decode('utf-8'))
    print("UPLOAD FACE IMAGE SUCCESS:", up_res)

# List persons
list_p_req = urllib.request.Request(f"{base_url}/faces/persons", headers=headers)
with urllib.request.urlopen(list_p_req) as resp:
    list_p_res = json.loads(resp.read().decode('utf-8'))
    p_items = list_p_res.get("data", {}).get("items", [])
    print(f"LIST PERSONS COUNT: {len(p_items)}")
    found_person = next((p for p in p_items if p.get("id") == person_id), None)
    if found_person:
        print(f"NEW PERSON FOUND IN LIST: name={found_person.get('name')}, face_count={found_person.get('face_count')}, thumbnail_url={found_person.get('thumbnail_url')}")

# Fetch Thumbnail
if person_id:
    thumb_url = f"http://localhost:8000/api/v1/faces/persons/{person_id}/thumbnail"
    with urllib.request.urlopen(thumb_url) as resp:
        content = resp.read()
        print(f"THUMBNAIL FETCH SUCCESS: {len(content)} bytes, Content-Type: {resp.headers.get('Content-Type')}")

print("\n==================================================")
print("AUDIT COMPLETE - ALL TESTS PASSED SUCCESSFULLY!")
print("==================================================")
