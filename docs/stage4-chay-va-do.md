# Giai đoạn 4 — Chạy tool & đo

> Mục tiêu: cho mỗi tool quét WebGoat **nhiều lần**, thu output thô, và ghi lại
> chi phí (token, thời gian) sao cho Giai đoạn 5 quy chiếu được từng đồng token
> về đúng tool đã tiêu nó.

## 1. Ba tầng, mỗi tầng một việc

```
scripts/stage4_run.sh   (orchestrator)  — quyết định chạy tool nào, mấy lần, đo
        │  truyền biến môi trường
        ▼
adapters/<tool>.sh      (adapter)       — biết cách gọi ĐÚNG một tool cụ thể
        │  gọi LLM
        ▼
LiteLLM proxy (GĐ3)                     — ghi token/latency mỗi call
```

Tách adapter ra khỏi orchestrator vì mỗi tool có cách chạy hoàn toàn khác nhau
(SAIST là binary Go trong Docker, Metis là CLI Python đọc `metis.yaml`). Nếu nhồi
hết vào một script thì thêm tool thứ ba là phải sửa lõi — dễ làm hỏng phần đã đo.

### Hợp đồng adapter

Orchestrator truyền vào qua biến môi trường, adapter chỉ cần tuân thủ:

| Biến | Ý nghĩa |
|---|---|
| `TARGET_DIR` | Đường tuyệt đối tới mã nguồn cần quét |
| `RUN_DIR` | Thư mục ghi kết quả của **riêng lần chạy này** |
| `MODEL_ALIAS` | Alias model trên proxy (dùng quy chiếu token) |
| `PROXY_BASE_URL` | `http://127.0.0.1:4000` |
| `PROXY_KEY` | `LITELLM_MASTER_KEY` |
| `TOOL_HEADER` | Tên header nhận diện tool (`x-tool-name`) |

**Đầu ra bắt buộc:** `$RUN_DIR/raw_output.sarif` + exit code phản ánh thành/bại.

Thêm tool mới = viết thêm một file trong `adapters/`, khai báo vào
`config/benchmark.yaml`, không đụng gì tới orchestrator.

## 2. Vấn đề khó nhất: token này của tool nào?

Proxy thấy một dòng request đến, nhưng **không tự biết** nó của SAIST hay Metis.
Không quy chiếu được thì cột "chi phí mỗi tool" trong báo cáo cuối là số vô nghĩa.
Ta dùng **hai cơ chế chồng lên nhau**:

**Cách 1 — header `x-tool-name`.** Sạch nhất, nhưng *không phải tool nào cũng cho
gắn header tuỳ ý*. SAIST thì không.

**Cách 2 — alias model riêng cho từng tool.** Mỗi tool gọi một tên model khác
nhau (`gemini-31-flash-lite-saist`, `...-metis`), nhưng **mọi alias đều trỏ về
cùng một model thật với cùng tham số** (dùng YAML anchor `&model_params` trong
`litellm_config.yaml`). Tính công bằng không đổi — chỉ khác cái nhãn dán ngoài.
`token_logger.py` suy ra tool từ hậu tố alias — nhưng phải lấy alias từ
`metadata.model_group`, **không** phải `kwargs["model"]` (xem mục Bẫy bên dưới).

Logger ưu tiên header, thiếu thì rơi về alias. Alias là mạng lưới an toàn, và nó
phổ quát: tool nào cũng phải chọn tên model, nên cách này luôn dùng được.

> ⚠️ Alias trong `benchmark.yaml` và trong `litellm_config.yaml` **phải khớp nhau**.
> Lệch một ký tự thì tool gọi vào sẽ lỗi, hoặc token rơi về `unknown`.
> `stage4_run.sh` kiểm tra điều này ở preflight trước khi chạy.

## 3. Preflight — dừng sớm còn hơn chạy 40 phút rồi hỏng

`stage4_run.sh --dry-run` kiểm tra, và **từ chối chạy** nếu có mục nào đỏ:

1. Target đang đứng **đúng SHA đã pin** (quét nhầm phiên bản = mọi so sánh vô nghĩa).
2. Proxy sống (không có proxy thì không đo được token — chạy cũng vô ích).
3. **Mọi alias** khai trong `benchmark.yaml` đều tồn tại trên proxy.
4. Mỗi tool enabled có adapter **và** đã cài xong (image Docker / venv).

