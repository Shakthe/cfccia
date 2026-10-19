#!/bin/sh
# One image, four roles: node | gateway | wearables | sim
set -e
case "${ROLE:-gateway}" in
  node)
    exec gunicorn --workers 1 --worker-class gthread --threads 128 \
      --bind 0.0.0.0:8000 testbed.node:app ;;
  gateway)
    exec gunicorn --workers 1 --worker-class gthread --threads 256 \
      --bind 0.0.0.0:8000 testbed.gateway:app ;;
  wearables)
    exec python -m testbed.wearables "$@" ;;
  sim)
    exec python -m fogsim.run_experiments "$@" ;;
  *)
    echo "unknown ROLE=$ROLE (use node, gateway, wearables or sim)" >&2
    exit 1 ;;
esac
