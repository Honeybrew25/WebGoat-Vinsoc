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
nhau (`gemini-3.1-flash-lite-saist`, `...-metis`), nhưng **mọi alias đều trỏ về
cùng một model thật với cùng tham số** (dùng YAML anchor `&gemini_params` trong
`litellm_config.yaml`). Tính công bằng không đổi — chỉ khác cái nhãn dán ngoài.
`token_logger.py` suy ra tool từ hậu tố alias.

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
  "model_alias": "gemini-3.1-flash-lite-metis",
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
- **Metis dùng provider `vllm` chứ không phải `gemini`.** `vllm` là provider
  OpenAI-compatible; nó nói `/v1/chat/completions` — đúng endpoint mà
  `token_logger` cắm callback. Nếu dùng provider `gemini` thì Metis nói giao thức
  Gemini gốc và ta mất điểm đo. **Model vẫn là Gemini**, chỉ khác giao thức.

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

### Header bị bỏ im lặng — `additional_headers` vs `default_headers`

Provider `vllm` của Metis chỉ nhận **`default_headers`** (xem
`CONFIG_SPEC.copy_keys` trong `src/metis/providers/vllm.py`). Key
`additional_headers` **chỉ tồn tại ở provider `gemini`**. Đặt nhầm tên thì Metis
không báo lỗi gì cả — header đơn giản biến mất, và mọi call rơi về tool
`unknown` trong `calls.jsonl`.

Bài học: với config kiểu "gõ sai key thì im lặng bỏ qua", đừng tin tài liệu của
provider khác — đọc thẳng `copy_keys` trong mã nguồn.

## 9. Xong Giai đoạn 4 khi...

- [x] `stage4_setup_tools.sh` cài được cả hai tool (image Docker + venv).
- [x] `stage4_run.sh --dry-run` preflight sạch.
- [ ] Chạy thật xong, mỗi tool có đủ `run-01-cold` … `run-03-warm` với
      `exit_code: 0` và `raw_output.sarif` khác rỗng.
- [ ] `llm_calls` trong `run_meta.json` khớp số dòng tăng thêm trong `calls.jsonl`,
      và cột `tool` trong log không có giá trị `unknown`.

➡️ Tiếp theo (Giai đoạn 5): chuẩn hoá SARIF/JSON của mỗi tool về **một schema
JSONL chung** (file, dòng, CWE, severity, mô tả) rồi gộp token theo tool.