## 4. Cold vs warm

`run.repeats: 3`, trong đó **lần 1 = cold**, lần 2-3 = **warm**.

Lần cold gánh chi phí dựng index/cache; trộn chung vào trung bình sẽ thổi phồng
thời gian và làm tool *có* index trông chậm hơn thực tế — trong khi ở đời thật
index chỉ dựng một lần. Tách ra để báo cáo được cả hai con số.

Chạy nhiều lần còn để đo **variance**: LLM không tất định tuyệt đối kể cả
`temperature=0`, nên một lần chạy duy nhất không nói lên điều gì.

## 5. Kết quả để ở đâu

```
results/findings/<tool>/run-01-cold/
    raw_output.sarif   <- output thô của tool
    run_meta.json      <- siêu dữ liệu đo được
    stdout.log / stderr.log
```

`run_meta.json` chứa mấu chốt của phép đo:

```json
{
  "tool": "arm-metis",
  "phase": "cold",
  "model_alias": "gemini-31-flash-lite-metis",
  "target_sha": "c3ed45a...",
  "wall_clock_s": 412,
  "exit_code": 0,
  "call_log_line_from": 1,
  "call_log_line_to": 87,
  "llm_calls": 87
}
```

**Vì sao có `call_log_line_from/to`:** orchestrator ghi lại số dòng của
`calls.jsonl` ngay trước và ngay sau mỗi lần chạy. Nhờ đó Giai đoạn 5 **cắt chính
xác** những call thuộc lần chạy này, thay vì đoán theo mốc thời gian — cách đoán
đó sai ngay khi hai lần chạy nối đuôi nhau hoặc có call đến muộn.

Một tool hỏng **không** làm chết cả mẻ chạy: orchestrator ghi `exit_code != 0` rồi
đi tiếp, Giai đoạn 5 sẽ loại lần chạy đó khỏi thống kê.

## 6. Bất đối xứng đã biết (phải ghi vào báo cáo)

Không phải mọi thứ đều cào bằng được. Những chỗ lệch còn lại phải **khai báo**,
vì giấu đi thì kết luận thành gian lận:

- **Metis chạy KHÔNG bật index/embeddings.** Index của Metis cần một
  embedding provider *riêng* — bật lên là kéo **model thứ hai** vào phép đo, phá
  vỡ nguyên tắc "cùng một model". Chấp nhận Metis chạy thiếu một tính năng mạnh
  của nó, và ghi rõ.
- **SAIST có sẵn 2 pha detect → validate**, Metis không. Đây là *thiết kế harness*
  — chính là thứ đem so, nên giữ nguyên, không cào bằng.
- **Metis dùng provider `vllm` chứ không phải `openai`.** Cả hai đều
  OpenAI-compatible và nói `/v1/chat/completions` — đúng endpoint mà
  `token_logger` cắm callback. Chọn `vllm` vì nó *không* mặc định trỏ về
  `api.openai.com` mà bắt buộc khai `base_url`, nên không có nguy cơ lỡ tay gọi
  thẳng ra ngoài, vòng qua proxy và mất điểm đo. **Model thật vẫn là
  `gemini-3.1-flash-lite`**,
  chỉ khác cái tên provider.

## 7. Chạy

```bash
# 1) Cài tool (một lần, lâu — build Go trong Docker + venv Python)
bash scripts/stage4_setup_tools.sh

# 2) Kiểm tra cấu hình trước khi tốn tiền
bash scripts/stage4_run.sh --dry-run

# 3) Chạy thật
bash scripts/stage4_run.sh                    # mọi tool enabled
bash scripts/stage4_run.sh --tool arm-metis   # một tool
bash scripts/stage4_run.sh --repeats 1        # rút ngắn khi thử
```

## 8. Bẫy đã gặp

### `case` không khớp dù chuỗi trông đúng — CRLF

`bench_config.py` chạy trên Windows nên `print()` xuất **CRLF**. Bash đọc bằng
`$(...)`, mà `\r` **không nằm trong `IFS`** nên nó dính vào cuối biến:
`"datadog-saist\r"`. Hậu quả rất khó thấy — thông báo lỗi in ra trông hoàn toàn
bình thường, nhưng `case "$tool" in datadog-saist)` **im lặng không khớp** và
đường dẫn thành `adapters/datadog-saist\r.sh`.

