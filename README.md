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

## Lộ trình 10 giai đoạn

| GĐ | Tên | Trạng thái | Doc |
|---|---|---|---|
| 0 | Chốt định nghĩa "thắng" | ✅ (trong README này) | — |
| 1 | Cố định biến để công bằng | ✅ Đã dựng | [docs/stage1-co-dinh-bien.md](docs/stage1-co-dinh-bien.md) |
| 2 | Dựng môi trường offline | ✅ Đã dựng | [docs/stage2-moi-truong-offline.md](docs/stage2-moi-truong-offline.md) |
| 3 | LLM proxy đo token/time | ✅ Đã dựng | [docs/stage3-llm-proxy.md](docs/stage3-llm-proxy.md) |
| 4 | Harness chạy + thu gom | ✅ Hoàn tất 6/6 run hợp lệ | [docs/stage4-chay-va-do.md](docs/stage4-chay-va-do.md) |
| 5 | Chuẩn hoá `findings.jsonl` | ✅ Đã dựng | [docs/stage5-chuan-hoa.md](docs/stage5-chuan-hoa.md) |
| 6 | Đếm & dedup | ✅ Đã dựng | [docs/stage6-dedup.md](docs/stage6-dedup.md) |
| 7 | Precision (judge) | ✅ Đã dựng | [docs/stage7-judge-precision.md](docs/stage7-judge-precision.md) |
| 7b | Recall (mức lesson) | ✅ Đã dựng | [docs/stage7b-recall.md](docs/stage7b-recall.md) |
| 7c | Judge độc lập (chống tuần hoàn) | ✅ 238/238; kappa 0.580 | [docs/stage7c-judge-doc-lap.md](docs/stage7c-judge-doc-lap.md) |
| 8 | Nâng performance | ✅ Hoàn tất; 2/2 tool qua Pareto gate | [docs/stage8-toi-uu-pareto.md](docs/stage8-toi-uu-pareto.md) |
| 9 | Báo cáo | ✅ Hoàn tất | [docs/stage9-bao-cao-cuoi.md](docs/stage9-bao-cao-cuoi.md) |
| 10 | CI regression gate | ✅ Hoàn tất | [docs/stage10-ci-regression.md](docs/stage10-ci-regression.md) |

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

Sau đó mọi tool SAST được cấu hình trỏ endpoint model về `http://127.0.0.1:4000`
(xem doc GĐ3) — từ đó token/latency được ghi khách quan vào
`results/proxy_logs/calls.jsonl`.

```bash
# GĐ4: cài tool (một lần, lâu) rồi chạy benchmark
bash scripts/stage4_setup_tools.sh
bash scripts/stage4_run.sh --dry-run    # preflight: SHA, proxy, alias, tool đã cài
bash scripts/stage4_run.sh              # chạy thật
bash scripts/stage4_status.sh           # xem mẻ chạy đang tới đâu (--watch để tự làm mới)

# GĐ5: chuẩn hoá output mọi tool về một schema chung
uv run --with pyyaml python scripts/stage5_normalize.py

# GĐ6: dedup (trong-run, giữa-run, giữa-tool) rồi đếm
uv run --with pyyaml python scripts/stage6_dedup.py

# GĐ7: chấm TP/FP bằng LLM-as-judge (chấm mù, phạm vi Java)
uv run --with pyyaml python scripts/stage7_judge.py

# GĐ9: tái sinh báo cáo sau khi source artifact thay đổi có chủ đích
uv run --with pyyaml python scripts/stage9_report.py --write

# GĐ9: kiểm tra report/artifact/provenance/metric/Pareto/budget còn nhất quán
uv run --with pyyaml python scripts/stage9_report.py --check

# GĐ10: strict regression gate offline dùng local và trên pull request
uv run --with pyyaml python scripts/stage10_ci_gate.py
```

---

## Cấu trúc thư mục

```
config/benchmark.yaml     <- SINGLE SOURCE OF TRUTH (SHA, model, tool matrix)
docs/                     <- giải thích cho người mới, từng giai đoạn
scripts/                  <- script tự động hoá mỗi giai đoạn
proxy/                    <- cấu hình LiteLLM + logger token
adapters/                 <- mỗi tool một script "biết cách chạy tool đó" (GĐ4)
tools/                    <- source các tool SAST được clone/build vào đây (không commit)
target/                   <- WebGoat được clone vào đây (không commit)
results/                  <- findings, thống kê, proxy logs
```
