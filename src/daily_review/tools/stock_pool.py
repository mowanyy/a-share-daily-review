"""供选股数据.csv 按日期切分（CLI: python -m daily_review split-pool）。

读取 776MB 供选股数据.csv，按 交易日期 分组，写入 data/stock_pool/{日期}.csv。
218 个月末日期 → 218 个 ~3.5MB 小文件。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from daily_review.config import get_settings

_DATE_COL = "交易日期"


def split_stock_pool_by_date(
    src: str | Path | None = None,
    *,
    out_dir: str | Path | None = None,
    show_progress: bool = True,
) -> dict[str, int]:
    """按日期切分供选股数据。

    src: 源 CSV 路径，缺省为项目根目录的 供选股数据.csv。
    out_dir: 输出目录，缺省为 settings.stock_pool_dir。
    show_progress: 是否打印进度。

    返回 {日期: 行数} 字典。
    """
    src_path = Path(src) if src else get_settings().project_root / "供选股数据.csv"
    out = Path(out_dir) if out_dir else get_settings().stock_pool_dir

    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {}

    if show_progress:
        print(f"[split-pool] 读取 {src_path} ...")

    # 分块读取避免内存爆炸（776MB 可一次性读，但分块更安全）
    chunksize = 50000
    for i, chunk in enumerate(pd.read_csv(src_path, dtype=str, chunksize=chunksize)):
        if _DATE_COL not in chunk.columns:
            raise ValueError(f"源文件缺少 {_DATE_COL} 列")
        # 标准化日期格式：YYYY-MM-DD → YYYYMMDD
        dates = chunk[_DATE_COL].str.replace("-", "", regex=False)
        for date_val, group in chunk.groupby(dates):
            date_str = str(date_val).strip() if pd.notna(date_val) else "unknown"
            out_path = out / f"{date_str}.csv"
            # 追加模式（如果文件已存在则不写表头）
            header = not out_path.exists()
            group.to_csv(
                out_path, mode="a", index=False, encoding="utf-8-sig",
                header=header,
            )
            result[date_str] = result.get(date_str, 0) + len(group)

        if show_progress and (i + 1) % 5 == 0:
            print(f"  已处理 {i + 1} 个块（{(i + 1) * chunksize} 行）...")

    if show_progress:
        print(f"\n[split-pool] 完成！共 {len(result)} 个日期文件 -> {out}/")
        small = [k for k, v in result.items() if v < 100]
        if small:
            print(f"  小文件（<100行）: {len(small)} 个，如 {small[:5]}")
    return result