# battery-uptime-report

Pulls hourly battery telemetry from Athena and produces a weekly uptime table (FR & GB).

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Configure AWS credentials**

You need read access to the `prod_bo2dl_bloqs_gluedb_prepared` Athena database.
The easiest way is via the AWS CLI:
```bash
aws configure
```
Enter your `AWS Access Key ID`, `Secret Access Key`, and set region to `eu-central-1`.

Alternatively, set environment variables:
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=eu-central-1
```

## Usage

```bash
# Previous month + current month (e.g. April + May 2026)
# Start from the Monday of the first ISO week you want to cover
python run.py --start 2026-03-30

# Explicit date range
python run.py --start 2026-03-30 --end 2026-05-14
```

`--start` is inclusive, `--end` is exclusive (defaults to tomorrow).

## Output

Prints the table to the console and saves a CSV named `weekly_uptime_<start>_to_<end>.csv`.

| year | week | country | total_hours | bloqs |
|------|------|---------|-------------|-------|
| 2026 | 14   | FR      | 454177      | 1094  |
| ...  | ...  | ...     | ...         | ...   |

- **total_hours** — sum of battery-hours reported with SOC > 0 across all batteries in FR/GB
- **bloqs** — number of distinct bloqs that reported that week
- Partial weeks (first/last) will have lower totals than full weeks
