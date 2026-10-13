#!/usr/bin/env bash
# Deploy or destroy the Terraform stack on LocalStack Community.
#   scripts/localstack.sh up      # start LocalStack, build the package, terraform apply
#   scripts/localstack.sh down    # terraform destroy, stop LocalStack
# Uses its own Terraform workspace, so it never touches real AWS state.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/terraform"
vars=(-var enable_athena=false -var pandas_layer_arn= -var "lambda_package_dir=build/package") # relative to terraform/

prepare() {
  cp "$root/localstack/localstack_override.tf" "$tf/localstack_override.tf"
  terraform -chdir="$tf" init -input=false >/dev/null
  terraform -chdir="$tf" workspace select -or-create localstack >/dev/null
}

case "${1:-}" in
  up)
    docker compose -f "$root/localstack/docker-compose.yml" up -d
    until curl -sf http://localhost:4566/_localstack/health | grep -q '"lambda": "\(available\|running\)"'; do sleep 2; done
    [ -d "$tf/build/package/pandas" ] || bash "$root/scripts/build_lambda_package.sh"
    prepare
    terraform -chdir="$tf" apply -input=false -auto-approve "${vars[@]}"
    ;;
  down)
    prepare
    terraform -chdir="$tf" destroy -input=false -auto-approve "${vars[@]}"
    terraform -chdir="$tf" workspace select default >/dev/null
    rm -f "$tf/localstack_override.tf"
    docker compose -f "$root/localstack/docker-compose.yml" down
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac
