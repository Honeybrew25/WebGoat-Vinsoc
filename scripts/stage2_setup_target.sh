#!/usr/bin/env bash
# ============================================================================
#  stage2_setup_target.sh — Giai đoạn 2: dựng môi trường target offline
# ============================================================================
#  Việc nó làm:
#   1. Clone WebGoat về target/WebGoat
#   2. Checkout ĐÚNG SHA đã pin trong config -> đóng băng phiên bản code
#   3. Kiểm tra SHA khớp (fail sớm nếu lệch)
#   4. Tạo "index" offline: danh sách file .java + thống kê (LoC) cho harness dùng
#   5. (tuỳ chọn) build bằng ./mvnw để warm cache maven -> lần sau chạy offline
#
#  Dùng:
#     bash scripts/stage2_setup_target.sh            # clone + index (mặc định)
#     bash scripts/stage2_setup_target.sh --build    # + build offline warm cache
#     bash scripts/stage2_setup_target.sh --force     # xoá clone cũ, làm lại
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

DO_BUILD=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --build) DO_BUILD=1 ;;
    --force) FORCE=1 ;;
    *) c_warn "Bỏ qua tham số lạ: $arg" ;;
  esac
done

REPO="$(yaml_get 'target.repo')"
SHA="$(yaml_get 'target.sha')"
REF="$(yaml_get 'target.ref')"
REL_PATH="$(yaml_get 'target.local_path')"
DEST="$ROOT_DIR/$REL_PATH"

c_info "Repo   : $REPO"
c_info "Ref    : $REF"
c_info "SHA    : $SHA"
c_info "Đích   : $DEST"

# --- 0. Xử lý clone cũ --------------------------------------------------------
if [[ -d "$DEST/.git" ]]; then
  if [[ $FORCE -eq 1 ]]; then
    c_warn "--force: xoá clone cũ"
    rm -rf "$DEST"
  else
    c_info "Đã có clone. Bỏ qua clone (dùng --force để làm lại)."
  fi
fi

# --- 1. Clone -----------------------------------------------------------------
if [[ ! -d "$DEST/.git" ]]; then
  c_info "Đang clone (chỉ lấy lịch sử cần thiết)..."
  mkdir -p "$(dirname "$DEST")"
  # Clone đầy đủ để chắc chắn checkout được SHA (shallow đôi khi thiếu commit).
  git clone "$REPO" "$DEST"
  c_ok "Clone xong."
fi

# --- 2. Checkout đúng SHA -----------------------------------------------------
c_info "Checkout SHA đã pin..."
git -C "$DEST" fetch --tags --quiet || true
git -C "$DEST" checkout --quiet "$SHA"

# --- 3. Xác minh SHA khớp -----------------------------------------------------
ACTUAL="$(git -C "$DEST" rev-parse HEAD)"
if [[ "$ACTUAL" != "$SHA" ]]; then
  c_err "SHA KHÔNG khớp! mong đợi=$SHA thực tế=$ACTUAL"
  exit 1
fi
c_ok "SHA khớp: $ACTUAL"

# --- 4. Index offline ---------------------------------------------------------
#  Tạo bản kê file nguồn + thống kê để: (a) cắt web tool - harness chỉ đọc local,
#  (b) biết quy mô target, (c) cho adapter tool nạp danh sách file.
INDEX_DIR="$ROOT_DIR/results/stats"
mkdir -p "$INDEX_DIR"
FILELIST="$INDEX_DIR/target_java_files.txt"
INDEX_JSON="$INDEX_DIR/target_index.json"

c_info "Đang liệt kê file .java..."
# Chỉ lấy mã nguồn Java, bỏ thư mục test/build/target nếu muốn (giữ test cũng ok).
( cd "$DEST" && git ls-files '*.java' ) > "$FILELIST"
NUM_FILES=$(wc -l < "$FILELIST" | tr -d ' ')

# Tổng số dòng code Java (LoC thô)
TOTAL_LOC=0
while IFS= read -r f; do
  [[ -f "$DEST/$f" ]] || continue
  n=$(wc -l < "$DEST/$f")
  TOTAL_LOC=$((TOTAL_LOC + n))
done < "$FILELIST"

cat > "$INDEX_JSON" <<EOF
{
  "target": "WebGoat",
  "ref": "$REF",
  "sha": "$ACTUAL",
  "local_path": "$REL_PATH",
  "num_java_files": $NUM_FILES,
  "total_java_loc": $TOTAL_LOC,
  "file_list": "results/stats/target_java_files.txt",
  "indexed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

c_ok "Index xong: $NUM_FILES file Java, ~$TOTAL_LOC dòng."
c_info "  -> $FILELIST"
c_info "  -> $INDEX_JSON"

# --- 5. (tuỳ chọn) build offline-warm ----------------------------------------
if [[ $DO_BUILD -eq 1 ]]; then
  c_info "Build bằng ./mvnw (lần đầu cần internet để tải deps, sau đó offline được)..."
  if [[ -f "$DEST/mvnw" ]]; then
    ( cd "$DEST" && ./mvnw -q -DskipTests compile ) \
      && c_ok "Build compile xong. Maven cache đã warm." \
      || c_warn "Build lỗi — không sao, SAST chủ yếu cần source, không cần build."
  else
    c_warn "Không thấy ./mvnw — bỏ qua build."
  fi
fi

echo
c_ok "GIAI ĐOẠN 2 HOÀN TẤT. Target đã đóng băng tại SHA $ACTUAL."
c_info "Nhắc: harness phải đọc code từ '$REL_PATH' và KHÔNG dùng web tool"
c_info "      (config internet.mode = $(yaml_get 'internet.mode'))."
