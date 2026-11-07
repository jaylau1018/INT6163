"""A 部分：医院患者数据的数据字典与质量分析。

用法：
    python A_数据质量分析.py --data /path/to/data.csv --out outputs/a_quality

支持 CSV、Excel 和 Parquet。脚本不会修改原始文件；所有清洗规则均输出为
可审计的统计表，便于把实际运行结果填入报告。
"""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", default=Path("outputs/a_quality"), type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    df = load_data(args.data)
    df.columns = [str(column).strip() for column in df.columns]

    profile_columns(df).to_csv(args.out / "column_profile.csv", index=False, encoding="utf-8-sig")
    duplicate_summary(df).to_csv(args.out / "duplicate_summary.csv", index=False, encoding="utf-8-sig")
    value_checks(df).to_csv(args.out / "numeric_value_checks.csv", index=False, encoding="utf-8-sig")
    categorical_summary(df).to_csv(args.out / "categorical_summary.csv", index=False, encoding="utf-8-sig")
    time_checks(df).to_csv(args.out / "time_checks.csv", index=False, encoding="utf-8-sig")

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

    print(f"rows={len(df)}, columns={len(df.columns)}")
    print(f"wrote={args.out.resolve()}")


if __name__ == "__main__":
    main()
