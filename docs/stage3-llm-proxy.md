# Giai đoạn 3 — LLM proxy đo token & thời gian (chiêu chính)

## Vấn đề cần giải

Muốn so chi phí giữa các tool, phải biết mỗi tool tốn bao nhiêu **token** và
**thời gian**. Nhưng:

- Mỗi tool tự report token một kiểu (mỗi ông một phách), có tool **giấu** hẳn.
- Nếu "token không có thì vibe code vào" (bịa) -> bảng số vô nghĩa.

## Ý tưởng

Đặt **một trạm trung chuyển (proxy)** đứng giữa mọi tool và Google Gemini:

```
   Tool SAST  ──►  LiteLLM proxy (127.0.0.1:4000)  ──►  Gemini (Google)
                        │
                        └──► ghi 1 dòng JSONL mỗi call:
                             tool, prompt_tokens, completion_tokens,
                             total_tokens, latency_s, cost_usd
```

**Mọi call bắt buộc đi qua proxy**, nên token lấy từ `usage` **thật** của Gemini
(`promptTokenCount`, `candidatesTokenCount`) — khách quan, không thể giấu. Chỉ khi
thật sự không lấy được mới ước lượng bằng tokenizer; đó mới là chỗ "vibe" chấp
nhận được, không phải bịa cả bảng.

## Vì sao chạy bằng Docker (không phải pip)

LiteLLM có dependency cần **biên dịch Rust**, cài bằng pip trên Windows hay vỡ
(thiếu Cargo/toolchain). Image Docker chính thức đóng gói sẵn mọi thứ -> chạy là
được, và **tái lập** tốt hơn (ai chạy cũng ra môi trường y hệt). File
`proxy/requirements.txt` vẫn giữ để ai muốn tự cài pip thì dùng.

## Các file trong `proxy/`

| File | Vai trò |
|---|---|
| `docker-compose.yml` | Định nghĩa container proxy: image, cổng, mount, env |
| `litellm_config.yaml` | Model ảo `gemini-3.1-flash-lite` + đăng ký logger + temp=0 |
| `token_logger.py` | Custom callback: mỗi call ghi 1 dòng JSONL |
| `.env` | `GEMINI_API_KEY` + `LITELLM_MASTER_KEY` — **cả hai đều bắt buộc** |
| `requirements.txt` | Phương án pip dự phòng |

## Chạy

```bash
# 1) Điền key (một lần)
cp proxy/.env.example proxy/.env        # nếu chưa có
#   mở proxy/.env:
#     GEMINI_API_KEY   = key ở https://aistudio.google.com/apikey (dạng AIza...)
#     LITELLM_MASTER_KEY = chuỗi bất kỳ bắt đầu bằng "sk-", KHÔNG để trống
#                          (để trống -> lỗi "No connected db", xem phần cuối file)

# 2) Bật proxy (nền)
bash scripts/stage3_start_proxy.sh

# 3) Kiểm tra: gửi 1 call thử, xác nhận có dòng token được ghi
bash scripts/stage3_start_proxy.sh --smoke-test

# Xem log container / tắt proxy
bash scripts/stage3_start_proxy.sh --logs
bash scripts/stage3_start_proxy.sh --down
```

## Tool trỏ vào proxy thế nào (Giai đoạn 4 sẽ dùng)

Proxy phơi ra endpoint **OpenAI-compatible**. Trong adapter mỗi tool, đặt:

| Thiết lập | Giá trị |
|---|---|
| Base URL / API base | `http://127.0.0.1:4000` |
| Endpoint | `/v1/chat/completions` |
| Model | `gemini-3.1-flash-lite` |
| API key (header) | `Authorization: Bearer <LITELLM_MASTER_KEY>` — phải đúng key, không phải chuỗi bất kỳ |
| **Header nhận diện tool** | `x-tool-name: <tên-tool>`  ← để proxy biết call của ai |

> Nhiều tool SAST cho phép cấu hình "OpenAI-compatible base URL" — trỏ nó vào đây
> là xong. Tool nào chỉ nói tiếng Gemini gốc thì dùng route passthrough `/gemini/...`
> của LiteLLM (sẽ hướng dẫn ở adapter tương ứng).

### Mấu chốt: `x-tool-name`
Proxy không tự biết call đến từ SAIST hay Metis. Mỗi adapter phải gắn header
`x-tool-name`. `token_logger.py` đọc header này -> cột `tool` trong log. Nhờ đó ta
**gộp token/time theo từng tool** ở Giai đoạn cuối.

## Định dạng log — `results/proxy_logs/calls.jsonl`

Mỗi dòng một call:

```json
{"ts":"2026-07-21T02:02:10Z","tool":"selftest","model":"gemini-3.1-flash-lite",
 "prompt_tokens":123,"completion_tokens":45,"total_tokens":168,
 "latency_s":1.83,"cost_usd":0.0001,"call_id":"..."}
```

Về sau chỉ cần `jq` gộp theo `tool`:

```bash
# Tổng token + số call mỗi tool
jq -s 'group_by(.tool)[] | {tool:.[0].tool,
       calls:length,
       input:  (map(.prompt_tokens)     | add),
       output: (map(.completion_tokens) | add),
       total:  (map(.total_tokens)      | add),
       cost:   (map(.cost_usd // 0)     | add)}' \
  results/proxy_logs/calls.jsonl
```

Con số này chính là phần `input_tokens/output_tokens/total_tokens/cost_usd` trong
schema thống kê ở Giai đoạn 5.

## Đã kiểm chứng những gì (khi dựng)

- ✅ Container khởi động, uvicorn chạy trên `0.0.0.0:4000`, health `200`.
- ✅ `token_logger.py` import sạch trong container (không lỗi callback).
- ✅ Gọi logger thử -> ghi đúng vào `/logs/calls.jsonl` (mount ra
  `results/proxy_logs/`), parse đúng `x-tool-name`, token, latency, cost.
- ✅ **Call THẬT qua Gemini đã chạy** (2026-07-21): `--smoke-test` trả 200 và ghi
  được dòng token thật vào `calls.jsonl` với `cost_usd` khác 0.

### Bẫy đã gặp: `No connected db`

Nếu `--smoke-test` trả **HTTP 400** kèm:

```json
{"error":{"message":"No connected db.","type":"no_db_connection","code":"400"}}
```

thì **không phải sai API key**. Nguyên nhân: `LITELLM_MASTER_KEY` trong `.env` để
trống, trong khi `litellm_config.yaml` có `master_key: os.environ/LITELLM_MASTER_KEY`.
Master key rỗng -> LiteLLM không nhận ra `Bearer ...` là master key nữa, nó coi đó là
*virtual key* và đi tra database (dự án này không dựng DB) -> 400.

**Cách sửa:** đặt `LITELLM_MASTER_KEY=sk-sast-bench-local` (phải bắt đầu bằng `sk-`)
rồi `docker compose up -d --force-recreate`.

## Xong Giai đoạn 3 khi...

- [x] `docker compose ... config` hợp lệ, image kéo được.
- [x] Proxy `up` -> health 200; logger ghi được JSONL.
- [x] `GEMINI_API_KEY` + `LITELLM_MASTER_KEY` đã điền, `--smoke-test` báo
      "Logger đã ghi token" với số liệu từ Gemini thật.

➡️ Tiếp theo (Giai đoạn 4): viết adapter cho từng tool (SAIST, Metis) để chúng
trỏ vào proxy này và quét `target/WebGoat`, kèm đo wall-clock và tách cold/warm.