Đã chặn ở hai lớp: `bench_config.py` ép `sys.stdout.reconfigure(newline="\n")`,
và `bench_cfg()` trong `lib_common.sh` lọc thêm `tr -d '\r'`.

### Rate limit phá hỏng cả mẻ chạy — và làm nó *trông như* thành công

Mẻ chạy đầu tiên (2026-07-21) hỏng hoàn toàn. Cùng tool, cùng target, cùng SHA:

| Lần chạy | exit | call LLM | findings |
|---|---|---|---|
| SAIST chạy đơn lẻ (trước đó) | 0 | 37 | **9** |
| SAIST run-01-cold | 1 | 19 | — |
| SAIST run-02-warm | **0** | **1** | **0** |
| SAIST run-03-warm | **0** | 19 | **1** |

Ba nguyên nhân chồng lên nhau:

**1. Free tier chỉ 15 request/phút.** Lỗi 429 nói thẳng:
`quotaValue: "15"`, metric `generate_content_free_tier_requests`. SAIST chạy 20
file song song nên đạp trần trong vài giây.

**2. SAIST tự đổi sang model khác.** 39 lần `switching to fallback detection
model: openai/gpt-4.1-nano` trong 3 run. Đây chính là confound mà cả benchmark
sinh ra để loại bỏ. Tệ hơn, nó là **hằng số hardcode** và chỉ kích hoạt ở chế độ
`--ai-gateway` — đúng cờ ta buộc phải bật để dùng alias:

```go
// internal/agents/detection.go:21
const aIGatewayFallbackModel = "openai/gpt-4.1-nano"
```

Không có cờ nào tắt. **Cách vô hiệu hoá** (đã áp dụng): đăng ký chính tên đó
trong `litellm_config.yaml` và trỏ về **cùng model, cùng tham số**. SAIST
tưởng nó đã đổi model, thực tế không có gì đổi. Kèm theo, `token_logger` đánh dấu
`via_fallback: true` cho từng call đi đường này — biến một confound vô hình thành
một con số đếm được và báo cáo được.

**3. Tiêu chí "thành công" của orchestrator quá lỏng.** `exit 0 + SARIF khác
rỗng` nhận nhầm run-02 (1 call LLM, 0 finding) là thành công. Đã siết: đếm dấu
hiệu 429 trong `stderr.log`, ghi `rate_limit_hits` / `model_fallbacks` / `valid`
vào `run_meta.json`, và **báo đỏ** khi run dính rate limit.

> **Bài học về tính công bằng, quan trọng hơn cả ba lỗi trên:** Metis gọi 399 call
> mà *không dính 429 lần nào* vì nó chạy **tuần tự**; SAIST chết vì chạy **song
> song**. Rate limit không chỉ làm hỏng dữ liệu — nó **phạt có hệ thống** tool
> song song và tha tool tuần tự. Chạy benchmark dưới hạn mức bị siết là tự tạo ra
> một biến gây nhiễu tỉ lệ thuận với mức độ song song của tool.

### Mọi call rơi về `unknown` — alias bị nuốt trước khi callback chạy

Triệu chứng: chạy SAIST, `calls.jsonl` ghi `"tool": "unknown"` và
`"model": "gemini-31-flash-lite"` — **không phải** alias `...-saist` đã truyền vào.

Nguyên nhân: khi callback của LiteLLM chạy thì việc định tuyến **đã xong**, và
`kwargs["model"]` là model **thật** ở phía sau. Alias đã bị thay mất. Mà mọi alias
đều trỏ về cùng một model thật — nên `kwargs["model"]` về nguyên tắc *không thể*
phân biệt tool nào.

Alias gốc còn nằm ở `litellm_params.metadata.model_group` và ở body request thô
(`proxy_server_request.body.model`). `token_logger._requested_alias()` dò lần lượt
các chỗ đó. Log giờ ghi **cả hai**: `model` (thật) và `requested_model` (alias),
để Giai đoạn 5 đối chiếu chéo được với `run_meta.json`.

