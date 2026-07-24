# Giai đoạn 6 — Dedup & đếm

```bash
uv run --with pyyaml python scripts/stage6_dedup.py
```

Đầu ra: `results/findings/normalized/deduped.jsonl`, `results/stats/counts.json`

## 1. Ba tầng gộp, ba câu hỏi khác nhau

| Tầng | Gộp cái gì | Trả lời câu hỏi |
|---|---|---|
| 1 | Trong **một** lần chạy | Tool thực sự báo bao nhiêu vấn đề *riêng biệt*? |
| 2 | Giữa **các** lần chạy | Vấn đề nào lặp lại được, cái nào chỉ là nhiễu? |
| 3 | Giữa **hai tool** | Hai tool có nhìn thấy cùng những vấn đề không? |

Tầng 2 quan trọng hơn vẻ ngoài: nếu chỉ lấy **hợp** của 3 lần chạy, tool nào *ngẫu
nhiên hơn* sẽ được thưởng oan — mỗi lần chạy đóng góp thêm một ít nhiễu mới và tổng
số phình lên trông như "tìm được nhiều hơn".

## 2. Chọn dung sai dòng — đo trước, chọn sau

SAIST báo cùng một vấn đề ở cùng file nhưng **lệch dòng** giữa các lần chạy. Đo 36
nhóm `(file, title)` xuất hiện ở ≥2 lần chạy:

```
lệch  0 dòng : 29 nhóm   (81%)
lệch  3,4,6,7,14,19,24 dòng : mỗi mức 1 nhóm
```

| Dung sai | Gom được |
|---|---|
| ±0 | 81% |
| ±5 | 86% |
| **±10** | **92%** |
| ±20 | 97% |
| ±50 | 100% |

**Không có ranh giới tự nhiên** — phần đuôi rải đều, không có khoảng trống nào để
cắt. Chọn **±10** vì gom được 92% và ~10 dòng xấp xỉ thân một hàm Java, nên "cùng
một vùng code" là cách giải thích bảo vệ được trước người phản biện. Nới lên ±50 gom
đủ 100% nhưng bắt đầu có nguy cơ gộp **nhầm** hai lỗ hổng khác nhau trong cùng file.

Giá trị nằm ở `config/benchmark.yaml → dedup.line_tolerance`, đổi được.

### Neo cụm vào phần tử ĐẦU, không phải phần tử trước

Gom theo dòng dễ dính lỗi "chuỗi dây": các dòng 1, 11, 21, 31 lần lượt cách nhau
đúng 10 sẽ bị nối thành một cụm duy nhất dù hai đầu cách nhau 30 dòng. `cluster_by_line()`
so với **đầu cụm**, nên cụm không bao giờ rộng quá `tolerance`.

## 3. Kết quả (2026-07-21)

### Tầng 1 — tự trùng ngay trong một lần chạy

| Tool | Run | Thô | Duy nhất | Tự trùng |
|---|---|---|---|---|
| arm-metis | cold / warm / warm | 278 / 278 / 278 | 276 / 276 / 276 | 2 / 2 / 2 |
| datadog-saist | cold / warm / warm | 39 / 43 / 41 | 38 / 38 / 38 | 1 / 5 / 3 |

Cả hai đều tự trùng nhưng ít. Đáng chú ý: SAIST sau khi dedup ra **đúng 38 cả ba
lần** — số thô dao động 39/43/41 chỉ là do nó báo lặp, không phải tìm thêm được gì.

### Tầng 2 — gộp 3 lần chạy, theo độ lặp lại

| Tool | Duy nhất | 3/3 run | 2/3 | 1/3 | % ổn định |
|---|---|---|---|---|---|
| arm-metis | 280 | 272 | 4 | 4 | **97.1%** |
| datadog-saist | 46 | 30 | 8 | 8 | **65.2%** |

**1/3 số finding của SAIST chỉ xuất hiện ở một lần chạy duy nhất.** Nếu bạn chạy
SAIST một lần rồi báo cáo, khoảng 35% kết quả là thứ mà lần chạy sau sẽ không tái
hiện. Đây là lập luận mạnh nhất cho `repeats: 3` — và cho việc **báo cáo cột "3/3"
riêng**, đừng gộp chung vào tổng.

### Tầng 3 — hai tool cùng thấy: 34 cặp khớp

Khớp theo `(file, CWE)`. **Không** dùng tiêu đề vì hai tool viết khác hẳn nhau
(`datadog/java-sqli` vs `SQL Injection`). Dòng chỉ ép khớp khi **cả hai** bên đều có
`line_confidence: exact` — Metis có 56% dòng rác, ép khớp dòng sẽ loại oan gần hết
phần giao.

Quy về số finding riêng biệt mỗi bên:

| | Được bên kia xác nhận |
|---|---|
| **SAIST** | 30 / 46 = **65%** |
| **Metis** | 22 / 280 = **8%** |

Đây là con số đáng chú ý nhất của Giai đoạn 6. Nó **bất đối xứng mạnh**: hầu hết
những gì SAIST tìm được thì Metis cũng tìm được, nhưng 92% những gì Metis báo thì
SAIST không thấy.

Hai cách đọc, và **dữ liệu hiện tại chưa phân biệt được**:

- **Metis đào sâu hơn** — nó bắt được lớp vấn đề mà SAIST bỏ sót.
- **Metis ồn hơn** — 258 finding không được xác nhận phần lớn là false positive.

Dấu hiệu nghiêng về khả năng thứ hai: trong 258 finding không được xác nhận, có
**23 nằm trong mã kiểm thử** và **99 không suy được CWE nào** (tiêu đề mơ hồ tới mức
không khớp nổi mẫu nào, ví dụ "Potential NullPointerException leading to Denial of
Service" — đó là bug chất lượng, không phải lỗ hổng bảo mật).

Nhưng đây mới là **dấu hiệu**, chưa phải kết luận. Chỉ Giai đoạn 7 (LLM-as-judge)
mới trả lời được. 34 cặp khớp là ứng viên True Positive **mạnh nhất** — hai harness
độc lập, cùng model, cùng chỉ vào một chỗ.

## 4. Vì sao KHÔNG loại mã kiểm thử ở đây

23 finding của Metis nằm trong `src/test/` và `src/it/`. Rất cám dỗ để loại thẳng.
Nhưng "lỗ hổng trong test có tính không" là **câu hỏi đánh giá**, không phải câu hỏi
gộp trùng. Giai đoạn 6 chỉ gộp và đếm; Giai đoạn 7 mới phán. `deduped.jsonl` giữ cờ
`in_test_code` để lọc lúc đó.

## 5. Xong Giai đoạn 6 khi...

- [x] `deduped.jsonl` sinh ra, 326 vấn đề duy nhất (280 Metis + 46 SAIST).
- [x] Dung sai dòng chọn dựa trên **đo phân bố trôi dòng**, không bốc số.
- [x] Có cột `run_count` để tách finding ổn định (3/3) khỏi finding một lần (1/3).
- [x] Giao giữa hai tool tính được: 34 cặp, quy về 30 SAIST / 22 Metis riêng biệt.
- [ ] Giai đoạn 7 judge precision trên tập này.

➡️ Tiếp theo (Giai đoạn 7): LLM-as-judge chấm từng finding là TP hay FP. Hai lưu ý
bắt buộc mang sang: judge phải đọc **`message`**, không đọc `rule_id_raw` (33%
finding SAIST có ruleId mâu thuẫn chính message của nó — xem
[stage5](stage5-chuan-hoa.md)); và judge nên chấm **mù**, không cho biết finding đến
từ tool nào, để tránh thiên vị.
