FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# iproute2 gives the "tc" command used to add WAN delay in front of the cloud node
RUN apt-get update \
 && apt-get install -y --no-install-recommends iproute2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/vitalwatch-fog

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fogsim ./fogsim
COPY testbed ./testbed

RUN useradd --create-home vitalwatch
USER vitalwatch

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

ENTRYPOINT ["testbed/entrypoint.sh"]
