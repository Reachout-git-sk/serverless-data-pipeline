#!/usr/bin/env bash
# Build terraform/build/package: the handler plus pandas and pyarrow installed for
# Linux x86_64 / Python 3.12, for runtimes without the AWS SDK for pandas layer
# (LocalStack Community). On real AWS the managed layer is used instead.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$root/terraform/build/package"

# everything runs inside the container so file ownership doesn't matter on Linux hosts.
# boto3 is already in the Lambda runtime; caches and test suites are dropped to save space.
MSYS_NO_PATHCONV=1 docker run --rm -v "$root:/src" -w /src python:3.12-slim sh -c '
  set -e
  out=terraform/build/package
  pip install --quiet --no-cache-dir --root-user-action=ignore --disable-pip-version-check \
    --target "$out" --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 \
    "pandas>=2.2,<3" "pyarrow>=15"
  find "$out" -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} +
  cp lambda/*.py "$out"/
  chmod -R a+rwX "$out"
  du -sh "$out"
'
