import pytest

from scripts.readme_charts import write_charts

DELIVERY_HEADER = (
    "fuel_group,unit_periods,units,first_date,last_date,median_deviation_mwh,"
    "p25_deviation_mwh,p75_deviation_mwh,abs_metered_mwh,abs_pn_mwh,"
    "instructed_periods,instructed_period_share,abs_deviation_mwh,"
    "abs_instructed_deviation_mwh,abs_unexplained_shortfall_mwh,"
    "instructed_share_of_abs_deviation,unit_periods_by_run,analysis_read_until"
)

# The charts order the groups by metered energy (nuclear, gas, wind, oil)
# and by deviation (wind, gas, nuclear, oil); the input order matches
# neither. Oil has no deviation, so its instructed share is undefined.
DELIVERY_ROWS = [
    "wind,400,20,2026-08-22,2026-10-10,-1.250,-4.000,0.500,9000.0,9500.0,"
    '120,0.300000,3000.0,900.0,2100.0,0.300000,"{""SF"": 400}",'
    "2026-10-25 00:00:00+00",
    "nuclear,150,8,2026-08-22,2026-10-11,-0.200,-0.900,0.100,40000.0,40500.0,"
    '10,0.066667,500.0,50.0,450.0,0.100000,"{""SF"": 150}",'
    "2026-10-25 00:00:00+00",
    "gas_turbine,300,15,2026-08-23,2026-10-11,0.125,-0.750,1.000,25000.0,"
    '25100.0,200,0.666667,1200.0,1000.0,200.0,0.833333,"{""R1"": 100, ""SF"": 200}",'
    "2026-10-25 00:00:00+00",
    "oil,50,5,2026-08-24,2026-10-09,0.000,0.000,0.000,100.0,100.0,"
    '0,0.000000,0.0,0.0,0.0,,"{""SF"": 50}",2026-10-25 00:00:00+00',
]

EXPECTED_Q3_CSV = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
    "fuel_group,unit_periods,first_date,last_date,p25_deviation_mwh,"
    "median_deviation_mwh,p75_deviation_mwh,analysis_read_until\n"
    "nuclear,150,2026-08-22,2026-10-11,-0.900,-0.200,0.100,2026-10-25 00:00:00+00\n"
    "gas_turbine,300,2026-08-23,2026-10-11,-0.750,0.125,1.000,2026-10-25 00:00:00+00\n"
    "wind,400,2026-08-22,2026-10-10,-4.000,-1.250,0.500,2026-10-25 00:00:00+00\n"
    "oil,50,2026-08-24,2026-10-09,0.000,0.000,0.000,2026-10-25 00:00:00+00\n"
)

EXPECTED_Q12_CSV = (
    "# Contains BMRS data © Elexon Limited copyright and database right 2026.\n"
    "fuel_group,unit_periods,first_date,last_date,abs_instructed_deviation_mwh,"
    "abs_unexplained_shortfall_mwh,instructed_share_of_abs_deviation,"
    "analysis_read_until\n"
    "wind,400,2026-08-22,2026-10-10,900.0,2100.0,0.300000,2026-10-25 00:00:00+00\n"
    "gas_turbine,300,2026-08-23,2026-10-11,1000.0,200.0,0.833333,2026-10-25 00:00:00+00\n"
    "nuclear,150,2026-08-22,2026-10-11,50.0,450.0,0.100000,2026-10-25 00:00:00+00\n"
    "oil,50,2026-08-24,2026-10-09,0.0,0.0,,2026-10-25 00:00:00+00\n"
)


def write_delivery_csv(path, rows):
    path.write_text("\n".join([DELIVERY_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def output_files(folder):
    files = {}
    for path in sorted(folder.iterdir()):
        files[path.name] = path.read_bytes()
    return files


def test_a_rerun_writes_identical_files(tmp_path):
    delivery = write_delivery_csv(tmp_path / "delivery.csv", DELIVERY_ROWS)
    write_charts(delivery, tmp_path / "first")
    write_charts(delivery, tmp_path / "second")

    first = output_files(tmp_path / "first")
    assert sorted(first) == [
        "q12_instructed_share_by_fuel_group.csv",
        "q12_instructed_share_by_fuel_group.png",
        "q3_deviation_by_fuel_group.csv",
        "q3_deviation_by_fuel_group.png",
    ]
    assert output_files(tmp_path / "second") == first


def test_each_chart_csv_holds_its_rows_in_reading_order_with_attribution(tmp_path):
    delivery = write_delivery_csv(tmp_path / "delivery.csv", DELIVERY_ROWS)
    write_charts(delivery, tmp_path / "out")

    q3 = (tmp_path / "out" / "q3_deviation_by_fuel_group.csv").read_bytes()
    q12 = (tmp_path / "out" / "q12_instructed_share_by_fuel_group.csv").read_bytes()
    assert (q3, q12) == (
        EXPECTED_Q3_CSV.encode("utf-8"),
        EXPECTED_Q12_CSV.encode("utf-8"),
    )


def test_a_result_with_two_cutoffs_is_refused(tmp_path):
    second_read = DELIVERY_ROWS[2].replace(
        "2026-10-25 00:00:00+00", "2026-11-30 23:30:00+00"
    )
    delivery = write_delivery_csv(
        tmp_path / "delivery.csv", [DELIVERY_ROWS[0], second_read]
    )

    with pytest.raises(ValueError, match="one cutoff"):
        write_charts(delivery, tmp_path / "out")
