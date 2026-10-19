#!/bin/sh
# Run the same wearable load against each scheduling policy on the live
# testbed and save every task record to results/testbed/<policy>.json
#   sh testbed/compare.sh 60 90          (patients, seconds)
set -e
PATIENTS=${1:-60}
DURATION=${2:-90}
GW=http://localhost:8000
mkdir -p results/testbed
for policy in laps fcfs rr mct cloud; do
  curl -s -X POST -H 'Content-Type: application/json' -d "{\"policy\": \"$policy\"}" $GW/policy
  echo
  # let the nodes drain anything left from the previous run
  until [ "$(curl -s $GW/stats | python3 -c 'import sys,json; print(sum(n["in_flight"] for n in json.load(sys.stdin)["nodes"].values()))')" = "0" ]; do
    sleep 2
  done
  curl -s -X POST $GW/reset > /dev/null
  docker compose run --rm wearables --patients "$PATIENTS" --duration "$DURATION" > /dev/null 2>&1
  # wait for the last tasks to come back before exporting
  until [ "$(curl -s $GW/stats | python3 -c 'import sys,json; print(sum(n["in_flight"] for n in json.load(sys.stdin)["nodes"].values()))')" = "0" ]; do
    sleep 2
  done
  curl -s $GW/export > "results/testbed/$policy.json"
  curl -s $GW/stats | python3 -c 'import sys,json; s=json.load(sys.stdin); print(s["policy"], {k: v["met_pct"] for k, v in s["kinds"].items()})'
done
