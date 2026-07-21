# SAST Benchmark trên WebGoat — cùng model, so nhiều tool

> **Câu hỏi lớn:** Cho cùng một model (`gemini-3.1-flash-lite`) và cùng một target
> (WebGoat), harness/skill SAST nào **moi ra nhiều lỗ hổng THẬT nhất** với
> chi phí (thời gian, token) hợp lý?

Đây **không** phải "chạy 1 con scanner". Đây là một **bài benchmark có kiểm soát**:
cố định mọi thứ trừ "con tool", rồi đo 3 con số song song cho mỗi tool:

| Con số | Ý nghĩa | Vì sao cần |
|---|---|---|
| `#findings` | Số finding thô | Dễ bị gian lận bằng spam false-positive |
| `#TP` | Số True Positive (sau khi judge) | Cái thật sự có giá trị |
| `precision = TP/(TP+FP)` | Tỉ lệ đúng | Biến count thô thành kết luận có nghĩa |

**Cảnh báo nền tảng:** "nhiều vulns nhất" là *metric bẫy*. Một tool spam FP sẽ
"thắng" về số lượng nhưng vô dụng. Nên con thắng là con tối ưu **TP + precision**,
không phải con ra nhiều dòng nhất.

---

## Lộ trình 9 giai đoạn

| GĐ | Tên | Trạng thái | Doc |
|---|---|---|---|
| 0 | Chốt định nghĩa "thắng" | ✅ (trong README này) | — |
| 1 | Cố định biến để công bằng | ✅ Đã dựng | [docs/stage1-co-dinh-bien.md](docs/stage1-co-dinh-bien.md) |
| 2 | Dựng môi trường offline | ✅ Đã dựng | [docs/stage2-moi-truong-offline.md](docs/stage2-moi-truong-offline.md) |
| 3 | LLM proxy đo token/time | ✅ Đã dựng | [docs/stage3-llm-proxy.md](docs/stage3-llm-proxy.md) |
| 4 | Harness chạy + thu gom | ⏳ Chưa | — |
| 5 | Chuẩn hoá `findings.jsonl` | ⏳ Chưa | — |
| 6 | Đếm & dedup | ⏳ Chưa | — |
| 7 | Precision bằng LLM-as-judge | ⏳ Chưa | — |
| 8 | Nâng performance | ⏳ Chưa | — |
| 9 | Báo cáo | ⏳ Chưa | — |

Doc chi tiết từng khái niệm nằm ở [docs/00-tong-quan.md](docs/00-tong-quan.md).

---

## Bắt đầu nhanh (3 lệnh)

Chạy trong **Git Bash** (đã có sẵn trên Windows). Từ thư mục gốc dự án:

```bash
# GĐ2: clone WebGoat đúng SHA đã pin + index offline
bash scripts/stage2_setup_target.sh

# GĐ3: cài + bật LiteLLM proxy (điểm đo token/time).
# Mở proxy/.env điền CẢ HAI: GEMINI_API_KEY và LITELLM_MASTER_KEY (bắt đầu bằng "sk-").
# Để trống LITELLM_MASTER_KEY -> proxy trả lỗi "No connected db", xem docs/stage3.
cp proxy/.env.example proxy/.env
bash scripts/stage3_start_proxy.sh

# Kiểm tra proxy sống + đang log token
bash scripts/stage3_start_proxy.sh --smoke-test
```

Sau đó mọi tool SAST được cấu hình trỏ endpoint Gemini về `http://127.0.0.1:4000`
(xem doc GĐ3) — từ đó token/latency được ghi khách quan vào
`results/proxy_logs/calls.jsonl`.

---

## Cấu trúc thư mục

```
config/benchmark.yaml     <- SINGLE SOURCE OF TRUTH (SHA, model, tool matrix)
docs/                     <- giải thích cho người mới, từng giai đoạn
scripts/                  <- script tự động hoá mỗi giai đoạn
proxy/                    <- cấu hình LiteLLM + logger token
target/                   <- WebGoat được clone vào đây (không commit)
results/                  <- findings, thống kê, proxy logs
```

## Điểm cần bạn chốt

Trong `config/benchmark.yaml` -> `internet.mode`:

- `webtools_off` (mặc định): cấm web search/fetch trong harness, scanner chỉ đọc
  code local — nhưng **Gemini hosted vẫn gọi được**.
- `airgapped`: cấm mọi outbound kể cả model -> phải đổi sang **model local**,
  kiến trúc khác hẳn.

Hiện đang giả định `webtools_off`. Nếu bạn muốn air-gapped hoàn toàn, đổi giá trị
đó và báo để mình thay phần model.
