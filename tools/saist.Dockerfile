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
RUN CGO_ENABLED=0 go build -o /out/datadog-saist ./cmd/datadog-saist

# --- Tầng chạy: ảnh gọn, chỉ chứa binary -------------------------------------
FROM debian:bookworm-slim
# ca-certificates cần cho HTTPS (SAIST tải detection rules từ API công khai của
# Datadog; dùng --local-prompts thì không cần mạng nữa).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/datadog-saist /usr/local/bin/datadog-saist
WORKDIR /work
ENTRYPOINT ["datadog-saist"]
