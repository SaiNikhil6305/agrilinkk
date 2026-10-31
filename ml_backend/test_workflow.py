"""End-to-end tests: farmer -> FPO -> buyer -> dispatch -> delivery -> settlement, plus guards.
Run: cd ml_backend && python -m pytest -q test_workflow.py
"""
import io
import os
import tempfile
import threading
from datetime import date, timedelta

os.environ["AGRI_DB_PATH"] = tempfile.mktemp(suffix=".db")

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server

client = TestClient(server.app)
client.__enter__()  # run startup (init_db)


def reg(role, name, email):
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": "secret123", "role": role,
                                                 "organisation": name + " Org", "location": "Vellore"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


F1 = reg("farmer", "Ravi", "ravi@x.in")
F2 = reg("farmer", "Selvi", "selvi@x.in")
AG = reg("aggregator", "Vellore FPO", "fpo@x.in")
AG2 = reg("aggregator", "Other FPO", "fpo2@x.in")
BU = reg("buyer", "Mill Co", "mill@x.in")
TODAY = date.today().isoformat()


def submit(h, qty, price=2400):
    r = client.post("/api/farmers/register", headers=h, json={"crop": "Wheat", "quantity_qtl": qty, "expected_price_per_qtl": price,
                                                              "location": "Katpadi", "harvest_date": TODAY})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert client.post(f"/api/aggregator/inflow/{sid}/review", headers=AG, json={"decision": "accept", "grade": "A"}).status_code == 200
    return sid


def test_role_cannot_be_escalated():
    assert client.post("/api/lots/create", headers=F1, json={}).status_code in (403, 422)
    r = client.put("/api/auth/profile", headers=F1, json={"role": "aggregator", "name": "Ravi"})
    assert r.json()["role"] == "farmer"
    assert client.get("/api/aggregator/inflow").status_code == 401


