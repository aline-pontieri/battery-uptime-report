"""
Weekly battery uptime report — FR & GB.

Usage:
    python run.py --start 2026-03-30 --end 2026-05-14
    python run.py --start 2026-03-30          # end defaults to today
"""

import argparse
from datetime import date, timedelta
import pandas as pd
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor

# ── Athena config ─────────────────────────────────────────────────────────────
REGION     = "eu-central-1"
S3_STAGING = "s3://prod-athena-global-s3-eu-central-1/"
CATALOG    = "AwsDataCatalog"
DATABASE   = "prod_bo2dl_bloqs_gluedb_prepared"
# ─────────────────────────────────────────────────────────────────────────────

SQL_TEMPLATE = """
WITH ranked AS (
    SELECT
        bloqs_id,
        details,
        firmwaredetails,
        date_trunc('hour', system_updated_at) AS hour_bucket,
        ROW_NUMBER() OVER (
            PARTITION BY bloqs_id, date_trunc('hour', system_updated_at)
            ORDER BY system_updated_at DESC
        ) AS rn
    FROM prod_bo2dl_bloqs_gluedb_prepared.prod_bo2dl_bloqs_prepared_iceberg_t
    WHERE system_updated_at >= DATE '{start}'
      AND system_updated_at <  DATE '{end}'
      AND powersource = 'BATTERY'
      AND lifecyclestatus IS NOT NULL
      AND lifecyclestatus <> 'terminated'
),
pre_filtered AS (
    SELECT *
    FROM ranked
    WHERE rn = 1
      AND lower(firmwaredetails) LIKE '%bat1%'
      AND lower(firmwaredetails) LIKE '%bat2%'
),
daily_battery AS (
    SELECT
        pf.bloqs_id,
        pf.hour_bucket,
        json_extract_scalar(bat, '$.batteryName')                           AS battery_name,
        CAST(json_extract_scalar(bat, '$.capacityStateOffCharge') AS DOUBLE) AS capacity_state_off_charge,
        json_extract_scalar(pf.details, '$.country')                        AS country
    FROM pre_filtered pf
    CROSS JOIN UNNEST(
        CAST(json_parse(json_extract_scalar(pf.firmwaredetails, '$.battery')) AS array(json))
    ) AS t(bat)
    WHERE json_extract_scalar(bat, '$.batteryName') IS NOT NULL
      AND json_extract_scalar(bat, '$.batteryName') <> ''
      AND json_extract_scalar(bat, '$.capacityStateOffCharge') IS NOT NULL
      AND json_extract_scalar(bat, '$.capacityStateOffCharge') <> ''
)
SELECT bloqs_id, hour_bucket, battery_name, capacity_state_off_charge, country
FROM daily_battery
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Weekly battery uptime report")
    parser.add_argument("--start", required=True, help="Start date (inclusive), e.g. 2026-03-30")
    parser.add_argument("--end",   default=str(date.today() + timedelta(days=1)),
                        help="End date (exclusive), e.g. 2026-05-14. Defaults to tomorrow.")
    return parser.parse_args()


def fetch(start: str, end: str) -> pd.DataFrame:
    sql = SQL_TEMPLATE.format(start=start, end=end)
    print(f"Querying Athena: {start} to {end} ...")
    conn = connect(
        s3_staging_dir=S3_STAGING,
        region_name=REGION,
        catalog_name=CATALOG,
        schema_name=DATABASE,
        cursor_class=PandasCursor,
    )
    df = conn.cursor().execute(sql).as_pandas()
    print(f"  {len(df):,} rows | {df['bloqs_id'].nunique()} bloqs")
    return df


def compute_weekly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.rename(columns={"hour_bucket": "timestamp", "capacity_state_off_charge": "SOC"}, inplace=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.floor("h")

    df_base = df[df["country"].isin(["FR", "GB"])][
        ["bloqs_id", "battery_name", "timestamp", "SOC", "country"]
    ].copy()

    # Build full hourly grid per battery
    rows = []
    for (bloq, bat, ctry), grp in df_base.groupby(["bloqs_id", "battery_name", "country"]):
        rows.append(pd.DataFrame({
            "timestamp":    pd.date_range(start=grp["timestamp"].min(),
                                          end=grp["timestamp"].max(), freq="h"),
            "bloqs_id":     bloq,
            "battery_name": bat,
            "country":      ctry,
        }))
    df_hours = pd.concat(rows, ignore_index=True)

    df_merged = df_hours.merge(df_base, on=["bloqs_id", "battery_name", "country", "timestamp"], how="left")
    df_merged["uptime_soc"] = (df_merged["SOC"] > 0).astype(int)

    valid = (
        df_merged.groupby(["bloqs_id", "battery_name", "country"])["uptime_soc"]
        .sum().reset_index()
    )
    valid = valid[valid["uptime_soc"] > 0][["bloqs_id", "battery_name", "country"]]
    df_clean = df_merged.merge(valid, on=["bloqs_id", "battery_name", "country"], how="inner")

    iso = df_clean["timestamp"].dt.isocalendar()
    df_clean["year"] = iso.year.astype(int)
    df_clean["week"] = iso.week.astype(int)

    return (
        df_clean
        .groupby(["year", "week", "country"], observed=True)
        .agg(total_hours=("timestamp", "count"), bloqs=("bloqs_id", "nunique"))
        .reset_index()
        .sort_values(["country", "year", "week"])
        [["year", "week", "country", "total_hours", "bloqs"]]
    )


def main():
    args = parse_args()
    df_raw = fetch(args.start, args.end)
    weekly = compute_weekly(df_raw)

    print("\n=== Weekly uptime ===")
    print(weekly.to_string(index=False))

    out = f"weekly_uptime_{args.start}_to_{args.end}.csv"
    weekly.to_csv(out, index=False)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
