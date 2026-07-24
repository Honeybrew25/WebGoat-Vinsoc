# Giai đoạn 7c — Judge độc lập (chống rủi ro tuần hoàn)

> ✅ **HOÀN TẤT 2026-07-23:** 238/238 finding đã được judge độc lập chấm, 0 lỗi
> còn lại. Agreement 87.8%; Cohen's kappa 0.580.

## 1. Lỗ hổng cần bịt

[Stage 7](stage7-judge-precision.md) chấm precision bằng judge **cùng model**
(`gemini-3.1-flash-lite`) với model mà tool dùng. Một model có thể tán thành lập
luận mang chính "giọng" của nó, làm precision bị thổi phồng có hệ thống.

Judge tự nhất quán 100% trên 50 finding chỉ chứng minh nó ổn định, không chứng
minh nó đúng. Vì vậy phải chấm lại đúng 238 finding trong phạm vi chung bằng một
model khác, rồi đo agreement và Cohen's kappa.

## 2. Thiết kế cuối cùng

Judge độc lập chính là `gemini-3-flash-preview`, `thinkingLevel=low`, chấm mù với
đúng prompt/code/phạm vi của Stage 7. Model này khác với `gemini-3.1-flash-lite`
của hai tool. Google mô tả Gemini 3 Flash có Pro-level intelligence, còn
Flash-Lite là dòng tối ưu chi phí/high-volume.

Transport dùng **Gemini Batch API**:

- quota tách khỏi interactive endpoint;
- kết quả giữ nguyên thứ tự request;
- chi phí bằng 50% standard;
- phù hợp evaluation không cần response tức thời.

Batch đầu chứa 234 finding còn thiếu; 214 thành công và 20 bị Google hủy ở mức
item với cùng lỗi `code=1, "The operation was cancelled."`. Sau khi collect,
batch retry chỉ chứa đúng 20 finding này và hoàn tất 20/20. Bốn verdict interactive
ban đầu được giữ lại; kết quả cuối hợp nhất và sắp đúng thứ tự đủ 238 finding.

Để kiểm độ nhạy theo mức suy luận, đã có mẫu hiệu chuẩn:

- Pro-high vs Pro-low: đồng thuận 7/8; ca lệch là finding mơ hồ về error message.
- Gemini 3 Flash-low vs Pro-high: đồng thuận 4/4 trên phần giao đã chấm được.

Các mẫu này chỉ là hiệu chuẩn; kết luận chính bên dưới dùng đủ 238 finding.

## 3. Vì sao không chạy Pro interactive tới cùng

Các lỗi đã được tách rõ, không gộp chung thành "model treo":

| Model/đường chạy | Kết quả đo được |
|---|---|
| `gemini-3.1-pro-preview` default high | ~50–80s/call, thỉnh thoảng 503 |
| Pro `thinkingLevel=low` | ít thinking token hơn; 26/238 đã checkpoint |
| Pro interactive | dừng bởi quota cứng 250 request/model/ngày; retry sau ~21h48m |
| `gemini-3.5-flash` | 503 ngay cả call đơn với prompt code thật |
| Gemini 3 Flash interactive, low | 4/8 thành công; 4/8 trả 503 |
| Gemini 3 Flash Batch #1 | 214 thành công, 20 item `CANCELLED` trên 234 request |
| Gemini 3 Flash Batch #2 | retry đúng 20 item, thành công 20/20 trong ~2 phút 20 giây |

Bug vận hành phát hiện trong quá trình này và đã sửa:

1. Script từng retry cả quota ngày như lỗi tạm thời. Giờ nhận diện metric
   `generate_requests_per_model_per_day` và fail-fast, giữ checkpoint.
2. Scheduler từng prequeue toàn bộ 238 task. Giờ chỉ giữ tối đa `workers` task;
   fatal error không kích hoạt cả hàng đợi.
3. Output từng bị nghẽn bởi task đầu chậm. Giờ task nào xong ghi checkpoint ngay;
   cuối mẻ mới sắp lại thứ tự nguồn.
4. `drop_params: true` từng có thể nuốt `reasoning_effort`. Alias judge có
   `allowed_openai_params` rõ ràng và đã đo token sau khi recreate proxy.
