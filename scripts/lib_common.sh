#!/usr/bin/env bash
# lib_common.sh — hàm dùng chung cho các script. Source, không chạy trực tiếp.
set -euo pipefail

# Thư mục gốc dự án (thư mục cha của scripts/)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/config/benchmark.yaml"

# Màu cho dễ đọc log
c_info() { printf '\033[36m[i]\033[0m %s\n' "$*"; }
c_ok()   { printf '\033[32m[✓]\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m[x]\033[0m %s\n' "$*" >&2; }

# Đọc 1 giá trị scalar từ benchmark.yaml mà KHÔNG cần cài thư viện YAML.
# Chỉ hỗ trợ "key: value" phẳng theo path "parent.child". Đủ cho các trường ta cần.
# Dùng: yaml_get "target.sha"
yaml_get() {
  local path="$1"
  local parent="${path%%.*}"
  local child="${path#*.}"
  awk -v parent="$parent" -v child="$child" '
    # bỏ comment cuối dòng đơn giản
    { line=$0 }
    # phát hiện block cha (cột 0, "parent:")
    $0 ~ "^"parent":[[:space:]]*$" { inblock=1; next }
    # rời block khi gặp key khác ở cột 0
    inblock && /^[^[:space:]#]/ { inblock=0 }
    inblock {
      # khớp "  child: value"
      if ($0 ~ "^[[:space:]]+"child":") {
        sub("^[[:space:]]+"child":[[:space:]]*", "", line)
        sub("[[:space:]]+#.*$", "", line)   # cắt comment
        gsub(/^["\x27]|["\x27]$/, "", line) # cắt nháy
        print line
        exit
      }
    }
  ' "$CONFIG_FILE"
}
