set -euo pipefail

mkdir -p "${MODEL_TARGET_DIR}"
rm -rf "${MODEL_TARGET_DIR}"/*

python -m pip install --quiet --no-cache-dir 'huggingface_hub[hf_xet]'
python - <<'PY'
import os

from huggingface_hub import snapshot_download

token = None
token_file = os.environ.get("HF_TOKEN_FILE")
if token_file and os.path.exists(token_file):
    with open(token_file, encoding="utf-8") as handle:
        token = handle.read().strip() or None

snapshot_download(
    repo_id=os.environ["MODEL_SOURCE"][5:],
    local_dir=os.environ["MODEL_TARGET_DIR"],
    local_dir_use_symlinks=False,
    token=token,
)
PY

cat > "${MARKER_FILE}" <<EOF
{"source_uri":"${MODEL_SOURCE}","cache_key":"${CACHE_KEY}","scheme":"hf"}
EOF
