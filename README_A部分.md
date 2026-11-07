# A 部分复现说明：数据来源、质量与清洗审计

## 运行环境

建议使用 Python 3.10+，安装：

```bash
pip install pandas openpyxl pyarrow
```

## 数据来源与版本记录

- 数据集：Kaggle `abdulqaderasiirii/hospital-patient-data`
- 数据页：https://www.kaggle.com/datasets/abdulqaderasiirii/hospital-patient-data
- 下载方式：使用题目提供的 `kagglehub.dataset_download(...)` 代码
- 本次实际输入：`hospital_data_sampleee.xlsx`
- 下载/分析日期：2026-11-07
- Kaggle 数据集版本和许可证：当前上传材料未提供，不能臆测；提交前应在 Kaggle 页面核对后填入报告。

原始患者数据不提交到 GitHub。运行后 `provenance.json` 保存文件 SHA-256、文件大小、行数、列名和生成时间，保证结果可追溯。

## 执行

```bash
python A_数据质量分析.py \
  --data /path/to/hospital_data_sampleee.xlsx \
  --out outputs/a_quality \
  --write-cleaned
```

默认只生成审计结果；`--write-cleaned` 才会生成清洗数据。清洗数据包含患者标识，应限制访问，不应上传公共仓库。

## 输出文件

| 文件 | 用途 |
|---|---|
| `column_profile.csv` | 字段类型、缺失率、唯一值和数值摘要 |
| `categorical_summary.csv` | 低基数类别频数及比例 |
| `numeric_value_checks.csv` | 负数、零值、无穷值及可解析数值数量 |
| `time_checks.csv` | 日期范围、时间解析及时间逆序 |
| `duplicate_summary.csv` | 完全重复记录 |
| `candidate_id_checks.csv` | 候选 ID 的唯一性和缺失 |
| `cleaning_audit.csv` | 每一步清洗前后行数和规则 |
| `provenance.json` | 输入文件指纹和运行元数据 |
| `cleaned_data.csv` | 可选；仅本地生成，禁止提交 |

## 与 B/C 的接口约定

本数据没有明确的临床目标标签。B/C 必须在报告中先定义预测目标、预测时点和标签生成规则，再决定是否使用收入、服务时长或其他观测变量。任何预测时点之后才产生的字段都不能作为特征；`Patient ID` 只能用于审计，不能进入模型。训练集拟合的缺失值填补、编码、缩放和异常值处理必须通过 pipeline 完成。

