# Stage 8 Pareto Optimization Design

## 1. Mục tiêu

Stage 8 tìm cấu hình vận hành Pareto tốt hơn cho Arm Metis và Datadog SAIST trên
đúng benchmark WebGoat đã khóa ở Stage 1–7. Cấu hình thắng phải giảm đáng kể ít
nhất một trong ba tài nguyên — wall-clock, token hoặc chi phí — mà chỉ được suy
giảm chất lượng trong dung sai nhỏ đã duyệt.

Ngân sách API cho toàn bộ screening, ba run finalist và judge finding mới có
hard cap **$5.00**. Baseline Stage 4–7 là bất biến và không được ghi đè.

## 2. Baseline và cổng chấp nhận

Quality gate dùng verdict của judge độc lập Gemini 3 Flash, không dùng finding
thô và không dùng Flash-Lite như ground truth duy nhất.

| Chỉ số | Arm Metis baseline | SAIST baseline | Ngưỡng finalist |
|---|---:|---:|---:|
| Precision độc lập | 148/192 = 77.1% | 41/46 = 89.1% | ≥75.1% / ≥87.1% |
| Recall đúng CWE ở mức lesson | 13/22 | 5/22 | ≥12/22 / ≥4/22 |
| Stable TP | 146 | 28 | ≥144 / ≥28 |

Resource gate so median của ba run finalist với đúng ba run baseline:

- ít nhất một trong `wall_clock_s`, `total_tokens`, `cost_usd` cải thiện ≥10%;
- hai chỉ số tài nguyên còn lại không được xấu đi quá 5%;
- cả ba run phải hợp lệ theo cùng tiêu chí Stage 4.

Một profile chỉ được gọi là Pareto improvement khi qua toàn bộ quality gate và
resource gate. Không dùng số finding thô làm tiêu chí thắng.

## 3. Phạm vi và các điều không làm

Stage 8 giữ nguyên:

- WebGoat SHA `c3ed45a733377bc7313b93f57ff518254d81380f`;
- model thật `gemini-3.1-flash-lite`, temperature 0 và top_p 1;
- phạm vi báo cáo chính: Java, không test, không vendor;
- ba lần chạy cold/warm/warm;
- normalization, line tolerance, blind judge và lesson ground truth.

Stage 8 không đổi model, không sửa prompt/rule của tool, không tắt indexing của
SAIST, không tắt navigation/triage của Metis và không thêm embedding model. Các
thay đổi này có nguy cơ biến bài tối ưu hiệu năng thành một benchmark khác.

## 4. Profile finalist `balanced-v1`

### Arm Metis

- `engine.max_workers`: tăng từ mặc định 8 lên 12;
- `review_code_include_paths`: chỉ tạo lượt review cho Java production;
- `review_code_exclude_paths`: loại test, JavaScript và vendor khỏi lượt review;
- file bị loại khỏi review vẫn hiện hữu cho navigation/context theo hợp đồng
  review-path của Metis;
- giữ `--triage`, default navigation tools và không bật vector index.

Scope filter này căn Metis theo đúng phạm vi bảng chính. Nó không được phép che
mã nguồn khỏi evidence tools hoặc thay đổi target checkout.

### Datadog SAIST

- `--file-concurrency`: tăng từ mặc định 20 lên 25;
- giữ local prompts, indexing và hai pha detection → validation;
- detection model và validation model tiếp tục trỏ cùng alias Flash-Lite.

Mọi knob Stage 8 được truyền bằng biến môi trường tùy chọn. Khi không có biến,
adapter phải giữ nguyên hành vi Stage 4 để baseline tái lập được.

## 5. Kiến trúc và lưu trữ

Pipeline riêng của Stage 8:

```text
config stage8 profile
        ↓
stage8 runner + budget ledger
        ↓
results/optimization/<profile>/<tool>/run-NN-{cold,warm}/
        ↓
normalize + dedup bằng logic Stage 5/6
        ↓
reuse verdict baseline + judge finding mới
        ↓
quality gate + resource gate + quyết định Pareto
```

Các thành phần:

- `config/benchmark.yaml`: profile, budget và threshold Stage 8;
- `adapters/arm-metis.sh`, `adapters/datadog-saist.sh`: nhận knob tùy chọn;
- `scripts/stage8_run.sh`: dry-run, screening, finalist, budget preflight;
- `scripts/stage8_evaluate.py`: normalize/dedup, matching verdict và Pareto gate;
- `results/optimization/`: toàn bộ raw output và thống kê Stage 8;
- `docs/stage8-toi-uu-pareto.md`: nhật ký giả thuyết, chi phí và kết luận.

