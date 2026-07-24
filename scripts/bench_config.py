#!/usr/bin/env python3
"""
bench_config.py — truy vấn config/benchmark.yaml (single source of truth).

Vì sao có file này: hàm `yaml_get` trong lib_common.sh (awk) chỉ đọc được scalar
phẳng `parent.child`. Danh sách `tools:` là list của dict -> cần parser YAML thật.

Chạy qua uv để không phải cài PyYAML vào hệ thống:
    uv run --with pyyaml python scripts/bench_config.py <lệnh>

Lệnh:
    get <path>                 # vd: get model.id
    tools-enabled              # in id các tool enabled=true, mỗi dòng 1 id
    tool <id> <field>          # vd: tool datadog-saist model_alias
    aliases                    # in model_alias của mọi tool enabled (để kiểm proxy)
"""
import sys
import os

import yaml

# Trên Windows, print() mặc định xuất CRLF. Script bash đọc kết quả này bằng
# $(...) — ký tự \r KHÔNG nằm trong IFS nên nó dính vào cuối biến, tạo ra
# "datadog-saist\r". Hệ quả: so sánh chuỗi và `case` im lặng không khớp, còn
# đường dẫn thì thành "adapters/datadog-saist\r.sh". Ép LF để cắt tận gốc.
sys.stdout.reconfigure(newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "config", "benchmark.yaml")


def load():
    with open(_CFG, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cmd_get(cfg, path):
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            sys.exit(f"[bench_config] không tìm thấy path: {path}")
        cur = cur[part]
    print(cur)


def enabled_tools(cfg):
    return [t for t in (cfg.get("tools") or []) if t.get("enabled")]


def cmd_tool(cfg, tool_id, field):
    for t in cfg.get("tools") or []:
        if t.get("id") == tool_id:
            if field not in t:
                sys.exit(f"[bench_config] tool '{tool_id}' không có field '{field}'")
            print(t[field])
            return
    sys.exit(f"[bench_config] không thấy tool id '{tool_id}'")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = load()
    cmd = sys.argv[1]

    if cmd == "get":
        cmd_get(cfg, sys.argv[2])
    elif cmd == "tools-enabled":
        for t in enabled_tools(cfg):
            print(t["id"])
    elif cmd == "tool":
        cmd_tool(cfg, sys.argv[2], sys.argv[3])
    elif cmd == "aliases":
        for t in enabled_tools(cfg):
            if t.get("model_alias"):
                print(t["model_alias"])
    else:
        sys.exit(f"[bench_config] lệnh lạ: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