5. Retry kép proxy × script từng có thể biến 1 finding thành 30 request. Alias
   judge đặt `num_retries: 0`; retry chỉ còn ở script và có ngân sách CLI.

## 4. Lệnh tái lập

```bash
# Xem trạng thái — không submit trùng một job đang chạy.
uv run --with pyyaml python scripts/stage7_batch_judge.py --status

# Khi batch thành công, giữ checkpoint các verdict hợp lệ:
uv run --with pyyaml python scripts/stage7_batch_judge.py --collect

# Nếu còn item lỗi, batch sau chỉ chứa phần còn thiếu:
uv run --with pyyaml python scripts/stage7_batch_judge.py --submit

# Đủ 238 dòng thì tính precision independent + agreement/kappa:
uv run --with pyyaml python scripts/stage7_judge.py \
  --judge-alias gemini-3-flash-judge --out-suffix=-independent \
  --reasoning-effort low --resume
uv run --with pyyaml python scripts/stage7c_judge_agreement.py
```

State chống submit trùng nằm ở `results/stats/judge_batch_state.json`. Batch create
không idempotent; khi một batch chỉ thành công một phần, phải `--collect` trước để
giữ checkpoint rồi mới `--submit` phần còn thiếu.

## 5. Kết quả

Hai judge đồng thuận **209/238 = 87.8%**, nhưng Cohen's kappa chỉ **0.580**. Tỉ lệ
agreement thô cao một phần vì TP chiếm đa số; sau khi trừ phần đồng thuận do ngẫu
nhiên theo phân bố nhãn, mức nhất quán chỉ còn "vừa phải".

| Tool | Flash-Lite TP/FP | Precision Flash-Lite | Judge độc lập TP/FP | Precision độc lập | Chênh |
|---|---:|---:|---:|---:|---:|
| arm-metis | 159 / 33 | 82.8% | 148 / 44 | **77.1%** | -5.7 điểm |
| datadog-saist | 45 / 1 | 97.8% | 41 / 5 | **89.1%** | -8.7 điểm |

Trong 29 bất đồng:

- Flash-Lite nói TP nhưng judge độc lập nói FP: Metis 18, SAIST 4.
- Flash-Lite nói FP nhưng judge độc lập nói TP: Metis 7, SAIST 0.

Thứ hạng precision không đổi: SAIST vẫn cao hơn. Tuy nhiên cả hai precision cùng
giảm, đặc biệt SAIST giảm 8.7 điểm. Vì kappa <0.6, không dùng một bộ precision như
ground truth tuyệt đối; báo cáo cuối phải trình bày cả hai judge như phân tích độ
nhạy.

Các artifact cuối:

- `results/findings/normalized/judged-independent.jsonl`: 238 verdict độc lập.
- `results/stats/precision-independent.json`: precision độc lập theo tool.
- `results/stats/judge_agreement.json`: agreement, kappa và hướng bất đồng.

Ngưỡng diễn giải đã chốt trước khi xem kết quả:

| Kappa | Diễn giải |
|---|---|
| > 0.8 | Phán quyết vững qua đổi model judge |
| 0.6–0.8 | Đồng thuận đáng kể nhưng precision nên báo kèm độ nhạy |
| < 0.6 | Số precision phụ thuộc mạnh vào judge, không dùng như số tuyệt đối |

Ngoài kappa tổng, phải xem bất đồng theo từng tool. Nếu Flash-Lite dễ dãi lệch
hẳn về một tool, đó là bằng chứng tuần hoàn trực tiếp chứ không chỉ nhiễu chung.

## 6. Xong Giai đoạn 7c khi...

- [x] Prompt/phạm vi/chấm mù của hai judge giống nhau.
- [x] Judge độc lập khác model đã cấu hình.
- [x] Checkpoint/resume, bounded workers, fail-fast quota ngày đã có test.
- [x] Batch đầu thành công 214/234; collect giữ đủ kết quả hợp lệ.
- [x] Batch retry thành công 20/20; hợp nhất đủ 238 verdict, 0 lỗi.
- [x] Agreement 87.8%, Cohen's kappa 0.580 và precision theo hai judge đã báo cáo.
