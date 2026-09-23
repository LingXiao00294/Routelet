"""快速查看调用记录，默认读取 ~/.routelet/calls.db。"""

import sqlite3
import sys

from routelet.paths import resolve_path

db_path = resolve_path(sys.argv[1] if len(sys.argv) > 1 else None, "calls.db")
if not db_path.is_file():
    raise SystemExit(f"数据库文件不存在: {db_path}")
conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, timestamp, virtual_model, provider_type, provider_model, "
    "status, latency_ms, input_tokens, output_tokens, cache_read_tokens, "
    "cache_write_tokens, cost_usd, error_type, error_message "
    "FROM calls ORDER BY timestamp DESC LIMIT 20"
).fetchall()

fmt = "{:<38} {:<20} {:<10} {:<22} {:<8} {:<8} {:<6} {:<6}"
print(
    fmt.format("ID", "时间", "虚模型", "实模型", "状态", "延迟ms", "入Token", "出Token")
)
print("-" * 130)
for r in rows:
    d = dict(r)
    print(
        fmt.format(
            d["id"][:36],
            d["timestamp"][:19] or "-",
            (d["virtual_model"] or "-")[:18],
            (d["provider_model"] or "-")[:20],
            d["status"] or "-",
            str(d["latency_ms"] or "-"),
            str(d["input_tokens"] or "-"),
            str(d["output_tokens"] or "-"),
        )
    )

total = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
success = conn.execute(
    "SELECT COUNT(*) FROM calls WHERE status = 'success'"
).fetchone()[0]
cost = conn.execute("SELECT SUM(cost_usd) FROM calls").fetchone()[0] or 0
print(
    f"\n总计: {total} 条 | 成功: {success} | 失败: {total - success} | 总费用: ${cost:.6f}"
)
conn.close()
