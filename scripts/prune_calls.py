"""Preview or remove call records older than a chosen number of days."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from routelet.paths import resolve_path


def positive_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("保留天数必须为正整数") from exc
    if days < 1:
        raise argparse.ArgumentTypeError("保留天数必须为正整数")
    return days


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="预览或清理过期调用记录")
    parser.add_argument("--older-than-days", type=positive_days, required=True)
    parser.add_argument("--db", help="数据库路径，默认 ~/.routelet/calls.db")
    parser.add_argument("--apply", action="store_true", help="执行删除；默认只预览")
    parser.add_argument(
        "--vacuum", action="store_true", help="删除后回收磁盘空间，需要 --apply"
    )
    args = parser.parse_args(argv)
    if args.vacuum and not args.apply:
        parser.error("--vacuum 需要同时指定 --apply")

    db_path = resolve_path(args.db, "calls.db")
    if not db_path.is_file():
        parser.error(f"数据库文件不存在: {db_path}")

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    ).isoformat()
    mode = "rw" if args.apply else "ro"
    if args.apply and os.name == "posix":
        os.umask(0o077)
    try:
        with closing(
            sqlite3.connect(db_path.as_uri() + f"?mode={mode}", uri=True)
        ) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM calls WHERE timestamp < ?", (cutoff,)
            ).fetchone()[0]
            if args.apply:
                deleted = conn.execute(
                    "DELETE FROM calls WHERE timestamp < ?", (cutoff,)
                ).rowcount
                conn.commit()
                if args.vacuum:
                    try:
                        conn.execute("VACUUM")
                    except sqlite3.Error as exc:
                        parser.exit(1, f"已清理 {deleted} 条，但 VACUUM 失败: {exc}\n")
    except sqlite3.Error as exc:
        parser.exit(1, f"数据库操作失败: {exc}\n")

    if args.apply:
        print(f"已清理 {deleted} 条早于 {cutoff} 的调用记录。")
        if args.vacuum:
            print("VACUUM 已完成，空闲数据库页已回收。")
    else:
        print(f"预览：{count} 条调用记录早于 {cutoff}；添加 --apply 执行清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
