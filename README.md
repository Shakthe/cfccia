# VitalWatch — Smart Healthcare Remote Patient Monitoring Platform

Cloud and Fog Computing for IoT (CS4001) — CIA-1, Question 2.

Cloud-based Remote Patient Monitoring System that continuously monitors
patient vital signs (heart rate, blood pressure, oxygen saturation, body
temperature) streamed from wearable devices, evaluates them against clinical
thresholds, and pushes alerts back to the patient's wearable device over
MQTT (AWS IoT Core).

## Layout

| Path | Contents |
|---|---|
| `app/` | Flask service: vitals simulator, threshold engine, wearable alert dispatch, live dashboard |
| `tests/` | pytest suite (8 tests) |
| `Dockerfile` | Container image (python:3.12-slim, non-root, healthcheck, gunicorn) |
| `k8s/` | Namespace, ConfigMap, Secret, Deployment (2 replicas + probes), NodePort Service, HPA |
| `.github/workflows/ci-cd.yml` | Test → Build & Push to Docker Hub → Deploy to Kubernetes |

## Run locally

```bash
docker build -t shakthe1311/vitalwatch:v1 .
docker run -d -p 5001:5000 shakthe1311/vitalwatch:v1
open http://localhost:5001        # live dashboard
curl http://localhost:5001/health
```

## Deploy to Kubernetes

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/
kubectl get all -n vitalwatch
kubectl port-forward -n vitalwatch svc/vitalwatch 8080:80
```
