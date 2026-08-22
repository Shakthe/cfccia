"""Simulated wearable vital-sign streams for registered patients.

Each cycle produces one reading per patient: heart rate (bpm),
blood pressure (systolic/diastolic mmHg), oxygen saturation (SpO2 %)
and body temperature (deg C). Values are drawn from a healthy baseline
with occasional abnormal excursions so that alert paths are exercised.
"""

import random
import time

# (low, high) healthy ranges used as the simulation baseline
BASELINES = {
    "heart_rate": (62, 96),
    "systolic": (105, 130),
    "diastolic": (68, 85),
    "spo2": (95, 100),
    "temperature": (36.3, 37.2),
}

# probability that a given patient produces an abnormal excursion this cycle
ABNORMAL_PROBABILITY = 0.35

ABNORMAL_EXCURSIONS = [
    {"heart_rate": (125, 165)},                 # tachycardia
    {"heart_rate": (32, 42)},                   # bradycardia
    {"spo2": (82, 89)},                         # hypoxemia
    {"temperature": (38.6, 40.2)},              # fever
    {"systolic": (182, 205), "diastolic": (112, 125)},  # hypertensive crisis
]


def generate_reading(patient_id):
    """Generate one vitals reading for a patient."""
    reading = {
        "patient_id": patient_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "heart_rate": random.randint(*BASELINES["heart_rate"]),
        "systolic": random.randint(*BASELINES["systolic"]),
        "diastolic": random.randint(*BASELINES["diastolic"]),
        "spo2": random.randint(*BASELINES["spo2"]),
        "temperature": round(random.uniform(*BASELINES["temperature"]), 1),
    }
    if random.random() < ABNORMAL_PROBABILITY:
        excursion = random.choice(ABNORMAL_EXCURSIONS)
        for field, (low, high) in excursion.items():
            if field == "temperature":
                reading[field] = round(random.uniform(low, high), 1)
            else:
                reading[field] = random.randint(low, high)
    return reading
