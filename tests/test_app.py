import os

os.environ["DISABLE_MONITOR"] = "1"

import pytest

from app import alerts, vitals
from app.app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _reading(**overrides):
    base = {"patient_id": "P-TEST", "timestamp": "2026-08-22T00:00:00Z",
            "heart_rate": 75, "systolic": 118, "diastolic": 76,
            "spo2": 98, "temperature": 36.8}
    base.update(overrides)
    return base


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "healthy"


def test_register_and_list_patients(client):
    resp = client.post("/patients", json={"patient_id": "P-9001", "name": "Test Patient",
                                          "device_id": "wearable-tp-09"})
    assert resp.status_code == 201
    ids = [p["patient_id"] for p in client.get("/patients").get_json()]
    assert "P-9001" in ids


def test_generated_reading_shape():
    r = vitals.generate_reading("P-1001")
    for field in ("heart_rate", "systolic", "diastolic", "spo2", "temperature"):
        assert field in r


def test_normal_vitals_produce_no_alert():
    assert alerts.evaluate(_reading()) == []


def test_tachycardia_alert():
    fired = alerts.evaluate(_reading(heart_rate=140))
    assert any(a["category"] == "TACHYCARDIA ALERT" for a in fired)


def test_low_spo2_alert():
    fired = alerts.evaluate(_reading(spo2=85))
    assert any(a["category"] == "LOW SPO2 ALERT" for a in fired)


def test_fever_and_hypertension_alerts():
    fired = alerts.evaluate(_reading(temperature=39.4, systolic=190, diastolic=118))
    categories = {a["category"] for a in fired}
    assert {"FEVER ALERT", "HYPERTENSIVE CRISIS ALERT"} <= categories


def test_refresh_dispatches_alerts_to_wearable(client, monkeypatch):
    monkeypatch.setattr(vitals, "generate_reading",
                        lambda pid: _reading(patient_id=pid, spo2=84))
    body = client.post("/vitals/refresh").get_json()
    assert body["alerts"], "expected at least one alert"
    assert all(a["topic"].startswith("vitalwatch/alerts/") for a in body["alerts"])
