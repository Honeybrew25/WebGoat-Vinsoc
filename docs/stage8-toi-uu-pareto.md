# Giai đoạn 8 — Tối ưu Pareto

> ✅ **HOÀN TẤT:** `balanced-v1` qua Pareto gate cho cả Metis và SAIST.

Mục tiêu của giai đoạn này là giảm thời gian, token hoặc chi phí mà chỉ cho
phép chất lượng giảm trong dung sai nhỏ đã duyệt. Profile chỉ được nhận nếu nó
nằm trên một Pareto frontier tốt hơn; không chọn cấu hình chỉ vì chạy nhanh.

## Baseline và gate

| Tool | Precision độc lập | Recall lesson | Stable TP | Precision floor | Recall floor | Stable TP floor |
|---|---:|---:|---:|---:|---:|---:|
| arm-metis | 77.1% | 13/22 | 146 | 75.1% | 12/22 | 144 |
| datadog-saist | 89.1% | 5/22 | 28 | 87.1% | 4/22 | 28 |

Resource gate dùng median của ba run: ít nhất một trong `wall_clock_s`,
`total_tokens`, `cost_usd` phải giảm tối thiểu 10%, và không metric nào được
tăng quá 5%.

Hard cap là **$5.00**, trong đó giữ lại **$0.25** cho việc judge độc lập các
finding mới. Mỗi lần chạy đều qua budget preflight; runner dừng ngay nếu lần
tiếp theo có thể vượt trần.

## Profile `balanced-v1`

- Metis: `max_workers=12`; review chỉ Java production và loại các cây test.
- SAIST: `file_concurrency=25`.
- Model, target SHA, temperature, scope và chính sách judge giữ nguyên baseline.

Mọi artifact nằm dưới `results/optimization/balanced-v1/`. Runner từ chối output
ngoài `results/optimization/`, không ghi đè `results/findings/` hay
`results/stats/` của Stage 4–7.

## Cách chạy

Chạy bằng **Git Bash**:

```bash
# Chỉ in knobs, budget projection và lệnh dự kiến; không gọi API
bash scripts/stage8_run.sh --dry-run

# Run 1/cold cho hai tool rồi đánh giá screening bảo thủ
bash scripts/stage8_run.sh --screen

# Chỉ chạy run 2–3 cho tool qua screening và tạo novel.jsonl
bash scripts/stage8_run.sh --complete
```

Nếu `novel.jsonl` không rỗng, judge bằng batch riêng:

```bash
uv run --with pyyaml python scripts/stage7_batch_judge.py --submit \
  --source results/optimization/balanced-v1/stats/novel.jsonl \
  --out results/optimization/balanced-v1/stats/judged-novel.jsonl \
  --state results/optimization/balanced-v1/stats/judge-state.json
```

Dùng lại đúng ba path trên với `--status`, rồi `--collect`. Cuối cùng:

```bash
uv run --with pyyaml python scripts/stage8_evaluate.py \
  --profile balanced-v1 --mode final
```

## Kết quả đo

Sáu run đều hợp lệ. Bảng dưới lấy median ba run từ
`results/optimization/balanced-v1/stats/final.json` và so với median Stage 4:

| Tool | Metric | Baseline | `balanced-v1` | Thay đổi |
|---|---|---:|---:|---:|
| arm-metis | Wall clock | 142s | 58s | **-59.2%** |
| arm-metis | Total token | 4,585,821 | 1,349,793 | **-70.6%** |
| arm-metis | Cost/run | $0.841598 | $0.445274 | **-47.1%** |
| datadog-saist | Wall clock | 13s | 11s | **-15.4%** |
| datadog-saist | Total token | 681,769 | 670,560 | -1.6% |
| datadog-saist | Cost/run | $0.182733 | $0.176114 | -3.6% |

Quality cuối cùng:

| Tool | TP/FP | Precision | Recall lesson | Stable TP | Gate |
|---|---:|---:|---:|---:|---|
| arm-metis | 148/44 | 77.08% | 13/22 | 146 | ✅ |
| datadog-saist | 41/6 | 87.23% | 5/22 | 30 | ✅ |

Hai finding mới của SAIST được chấm độc lập bằng Gemini Batch: hardcoded JWT
secret là TP; SSRF bị regex exact khóa là FP. Judge dùng 3,542 input và 117
output/thinking token, ước tính **$0.001061** theo
[bảng giá Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing).

Tổng chi Stage 8 là **$1.872177/$5.00**, gồm $1.871116 cho sáu scan và
$0.001061 cho judge.

> ✅ **PARETO IMPROVEMENT — arm-metis:** giảm token 70.6%, thời gian 59.2% và
> cost 47.1%; mọi quality gate qua.

> ✅ **PARETO IMPROVEMENT — datadog-saist:** giảm thời gian 15.4%; token và cost
> cũng giảm, mọi quality gate qua.

Quyết định máy đọc nằm tại
`results/optimization/balanced-v1/stats/final.json`; verdict cuối nằm tại
`results/optimization/balanced-v1/stats/judged-final.jsonl`.