Stage 8 chạy trong checkout hiện tại vì baseline Stage 4–7 vẫn là thay đổi chưa
commit; tạo worktree từ HEAD sẽ thiếu chính pipeline và artifact cần tái sử dụng.
Runner phải bảo vệ baseline bằng đường dẫn output riêng và từ chối đường dẫn nằm
trong `results/findings/<tool>/run-*`.

## 6. Lịch thí nghiệm và ngân sách

1. Chạy `balanced-v1` một lần cho cả hai tool như screening.
2. Tính quality proxy bảo thủ và resource delta của run đó.
3. Nếu profile qua screening và budget ledger cho phép, dùng chính screening làm
   run 1 rồi chạy thêm run 2–3 cùng cấu hình.
4. Judge các finding finalist chưa có verdict baseline.
5. Tính quality/resource gate cuối trên đủ ba run.
6. Chỉ khi còn ngân sách sau khi giữ reserve cho judge, chạy một Metis ablation
   (`scope-only` hoặc `workers-only`) để giải thích nguồn cải thiện. Ablation
   không phải điều kiện công nhận finalist.

Budget ledger cộng toàn bộ model cost của Stage 8. Trước mỗi run, runner dự báo
chi phí bằng dữ liệu screening hoặc median baseline. Run không được khởi động nếu
`actual_spend + projected_run_cost + judge_reserve > 5 USD`. Judge reserve mặc
định là $0.25 và được thay bằng chi phí thực khi đã collect.

## 7. Tái sử dụng verdict và đánh giá chất lượng

Candidate được normalize/dedup bằng đúng logic Stage 5/6. Một finding candidate
được kế thừa verdict độc lập baseline khi cùng tool, cùng file, tiêu đề chuẩn hóa
giống nhau và dòng nằm trong `dedup.line_tolerance`; finding có dòng không đáng
tin dùng cùng quy tắc matching đã áp dụng ở Stage 6.

Trong screening:

- finding mới chưa có verdict được tính là FP tạm thời;
- baseline stable TP biến mất được tính là TP bị mất;
- profile chỉ đi tiếp nếu vẫn qua quality gate dưới giả định bảo thủ này.

Với finalist, mọi finding mới được chấm mù bằng Gemini 3 Flash independent judge
với cùng prompt, code context và scope Stage 7c. Sau đó evaluator tính lại
precision, recall lesson và stable TP chính xác. Finding mới không được tự động
coi là TP chỉ vì làm raw count tăng.

## 8. Xử lý lỗi và dừng sớm

Một run không hợp lệ nếu có một trong các dấu hiệu:

- exit code khác 0, timeout hoặc SARIF rỗng/hỏng;
- 429/rate-limit, model fallback hoặc call quy về tool `unknown`;
- không có LLM call dù profile yêu cầu quét thật;
- target SHA, alias model hoặc tham số profile không khớp manifest;
- chi phí thực làm tổng ledger vượt hard stop $5.

Runner dừng profile ngay sau run không hợp lệ và giữ toàn bộ log để chẩn đoán.
Không tự động retry cả run vì retry sẽ làm sai ngân sách và cold/warm semantics.
Lỗi tạm thời phải được người vận hành xác nhận trước khi chạy lại đúng run index.

## 9. Kiểm thử

TDD bao phủ tối thiểu:

- parse profile và default adapter không đổi hành vi Stage 4;
- Stage 8 chỉ ghi dưới `results/optimization`;
- budget ledger từ chối run dự kiến vượt $5;
- matching verdict tôn trọng tool/file/title/dung sai dòng;
- finding mới bị tính FP trong screening;
- stable TP mất đi làm quality gate thất bại;
- resource gate yêu cầu cải thiện ≥10% và chặn regression >5%;
- precision/recall/stable-TP thresholds đúng cho từng tool;
- invalid run/fallback/unknown call dừng profile.

Sau unit test phải chạy `stage8_run.sh --dry-run`, Python compile, YAML parse,
shell syntax và `git diff --check`. Chỉ sau đó mới bật Docker/proxy và gọi API.

## 10. Điều kiện hoàn tất Stage 8

- Baseline Stage 4–7 không thay đổi và còn tái lập được.
- `balanced-v1` có ba run hợp lệ hoặc được báo thất bại minh bạch tại gate.
- Tổng chi phí Stage 8 không vượt hard stop $5.
- Mọi finding finalist được gắn verdict độc lập hoặc đánh dấu lỗi rõ ràng.
- Báo cáo có delta thời gian/token/chi phí, precision, recall, stable TP và quyết
  định Pareto cho từng tool.
- README và tài liệu Stage 8 đồng bộ với artifact thực tế.