def test_invalid_image_rejected():
    r = client.post("/api/predict", headers=F1, files={"image": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_predict_without_models_is_honest():
    buf = io.BytesIO(); Image.new("RGB", (200, 200), (180, 150, 60)).save(buf, "JPEG")
    r = client.post("/api/predict", headers=F1, files={"image": ("w.jpg", buf.getvalue(), "image/jpeg")})
    if not any(v["installed"] for v in server.inference.model_status().values()):
        assert r.status_code == 503


def test_full_workflow_and_ledger():
    s1, s2 = submit(F1, 25, 2400), submit(F2, 15, 2450)
    # overselling: farmer 1 has only 25
    bad = client.post("/api/lots/create", headers=AG, json={"crop": "Wheat", "grade": "A", "base_price_per_qtl": 2600,
                                                             "allocations": [{"submission_id": s1, "quantity": 30}]})
    assert bad.status_code == 409
    lot = client.post("/api/lots/create", headers=AG, json={"crop": "Wheat", "grade": "A", "base_price_per_qtl": 2600,
                                                             "allocations": [{"submission_id": s1, "quantity": 25},
                                                                             {"submission_id": s2, "quantity": 15}]}).json()
    assert lot["total_quantity"] == 40
    # duplicate allocation of same batch
    assert client.post("/api/lots/allocate", headers=AG, json={"lot_id": lot["id"], "submission_id": s1, "quantity": 1}).status_code == 409

    req = client.post("/api/buyer/requests", headers=BU, json={"crop": "Wheat", "quantity_qtl": 40, "min_grade": "B", "target_price": 2700,
                                                                "delivery_location": "Chennai",
                                                                "delivery_date": (date.today() + timedelta(days=5)).isoformat(),
                                                                "idempotency_key": "req-key-1"}).json()
    again = client.post("/api/buyer/requests", headers=BU, json={"crop": "Wheat", "quantity_qtl": 40, "min_grade": "B", "target_price": 2700,
                                                                  "delivery_location": "Chennai",
                                                                  "delivery_date": (date.today() + timedelta(days=5)).isoformat(),
                                                                  "idempotency_key": "req-key-1"}).json()
    assert again["id"] == req["id"]
    m = client.get(f"/api/matchmaking/suggestions/{req['id']}", headers=BU).json()
    assert m["matches"][0]["id"] == lot["id"]

    t = client.post("/api/procure", headers=BU, json={"lot_id": lot["id"], "request_id": req["id"], "idempotency_key": "po-key-0001"}).json()
    assert t["crop_sales_revenue"] == 104000.0
    assert t["farmer_procurement_cost"] == 25 * 2400 + 15 * 2450
    dup = client.post("/api/procure", headers=BU, json={"lot_id": lot["id"], "idempotency_key": "po-key-0001"}).json()
    assert dup["id"] == t["id"]
    assert client.post("/api/procure", headers=BU, json={"lot_id": lot["id"], "idempotency_key": "po-key-0002"}).status_code == 409

    # other aggregator cannot touch it
    assert client.post(f"/api/transactions/{t['id']}/dispatch", headers=AG2, json={"transport_charges": 1, "vehicle_number": "TN23AB1234", "carrier": "XY Carriers"}).status_code == 404
    assert client.post("/api/transactions/complete", headers=BU, json={"transaction_id": t["id"]}).status_code == 409  # not dispatched
    assert client.post(f"/api/transactions/{t['id']}/dispatch", headers=AG, json={"transport_charges": 3000, "vehicle_number": "TN23AB1234", "carrier": "Sri Logistics"}).status_code == 200
    assert client.post("/api/transactions/complete", headers=BU, json={"transaction_id": t["id"]}).status_code == 200
    assert client.post("/api/transactions/complete", headers=BU, json={"transaction_id": t["id"]}).status_code == 409  # exactly once

    d = client.get(f"/api/transactions/{t['id']}", headers=AG).json()
    assert d["aggregator_net_earnings"] == 104000 - 96750 - 3000
    p1 = [p for p in d["farmer_payments"] if p["farmer_name"] == "Ravi"][0]
    assert client.post("/api/payments/farmer/record", headers=AG, json={"payment_id": p1["id"], "deductions": 500, "payment_reference": "UTR1234",
                                                                         "payment_method": "UPI"}).status_code == 422  # no reason
    assert client.post("/api/payments/farmer/record", headers=AG, json={"payment_id": p1["id"], "deductions": 500, "deduction_reason": "Gunny bags",
                                                                         "payment_reference": "UTR1234", "payment_method": "UPI"}).status_code == 200
    e = client.get("/api/earnings/farmer", headers=F1).json()
    assert (e["gross"], e["deductions"], e["net"], e["paid"]) == (60000, 500, 59500, 59500)
    # farmer 2 cannot see farmer 1's payment
    d2 = client.get(f"/api/transactions/{t['id']}", headers=F2).json()
    assert all(p["farmer_name"] == "Selvi" for p in d2["farmer_payments"])

    p2 = [p for p in d["farmer_payments"] if p["farmer_name"] == "Selvi"][0]
    client.post("/api/payments/farmer/record", headers=AG, json={"payment_id": p2["id"], "payment_reference": "UTR5678", "payment_method": "NEFT"})
    assert client.post("/api/payments/buyer/record", headers=BU, json={"transaction_id": t["id"], "payment_reference": "x1234", "payment_method": "NEFT"}).status_code == 403
    assert client.post("/api/payments/buyer/record", headers=AG, json={"transaction_id": t["id"], "payment_reference": "RTGS999", "payment_method": "RTGS"}).status_code == 200
    assert client.get(f"/api/transactions/{t['id']}", headers=BU).json()["status"] == "Settled"
    assert client.get(f"/api/transactions/{t['id']}/invoice", headers=BU).status_code == 200
    eb = client.get("/api/earnings/buyer", headers=BU).json()
    assert eb["total_spend"] == 104000 and eb["outstanding"] == 0


def test_concurrent_allocation_never_oversells():
    sid = submit(F1, 10)
    results = []

    def go(h):
        r = client.post("/api/lots/create", headers=h, json={"crop": "Wheat", "grade": "A", "base_price_per_qtl": 2600,
                                                              "allocations": [{"submission_id": sid, "quantity": 8}]})
        results.append(r.status_code)

    ths = [threading.Thread(target=go, args=(h,)) for h in (AG, AG2, AG, AG2)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert results.count(200) == 1 and results.count(409) == 3
    subs = client.get("/api/aggregator/inflow", headers=AG).json()
    assert [s for s in subs if s["id"] == sid][0]["available_quantity"] == 2