> Bài học chung: trong benchmark, thứ ta *cấu hình* và thứ hệ thống *ghi lại* có
> thể là hai giá trị khác nhau. Luôn kiểm chứng bằng một call thật rồi đọc log,
> đừng tin cấu hình trông có vẻ đúng.

### `build constraints exclude all Go files` — SAIST cần cgo

Build SAIST với `CGO_ENABLED=0` sẽ chết với thông báo:

```
tree-sitter-java/bindings/go: build constraints exclude all Go files in ...
```

Nghe như thiếu file, thực ra là **thiếu cgo**: SAIST dùng tree-sitter để dựng call
graph đa file, mà binding Go của tree-sitter là cgo. Tắt cgo thì build constraint
loại sạch file Go.

Đã đặt `CGO_ENABLED=1` trong `tools/saist.Dockerfile`. Hệ quả kéo theo: binary
link **động** với glibc, nên tầng chạy phải cùng nền Debian bookworm với
`golang:1.24`. Đổi tầng chạy sang alpine (musl) hay distroless là gãy lúc chạy —
và gãy *muộn*, sau khi build đã xanh.

> Không có đường vòng: bỏ tree-sitter đi thì mất chính cross-file context — thế
> mạnh của SAIST — tức là tự làm yếu tool và phá vỡ tính công bằng của benchmark.

### Header bị bỏ im lặng — `additional_headers` vs `default_headers`

Provider `vllm` của Metis chỉ nhận **`default_headers`** (xem
`CONFIG_SPEC.copy_keys` trong `src/metis/providers/vllm.py`). Key
`additional_headers` **chỉ tồn tại ở provider `gemini`**. Đặt nhầm tên thì Metis
không báo lỗi gì cả — header đơn giản biến mất, và mọi call rơi về tool
`unknown` trong `calls.jsonl`.

Bài học: với config kiểu "gõ sai key thì im lặng bỏ qua", đừng tin tài liệu của
provider khác — đọc thẳng `copy_keys` trong mã nguồn.

## 9. Xong Giai đoạn 4 khi...

- [x] Metis cài xong bằng `uv` (venv có binary `metis`).
- [x] SAIST build xong thành image `sast-bench/saist:pinned` (210MB).
- [x] `stage4_run.sh --dry-run` chạy đúng: bắt được target sai SHA, alias thiếu
      trên proxy, và tool chưa cài — đã kiểm chứng bằng cách để nó fail thật.
- [x] **SAIST chạy thật xong 1 lần** (2026-07-21): `exit_code: 0`, 34s,
      37 call LLM, 9 finding, SARIF 78KB.
- [x] `llm_calls` (37) khớp đúng số dòng `calls.jsonl` (37); **37/37 call quy về
      `datadog-saist`** với `requested_model: gemini-31-flash-lite-saist`,
      không còn `unknown`.
- [x] Alias fallback `openai/gpt-4.1-nano` đã trỏ về cùng model và được
      đánh dấu `via_fallback: true` — kiểm chứng bằng call thật.
- [x] Key Gemini KHÔNG dính free tier (đã đo: 25 request song song đều 200).

- [x] Metis chạy thật trọn vẹn: 3/3 run valid, 934 call mỗi run.
- [x] Chạy đủ mẻ hợp lệ: 6/6 run `valid: true`, `rate_limit_hits: 0`, 0 fallback.


### ✅ Mẻ chạy hợp lệ đầu tiên (2026-07-21, `gemini-3.1-flash-lite`)

6/6 lần chạy `valid: true`, 0 fallback, 0 call rơi về `unknown`. Tổng **$3.53**, ~9 phút.

| Tool | Lần chạy | Giây | Call LLM | Findings |
|---|---|---|---|---|
| arm-metis | cold / warm / warm | 142 / 140 / 181 | 934 / 934 / 934 | 278 / 278 / 278 |
| datadog-saist | cold / warm / warm | 16 / 11 / 13 | 162 / 174 / 171 | 39 / 43 / 41 |

#### Phát hiện quan trọng: độ ỔN ĐỊNH giữa các lần chạy khác nhau rất xa

Đếm finding **duy nhất** theo bộ ba `(ruleId, file, dòng)` rồi so 3 lần chạy:

