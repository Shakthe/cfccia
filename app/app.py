"""VitalWatch — Smart Healthcare Remote Patient Monitoring Platform.

Flask service that continuously monitors patient vital signs streamed from
wearable devices (simulated), evaluates them against clinical thresholds,
and pushes alerts back to the patient's wearable device.
"""

import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template_string, request

from app import alerts, vitals

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vitalwatch")

app = Flask(__name__)

MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", 15))

# In-memory stores (a production deployment would use DynamoDB / Timestream)
PATIENTS = {}
LATEST_READINGS = {}
ALERT_HISTORY = []
_lock = threading.Lock()


def _seed_patients():
    for p in (
        {"patient_id": "P-1001", "name": "Arun Kumar", "age": 67,
         "device_id": "wearable-ak-01", "condition": "post-cardiac surgery"},
        {"patient_id": "P-1002", "name": "Meera Nair", "age": 54,
         "device_id": "wearable-mn-02", "condition": "COPD"},
    ):
        PATIENTS[p["patient_id"]] = p


_seed_patients()


def run_monitor_cycle():
    """One monitoring pass: read vitals for every patient, evaluate, alert."""
    cycle_alerts = []
    with _lock:
        for pid, patient in PATIENTS.items():
            reading = vitals.generate_reading(pid)
            LATEST_READINGS[pid] = reading
            for alert in alerts.evaluate(reading):
                alert["topic"] = alerts.dispatch_to_wearable(alert, patient)
                ALERT_HISTORY.append(alert)
                cycle_alerts.append(alert)
        del ALERT_HISTORY[:-50]  # keep the most recent 50
    return cycle_alerts


def _monitor_loop():
    while True:
        try:
            run_monitor_cycle()
        except Exception:
            logger.exception("monitor cycle failed")
        time.sleep(MONITOR_INTERVAL)


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "patients": len(PATIENTS),
                    "monitor_interval_s": MONITOR_INTERVAL})


@app.route("/patients", methods=["GET", "POST"])
def patients():
    if request.method == "POST":
        body = request.get_json(force=True)
        required = {"patient_id", "name", "device_id"}
        if not body or not required.issubset(body):
            return jsonify({"error": f"required fields: {sorted(required)}"}), 400
        with _lock:
            PATIENTS[body["patient_id"]] = body
        logger.info("registered patient %s (%s)", body["patient_id"], body["name"])
        return jsonify(body), 201
    return jsonify(list(PATIENTS.values()))


@app.route("/vitals")
def latest_vitals():
    return jsonify(list(LATEST_READINGS.values()))


@app.route("/vitals/refresh", methods=["POST"])
def refresh():
    fired = run_monitor_cycle()
    return jsonify({"readings": list(LATEST_READINGS.values()), "alerts": fired})


@app.route("/alerts")
def alert_history():
    return jsonify(ALERT_HISTORY)


DASHBOARD = """
<!doctype html><html><head><title>VitalWatch — Remote Patient Monitoring</title>
<meta http-equiv="refresh" content="10">
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;margin:2rem;background:#f4f7fb;color:#16324f}
 h1{color:#0b5394} .sub{color:#5a7184}
 table{border-collapse:collapse;background:#fff;box-shadow:0 1px 3px rgba(22,50,79,.15);margin-bottom:2rem;width:100%}
 th,td{padding:.55rem .9rem;border-bottom:1px solid #e3eaf3;text-align:left}
 th{background:#0b5394;color:#fff} .bad{color:#c0392b;font-weight:700}
 .pill{background:#fdecea;color:#c0392b;border-radius:4px;padding:.15rem .5rem;font-size:.85rem;font-weight:600}
</style></head><body>
<h1>VitalWatch &mdash; Remote Patient Monitoring</h1>
<p class="sub">Live vitals from wearable devices &middot; auto-refreshes every 10&nbsp;s</p>
<h2>Registered patients</h2>
<table><tr><th>ID</th><th>Name</th><th>Age</th><th>Condition</th><th>Wearable</th></tr>
{% for p in patients %}<tr><td>{{p.patient_id}}</td><td>{{p.name}}</td><td>{{p.get('age','—')}}</td>
<td>{{p.get('condition','—')}}</td><td>{{p.device_id}}</td></tr>{% endfor %}</table>
<h2>Latest vitals</h2>
<table><tr><th>Patient</th><th>Heart rate</th><th>BP</th><th>SpO2</th><th>Temp</th><th>Time (UTC)</th></tr>
{% for r in readings %}<tr><td>{{r.patient_id}}</td>
<td class="{{'bad' if r.heart_rate>120 or r.heart_rate<45 else ''}}">{{r.heart_rate}} bpm</td>
<td class="{{'bad' if r.systolic>180 or r.diastolic>110 else ''}}">{{r.systolic}}/{{r.diastolic}}</td>
<td class="{{'bad' if r.spo2<90 else ''}}">{{r.spo2}}%</td>
<td class="{{'bad' if r.temperature>38.5 else ''}}">{{r.temperature}} &deg;C</td>
<td>{{r.timestamp}}</td></tr>{% endfor %}</table>
<h2>Recent alerts pushed to wearables</h2>
<table><tr><th>Time (UTC)</th><th>Patient</th><th>Alert</th><th>Detail</th><th>MQTT topic</th></tr>
{% for a in alerts[-10:]|reverse %}<tr><td>{{a.timestamp}}</td><td>{{a.patient_id}}</td>
<td><span class="pill">{{a.category}}</span></td><td>{{a.message}}</td><td>{{a.get('topic','')}}</td></tr>{% endfor %}</table>
</body></html>
"""


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD, patients=list(PATIENTS.values()),
                                  readings=list(LATEST_READINGS.values()),
                                  alerts=ALERT_HISTORY)


def start_background_monitor():
    t = threading.Thread(target=_monitor_loop, daemon=True)
    t.start()
    logger.info("background monitor started (interval %ss)", MONITOR_INTERVAL)


if os.environ.get("DISABLE_MONITOR") != "1":
    start_background_monitor()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
