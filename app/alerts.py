"""Clinical threshold engine and wearable alert dispatch.

evaluate() is deliberately decoupled from the vitals source: it accepts any
reading dict, so swapping the simulator for real AWS IoT Core telemetry
requires no change to the alerting or delivery code.

Alerts are delivered to the patient's wearable device over MQTT
(AWS IoT Core topic ``vitalwatch/alerts/<device_id>``). When no IoT
endpoint is configured the publish is simulated and logged, so the demo
always works; setting real credentials activates real delivery with zero
code change.
"""

import json
import logging
import os

logger = logging.getLogger("vitalwatch.alerts")

# Clinical thresholds — overridable per environment via ConfigMap / env vars
THRESHOLDS = {
    "HR_HIGH": int(os.environ.get("HR_HIGH", 120)),        # bpm, tachycardia
    "HR_LOW": int(os.environ.get("HR_LOW", 45)),           # bpm, bradycardia
    "SPO2_LOW": int(os.environ.get("SPO2_LOW", 90)),       # %, hypoxemia
    "TEMP_HIGH": float(os.environ.get("TEMP_HIGH", 38.5)), # deg C, fever
    "SYS_HIGH": int(os.environ.get("SYS_HIGH", 180)),      # mmHg
    "DIA_HIGH": int(os.environ.get("DIA_HIGH", 110)),      # mmHg
}

IOT_ENDPOINT = os.environ.get("AWS_IOT_ENDPOINT", "")


def evaluate(reading, thresholds=None):
    """Evaluate one vitals reading against clinical thresholds.

    Returns a list of alert dicts (empty when all vitals are normal).
    """
    t = thresholds or THRESHOLDS
    alerts = []

    if reading["heart_rate"] > t["HR_HIGH"]:
        alerts.append(_alert(reading, "TACHYCARDIA ALERT",
                             f"Heart rate {reading['heart_rate']} bpm exceeds {t['HR_HIGH']} bpm"))
    elif reading["heart_rate"] < t["HR_LOW"]:
        alerts.append(_alert(reading, "BRADYCARDIA ALERT",
                             f"Heart rate {reading['heart_rate']} bpm below {t['HR_LOW']} bpm"))

    if reading["spo2"] < t["SPO2_LOW"]:
        alerts.append(_alert(reading, "LOW SPO2 ALERT",
                             f"Oxygen saturation {reading['spo2']}% below {t['SPO2_LOW']}%"))

    if reading["temperature"] > t["TEMP_HIGH"]:
        alerts.append(_alert(reading, "FEVER ALERT",
                             f"Body temperature {reading['temperature']} degC exceeds {t['TEMP_HIGH']} degC"))

    if reading["systolic"] > t["SYS_HIGH"] or reading["diastolic"] > t["DIA_HIGH"]:
        alerts.append(_alert(reading, "HYPERTENSIVE CRISIS ALERT",
                             f"Blood pressure {reading['systolic']}/{reading['diastolic']} mmHg "
                             f"exceeds {t['SYS_HIGH']}/{t['DIA_HIGH']} mmHg"))

    return alerts


def _alert(reading, category, message):
    return {
        "patient_id": reading["patient_id"],
        "timestamp": reading["timestamp"],
        "category": category,
        "message": message,
        "reading": {k: reading[k] for k in
                    ("heart_rate", "systolic", "diastolic", "spo2", "temperature")},
    }


def dispatch_to_wearable(alert, patient):
    """Push an alert to the patient's wearable over MQTT (AWS IoT Core).

    Without a configured IoT endpoint the publish is simulated and logged,
    which keeps the container fully demonstrable offline.
    """
    topic = f"vitalwatch/alerts/{patient['device_id']}"
    payload = json.dumps({"category": alert["category"], "message": alert["message"]})
    if IOT_ENDPOINT:
        # Real path: boto3 iot-data publish to the device topic.
        import boto3  # imported lazily so the simulator needs no AWS SDK
        client = boto3.client("iot-data", endpoint_url=f"https://{IOT_ENDPOINT}")
        client.publish(topic=topic, qos=1, payload=payload)
        logger.warning("WEARABLE PUSH (IoT Core) -> %s [%s] %s",
                       topic, patient["name"], alert["message"])
    else:
        logger.warning("WEARABLE PUSH (simulated MQTT) -> %s [%s] %s: %s",
                       topic, patient["name"], alert["category"], alert["message"])
    return topic
