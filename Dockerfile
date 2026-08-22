# VitalWatch — Smart Healthcare Remote Patient Monitoring Platform
FROM python:3.12-slim

# No .pyc files; unbuffered stdout so docker logs / kubectl logs stream alerts live
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/vitalwatch

# Dependencies first: Docker layer caching means editing source code
# never re-downloads/reinstalls packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as an unprivileged user
RUN useradd --create-home vitalwatch
USER vitalwatch

EXPOSE 5000

# Self-healing: Docker/Kubernetes detect an unhealthy container via /health
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/health').status==200 else 1)"

# Production WSGI server, 2 workers for concurrent requests
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "app.app:app"]
