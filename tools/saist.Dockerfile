# ============================================================================
#  saist.Dockerfile — build SAIST (Go) mà KHÔNG cần cài Go trên host
# ============================================================================
#  Build:  docker build -f tools/saist.Dockerfile -t sast-bench/saist:pinned tools/datadog-saist
#  (scripts/stage4_setup_tools.sh làm việc này giúp bạn)
# ============================================================================

# --- Tầng build: biên dịch binary tĩnh ---------------------------------------
FROM golang:1.24 AS build
WORKDIR /src

# Tải dependency trước (tận dụng cache Docker: chỉ chạy lại khi go.mod đổi)
COPY go.mod go.sum ./
RUN go mod download

# Chép phần còn lại rồi build
COPY . .
# CGO_ENABLED=1 là BẮT BUỘC, không phải tuỳ chọn: SAIST dùng tree-sitter để dựng
# call graph đa file, mà binding Go của tree-sitter là cgo. Đặt CGO_ENABLED=0 thì
# build constraint loại sạch file Go và báo "build constraints exclude all Go files"
# — nghe như thiếu file, thực ra là thiếu cgo.
# Hệ quả: binary link động với glibc, nên tầng chạy phải cùng nền Debian bookworm
# như golang:1.24 (xem FROM bên dưới). Đổi sang alpine/distroless là gãy.
RUN CGO_ENABLED=1 go build -o /out/datadog-saist ./cmd/datadog-saist

# --- Tầng chạy: ảnh gọn, chỉ chứa binary -------------------------------------
FROM debian:bookworm-slim
# ca-certificates cần cho HTTPS (SAIST tải detection rules từ API công khai của
# Datadog; dùng --local-prompts thì không cần mạng nữa).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/datadog-saist /usr/local/bin/datadog-saist
WORKDIR /work
ENTRYPOINT ["datadog-saist"]