| Tool | Mỗi run | Giao cả 3 | Hợp cả 3 | **Ổn định** |
|---|---|---|---|---|
| arm-metis | 262 / 263 / 263 | 261 | 264 | **98.9%** |
| datadog-saist | 39 / 43 / 41 | 29 | 55 | **52.7%** |

> ⚠️ **HAI CON SỐ TRÊN ĐÃ BỊ ĐÍNH CHÍNH** — xem
> [stage5 §6](stage5-chuan-hoa.md#6-đính-chính-số-liệu-độ-ổn-định-ở-giai-đoạn-4).
> Khoá `(rule, file, dòng)` không công bằng: Metis chỉ có **một** `ruleId` và 56%
> `startLine` bằng 1, nên khoá của nó co lại gần bằng "tên file". Tính lại bằng
> khoá công bằng `(title, file)`: Metis **97.1%**, SAIST **73.2%** — kết luận định
> tính không đổi nhưng khoảng cách hẹp hơn nhiều (24 điểm, không phải 46).

**SAIST chỉ lặp lại được ~half số finding của chính nó** dù `temperature=0`. Nghĩa là
một lần chạy duy nhất của SAIST **không đại diện** cho năng lực của nó — báo cáo dựa
trên 1 run sẽ sai lệch tuỳ vào việc bạn bốc trúng run nào. Đây chính là lý do
`run.repeats: 3` tồn tại, và bản thân **độ ổn định là một chỉ số chất lượng harness**
đáng đưa vào báo cáo cuối, không kém gì precision.

> Ghi chú cho Giai đoạn 6 (dedup): Metis báo 278 kết quả nhưng chỉ 262 **duy nhất**
> -> có trùng lặp NGAY TRONG một lần chạy. Dedup phải làm cả trong-run lẫn giữa-run.

#### Cảnh báo cho Giai đoạn 7 (judge precision)

Metis ra **262** finding, SAIST ra **39** — gấp ~6.7 lần. **Đừng vội kết luận Metis
giỏi hơn.** Đây đúng là kịch bản "count là metric bẫy" mà [stage0](00-tong-quan.md)
cảnh báo: cần precision mới biết trong 262 cái đó bao nhiêu là thật.

Thêm một dấu hiệu đáng ngờ: trên `gemini-3-flash-preview` (model **mạnh hơn**), SAIST
chỉ ra 8/3/11 finding; trên `flash-lite` (**yếu hơn**) nó ra 39/43/41. Model yếu hơn mà
báo nhiều hơn gấp 4 lần thường có nghĩa là **nhiều false positive hơn**, không phải
"tìm giỏi hơn". Giai đoạn 7 phải trả lời câu này.

### Hai lưu ý đo lường cho Giai đoạn 5

**1. KHÔNG cộng dồn `latency_s` để báo cáo "thời gian tool".** Ở lần chạy SAIST đo
thử, tổng `latency_s` của 37 call là **48.5s** trong khi wall clock chỉ **34s**.
Không mâu thuẫn — SAIST chạy song song (`file-concurrency` mặc định 20). Cộng dồn
latency sẽ làm tool chạy **song song** trông *chậm hơn* tool chạy **tuần tự**, tức
ngược hẳn sự thật. Dùng `wall_clock_s` trong `run_meta.json`. Tổng latency chỉ có ý
nghĩa như thước đo *tổng công* LLM.

**2. Ước tính chi phí phải dựa trên ĐO, không suy từ model cùng họ.** Tôi từng ước
"cả mẻ dưới $1" bằng cách ngoại suy từ `gemini-3.1-flash-lite`; chạy thật trên
`gemini-3-flash-preview` thì hết **$17.45 mà chưa xong một nửa** — sai ~70 lần.
Nguyên nhân: model có reasoning sinh 3,400–5,600 token đầu ra mỗi call, so với ~90
token của model không reasoning. **Reasoning token tính vào output và quyết định hoá
đơn.** Trước khi chạy mẻ mới trên model mới, luôn gửi 1 call thật với prompt thật rồi
đọc `thoughtsTokenCount`.

➡️ Tiếp theo (Giai đoạn 5): chuẩn hoá SARIF/JSON của mỗi tool về **một schema
JSONL chung** (file, dòng, CWE, severity, mô tả) rồi gộp token theo tool.
