# Giai đoạn 7b — Recall (mức lesson)

```bash
uv run --with pyyaml python scripts/stage7b_recall.py
```

Đầu ra: `results/stats/recall.json`

## 1. Vì sao cần

Precision một mình **thưởng cho sự im lặng**. Một tool chỉ báo 1 finding chắc ăn có
precision 100% nhưng bỏ sót mọi thứ khác. Recall trả lời câu còn lại: *tool bỏ sót
bao nhiêu?* Không có nó, không kết luận được tool nào thực sự bao quát hơn.

## 2. CẢNH BÁO PHƯƠNG PHÁP — đọc trước khi tin số

WebGoat **không** phát hành ground truth máy đọc ở mức dòng. Thứ nó có là cấu trúc
`lessons/<tên>/`, mỗi lesson dạy một lớp lỗ hổng. Ta khai CWE kỳ vọng cho từng
lesson trong `config/benchmark.yaml → ground_truth`.

Vậy "recall" ở đây = **"tool có tìm được lỗ hổng đúng CWE bên trong thư mục lesson
dạy lớp đó không"**. Đây là recall **thô ở mức lesson**, KHÔNG phải recall ở mức
dòng. Nó **đánh giá cao hơn** thực tế: tool báo đúng CWE ở *nhầm dòng* trong đúng
lesson vẫn được tính là bắt được. Con số này để **so sánh giữa hai tool**, không
phải để tuyên bố "recall tuyệt đối của tool X là Y%".

8 lesson là intro/tutorial (không cài lỗ hổng) → gán `null` và **loại khỏi mẫu số**.
Tính chúng vào là phạt oan cả hai tool. Còn lại **22 lesson** có CWE kỳ vọng.

## 3. Kết quả (2026-07-21)

| Tool | Bắt đúng CWE | **Recall** | Có TP bất kỳ trong lesson | Recall nới |
|---|---|---|---|---|
| **arm-metis** | 14 / 22 | **63.6%** | 21 / 22 | 95.5% |
| **datadog-saist** | 5 / 22 | **22.7%** | 10 / 22 | 45.5% |

Hai cột recall đo hai thứ khác nhau:
- **Recall (chặt)**: bắt đúng *đúng CWE* mà lesson dạy.
- **Recall nới**: có *bất kỳ* TP nào trong thư mục lesson, kể cả khác CWE.

Khoảng cách giữa hai cột cho biết tool tìm ra vấn đề *đúng chỗ* nhưng *gọi sai tên*
bao nhiêu. Metis: 21 lesson có TP nhưng chỉ 14 đúng CWE — nó hay tìm ra thứ gì đó
trong lesson nhưng phân loại lệch.

## 4. Đây là mảnh ghép lật ngược một nửa câu chuyện precision

[Stage 7](stage7-judge-precision.md) cho thấy SAIST **chính xác hơn** (97.8% vs
82.8%). Nhìn riêng con số đó dễ tưởng SAIST là lựa chọn an toàn hơn. Recall cho thấy
cái giá của sự an toàn đó:

| | SAIST | Metis |
|---|---|---|
| Precision | **97.8%** | 82.8% |
| Recall (lesson) | 22.7% | **63.6%** |
| Lesson có TP | 10/22 | **21/22** |

**SAIST gần như im lặng trên 12/22 lớp lỗ hổng.** Nó nói ít, và khi nói thì gần như
luôn đúng — nhưng bỏ sót gần 4/5 số lớp lỗ hổng mà đề bài cài. Metis bắt được đúng
CWE ở gần 3× số lesson, đổi lại 1/5 số báo động là giả.

Đây chính xác là đánh đổi precision–recall kinh điển, và **không có con số đơn lẻ nào
tuyên được người thắng**. Nếu buộc phải rút về một số, F1 (trung bình điều hoà):

- Metis:  F1 = 2·0.828·0.636 / (0.828+0.636) = **0.72**
- SAIST:  F1 = 2·0.978·0.227 / (0.978+0.227) = **0.37**

Theo F1, **Metis nhỉnh hơn rõ** — bao phủ rộng bù lại được phần nhiễu, còn precision
cao của SAIST không cứu được recall quá thấp. Nhưng F1 giả định precision và recall
đáng giá ngang nhau; đội nào coi trọng "không bỏ sót" hay "không nhiễu" hơn thì cân
lại theo nhu cầu của mình.

## 5. Điểm mù chung — 8/22 lesson không tool nào bắt đúng CWE

```
hijacksession (CWE-384)      insecurelogin (CWE-319)
logging (CWE-117)            missingac (CWE-862)
securepasswords (CWE-521)    spoofcookie (CWE-565)
ssrf (CWE-918)               vulnerablecomponents (CWE-1104)
```

Đây là phần đáng giá nhất cho người dùng SAST hiểu giới hạn công cụ: cả hai harness,
cùng model, đều mù trước những lớp này. Đa số là lỗ hổng **cần hiểu ngữ cảnh nhiều
file hoặc luồng xác thực** (missing access control, session hijack, insecure login) —
thứ khó thấy khi chỉ đọc một file. Không phải lỗi của riêng harness nào; là giới hạn
của SAST đọc-tĩnh nói chung với lớp lỗ hổng này.

## 6. Xong Giai đoạn 7b khi...

- [x] Ground truth mức lesson khai trong `benchmark.yaml`, tách lesson null.
- [x] Recall tính cho cả hai tool, có cả bản chặt (đúng CWE) và nới (TP bất kỳ).
- [x] Điểm mù chung liệt kê được.
- [x] **Cảnh báo "mức lesson, không phải mức dòng" ghi rõ** — đây là xấp xỉ thô.

➡️ Còn một lỗ hổng phương pháp: judge dùng cùng model với tool. Xem
[stage7c](stage7c-judge-doc-lap.md).
