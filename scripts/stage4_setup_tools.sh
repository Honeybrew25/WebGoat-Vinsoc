#!/usr/bin/env bash
# ============================================================================
#  stage4_setup_tools.sh — Giai đoạn 4 (bước 1): cài/build các tool SAST
# ============================================================================
#  Chạy MỘT LẦN trước khi benchmark. Mỗi tool cài theo cách riêng của nó:
#    - datadog-saist : Go -> build thành Docker image (host không cần cài Go)
#    - arm-metis     : Python -> cài bằng uv vào venv riêng
#
#  Dùng:
#    bash scripts/stage4_setup_tools.sh            # cài mọi tool enabled
#    bash scripts/stage4_setup_tools.sh saist      # chỉ SAIST
#    bash scripts/stage4_setup_tools.sh metis      # chỉ Metis
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

TOOLS_DIR="$ROOT_DIR/tools"
ONLY="${1:-all}"

setup_saist() {
  c_info "=== SAIST (Datadog) ==="
  local repo dest
  repo="$(bench_cfg tool datadog-saist repo)"
  dest="$TOOLS_DIR/datadog-saist"

  if [[ ! -d "$dest/.git" ]]; then
    c_info "Clone SAIST..."
    git clone --depth 1 "$repo" "$dest"
  else
    c_info "Đã có source SAIST."
  fi
  # Ghi lại commit của TOOL (khác với SHA của target) để báo cáo tái lập được.
  local tool_sha
  tool_sha="$(git -C "$dest" rev-parse HEAD)"
  c_info "SAIST commit: $tool_sha"

  c_info "Build Docker image (lần đầu tải Go deps, hơi lâu)..."
  docker build -f "$TOOLS_DIR/saist.Dockerfile" -t sast-bench/saist:pinned "$dest"
  c_ok "SAIST sẵn sàng: image sast-bench/saist:pinned"
  echo "$tool_sha" > "$TOOLS_DIR/.saist_commit"
}

setup_metis() {
  c_info "=== Arm Metis ==="
  local repo dest
  repo="$(bench_cfg tool arm-metis repo)"
  dest="$TOOLS_DIR/arm-metis"

  if [[ ! -d "$dest/.git" ]]; then
    c_info "Clone Metis..."
    git clone --depth 1 "$repo" "$dest"
  else
    c_info "Đã có source Metis."
  fi
  local tool_sha
  tool_sha="$(git -C "$dest" rev-parse HEAD)"
  c_info "Metis commit: $tool_sha"

  c_info "Tạo venv + cài Metis (kèm extra 'gemini')..."
  ( cd "$dest" && uv venv && uv pip install '.[gemini]' )
  c_ok "Metis sẵn sàng: $dest/.venv"
  echo "$tool_sha" > "$TOOLS_DIR/.metis_commit"
}

case "$ONLY" in
  all)    setup_saist; echo; setup_metis ;;
  saist)  setup_saist ;;
  metis)  setup_metis ;;
  *) c_err "Tham số lạ: $ONLY (dùng: all | saist | metis)"; exit 1 ;;
esac

echo
c_ok "Cài tool xong. Tiếp: bash scripts/stage4_run.sh --dry-run"
