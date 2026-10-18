#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
image_name=wujie-libero-practice:20261018-final
if [[ "${BUILD:-0}" != 1 && -f wujie-libero-practice-20261018-final.tar.gz ]] && ! docker image inspect "$image_name" >/dev/null 2>&1; then
  docker load -i wujie-libero-practice-20261018-final.tar.gz
fi
if [[ "${BUILD:-0}" == 1 ]] || ! docker image inspect "$image_name" >/dev/null 2>&1; then
  docker build -f Dockerfile.environment -t "$image_name" .
fi
docker run --rm --network none -v "$PWD:/workspace" --entrypoint python "$image_name" /workspace/pack.py
output="${2:-runs/$(date +%Y%m%d-%H%M%S)}"
set +e
docker run --rm --cpus 4 --memory 4g --network "${UESTC_DOCKER_NETWORK:-bridge}" -v "$PWD:/workspace" -e UESTC_MODEL_API_KEY -e UESTC_MODEL_API_URL \
  --entrypoint python "$image_name" /workspace/practice.py --package agent.zip --scenes "${1:-practice-scenes.json}" --output "$output"
result=$?
[[ ! -f "$output/review.html" ]] || printf 'Review: %s/%s/review.html\n' "$PWD" "$output"
exit "$result"
