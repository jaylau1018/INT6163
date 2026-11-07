"""A 部分：医院患者数据的数据字典、质量分析与清洗审计。

用法：
    python A_数据质量分析.py --data /path/to/data.xlsx --out outputs/a_quality

支持 CSV、Excel 和 Parquet。脚本不会修改原始文件；所有清洗规则均输出为
可审计的统计表，便于把实际运行结果填入报告。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import pandas as pd


def load_data(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=None)
        frames = [frame.assign(_source_sheet=name) for name, frame in sheets.items()]
        return pd.concat(frames, ignore_index=True)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"不支持的文件格式: {path.suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_name(name: object) -> str:
    value = re.sub(r"\s+", "_", str(name).strip())
    return re.sub(r"[^\w\u4e00-\u9fff]", "_", value).strip("_").lower()


def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in df.columns:
        series = df[column]
        non_null = series.dropna()
        row = {
            "column": column,
            "normalised_name": normalise_name(column),
            "dtype": str(series.dtype),
            "n_rows": len(series),
            "n_missing": int(series.isna().sum()),
            "missing_rate": float(series.isna().mean()),
            "n_unique": int(series.nunique(dropna=True)),
            "unique_rate_non_null": (
                float(series.nunique(dropna=True) / len(non_null))
                if len(non_null)
                else 0.0
            ),
        }
        if pd.api.types.is_numeric_dtype(series):
            row.update(
                {
                    "min": series.min(),
                    "p01": series.quantile(0.01),
                    "median": series.median(),
                    "p99": series.quantile(0.99),
                    "max": series.max(),
                    "n_non_finite": int((~series.replace([float("inf"), float("-inf")], pd.NA).notna()).sum()),
                }
            )
        else:
            row.update({"min": None, "p01": None, "median": None, "p99": None, "max": None, "n_non_finite": None})
        rows.append(row)
    return pd.DataFrame(rows)


def duplicate_summary(df: pd.DataFrame) -> pd.DataFrame:
    duplicate_rows = int(df.duplicated().sum())
    return pd.DataFrame(
        [
            {
                "n_rows": len(df),
                "n_duplicate_rows": duplicate_rows,
                "duplicate_rate": duplicate_rows / len(df) if len(df) else 0.0,
            }
        ]
    )


def candidate_id_columns(df: pd.DataFrame) -> list[str]:
    return [
        column
        for column in df.columns
        if re.search(r"(^|[_\s-])(id|no|number|code)$", str(column), re.I)
        or re.search(r"(patient|record|encounter).*(id|no|number)", str(column), re.I)
    ]


def value_checks(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in df.columns:
        series = pd.to_numeric(
            df[column].astype("string").str.replace(r"[$,]", "", regex=True)
            .replace({"$-": pd.NA, "-": pd.NA, "": pd.NA}),
            errors="coerce",
        )
        if series.notna().sum() == 0:
            continue
        rows.append(
            {
                "column": column,
                "n_negative": int((series < 0).sum()),
                "n_zero": int((series == 0).sum()),
                "n_infinite": int(series.isin([float("inf"), float("-inf")]).sum()),
                "n_numeric": int(series.notna().sum()),
            }
        )
    return pd.DataFrame(rows)


def categorical_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in df.select_dtypes(include=["object", "string"]).columns:
        if column == "_source_sheet" or column in candidate_id_columns(df):
            continue
        counts = df[column].value_counts(dropna=False)
        if len(counts) > 20:
            continue
        for value, count in counts.items():
            rows.append(
                {
                    "column": column,
                    "value": value,
                    "count": int(count),
                    "share": float(count / len(df)) if len(df) else 0.0,
                }
            )
    return pd.DataFrame(rows)


def time_checks(df: pd.DataFrame) -> pd.DataFrame:
    date_columns = [c for c in df.columns if "date" in str(c).lower()]
    time_columns = [c for c in df.columns if "time" in str(c).lower()]
    rows = []
    for column in date_columns:
        parsed = pd.to_datetime(df[column], errors="coerce")
        rows.append(
            {
                "column": column,
                "n_parseable": int(parsed.notna().sum()),
                "n_unparseable": int(parsed.isna().sum()),
                "min": parsed.min(),
                "max": parsed.max(),
            }
        )
    if time_columns:
        parsed = {c: pd.to_datetime(df[c].astype("string"), format="%H:%M:%S", errors="coerce") for c in time_columns}
        for left, right in zip(time_columns, time_columns[1:]):
            invalid = (parsed[right] < parsed[left]).fillna(False)
            rows.append(
                {
                    "column": f"{left} -> {right}",
                    "n_parseable": int(parsed[left].notna().sum() & parsed[right].notna().sum()),
                    "n_unparseable": int((parsed[left].isna() | parsed[right].isna()).sum()),
                    "min": None,
                    "max": f"n_reverse={int(invalid.sum())}",
                }
            )
    return pd.DataFrame(rows)


def clean_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply transparent, non-destructive cleaning rules and return an audit."""
    data = df.copy()
    audit = []

    def record(step: str, before: int, after: int, action: str) -> None:
        audit.append(
            {
                "step": step,
                "rows_before": before,
                "rows_after": after,
                "rows_removed": before - after,
                "action": action,
            }
        )

    before = len(data)
    data.columns = [str(column).strip() for column in data.columns]
    record("trim_column_names", before, len(data), "去除列名首尾空格，不删除记录")

    before = len(data)
    data = data.drop_duplicates().copy()
    record("remove_exact_duplicates", before, len(data), "删除完全重复行")

    money_columns = ["Medication Revenue", "Lab Cost", "Consultation Revenue"]
    for column in money_columns:
        if column in data:
            data[column] = pd.to_numeric(
                data[column].astype("string")
                .str.replace(r"[$,]", "", regex=True)
                .replace({"$-": pd.NA, "-": pd.NA, "": pd.NA}),
                errors="coerce",
            )
    date_columns = [column for column in data if "date" in str(column).lower()]
    for column in date_columns:
        data[column] = pd.to_datetime(data[column], errors="coerce")

    time_columns = [column for column in data if "time" in str(column).lower()]
    parsed_times = {
        column: pd.to_datetime(data[column].astype("string"), format="%H:%M:%S", errors="coerce")
        for column in time_columns
    }
    if {"Entry Time", "Post-Consultation Time", "Completion Time"} <= set(parsed_times):
        invalid_time = (
            (parsed_times["Entry Time"] > parsed_times["Post-Consultation Time"])
            | (parsed_times["Post-Consultation Time"] > parsed_times["Completion Time"])
        )
        data["_invalid_time_order"] = invalid_time
    else:
        data["_invalid_time_order"] = False

    data["_invalid_time_order"] = data["_invalid_time_order"].fillna(True)
    record(
        "flag_invalid_time_order",
        len(data),
        len(data),
        "标记时间顺序异常，不静默删除；下游建模须明确处理",
    )

    constant_columns = [
        column for column in data.columns
        if not str(column).startswith("_") and data[column].nunique(dropna=False) <= 1
    ]
    for column in constant_columns:
        data[f"_constant_{normalise_name(column)}"] = data[column].nunique(dropna=False)
    record(
        "flag_constant_columns",
        len(data),
        len(data),
        f"标记常量字段：{', '.join(map(str, constant_columns)) or '无'}；不自动删除",
    )
    return data, pd.DataFrame(audit)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", default=Path("outputs/a_quality"), type=Path)
    parser.add_argument("--write-cleaned", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    df = load_data(args.data)
    df.columns = [str(column).strip() for column in df.columns]
    cleaned, cleaning_audit = clean_data(df)

    profile_columns(df).to_csv(args.out / "column_profile.csv", index=False, encoding="utf-8-sig")
    duplicate_summary(df).to_csv(args.out / "duplicate_summary.csv", index=False, encoding="utf-8-sig")
    value_checks(df).to_csv(args.out / "numeric_value_checks.csv", index=False, encoding="utf-8-sig")
    categorical_summary(df).to_csv(args.out / "categorical_summary.csv", index=False, encoding="utf-8-sig")
    time_checks(df).to_csv(args.out / "time_checks.csv", index=False, encoding="utf-8-sig")
    cleaning_audit.to_csv(args.out / "cleaning_audit.csv", index=False, encoding="utf-8-sig")
    if args.write_cleaned:
        cleaned.to_csv(args.out / "cleaned_data.csv", index=False, encoding="utf-8-sig")

    metadata = {
        "source_file": str(args.data.resolve()),
        "source_sha256": sha256(args.data),
        "source_bytes": args.data.stat().st_size,
        "rows_raw": int(len(df)),
        "columns_loaded": [str(column) for column in df.columns],
        "columns_raw": [
            str(column) for column in df.columns if str(column) != "_source_sheet"
        ],
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "cleaned_output_written": bool(args.write_cleaned),
        "target": "未在数据文件中提供；须由 B/C 明确标签和预测时点后再建模",
        "prohibited_features": ["Patient ID", "任何在预测时点之后产生的字段"],
    }
    (args.out / "provenance.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ids = candidate_id_columns(df)
    id_rows = []
    for column in ids:
        id_rows.append(
            {
                "column": column,
                "n_unique": int(df[column].nunique(dropna=True)),
                "n_missing": int(df[column].isna().sum()),
                "n_duplicate_non_null": int(df[column].dropna().duplicated().sum()),
            }
        )
    pd.DataFrame(id_rows).to_csv(args.out / "candidate_id_checks.csv", index=False, encoding="utf-8-sig")

    with (args.out / "run_summary.txt").open("w", encoding="utf-8") as file:
        file.write(f"source={args.data.resolve()}\n")
        file.write(f"rows={len(df)}\n")
        file.write(f"columns={len(df.columns)}\n")
        file.write(f"duplicate_rows={int(df.duplicated().sum())}\n")
        file.write("columns=" + ", ".join(map(str, df.columns)) + "\n")
        file.write(f"sha256={metadata['source_sha256']}\n")

    print(f"rows={len(df)}, columns={len(df.columns)}")
    print(f"wrote={args.out.resolve()}")


if __name__ == "__main__":
    main()
