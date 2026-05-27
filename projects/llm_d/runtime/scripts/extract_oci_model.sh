set -euo pipefail

mkdir -p "${MODEL_TARGET_DIR}"
rm -rf "${MODEL_TARGET_DIR}"/*

auth_args=()
if [[ -n "${REGISTRY_AUTH_FILE:-}" && -f "${REGISTRY_AUTH_FILE}" ]]; then
  auth_args+=(--registry-config="${REGISTRY_AUTH_FILE}")
fi

oc image extract "${MODEL_SOURCE#oci://}" \
  --path "${OCI_IMAGE_PATH}:${MODEL_TARGET_DIR}" \
  --confirm \
  "${auth_args[@]}"

cat > "${MARKER_FILE}" <<EOF
{"source_uri":"${MODEL_SOURCE}","cache_key":"${CACHE_KEY}","scheme":"oci","image_path":"${OCI_IMAGE_PATH}"}
EOF
