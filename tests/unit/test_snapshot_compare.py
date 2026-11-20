import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from compare_api_snapshots import compare_values  # noqa: E402


def test_average_zone_temperature_roundoff_is_within_resolution():
    differences, tolerated = compare_values(
        {"avg_zone_temp": 17.872061855670097},
        {"avg_zone_temp": 17.8720618556701},
    )
    assert differences == []
    assert len(tolerated) == 1 and tolerated[0][0] == "$.avg_zone_temp"
    assert tolerated[0][3] < 1e-12


def test_operationally_visible_numeric_change_is_not_hidden():
    differences, tolerated = compare_values({"temperature_c": 20.0}, {"temperature_c": 20.01})
    assert tolerated == []
    assert differences == [("$.temperature_c", 20.0, 20.01)]


def test_non_numeric_fields_remain_exact():
    differences, tolerated = compare_values({"count": 1, "status": "OK"}, {"count": 2, "status": "OK"})
    assert tolerated == []
    assert differences == [("$.count", 1, 2)]


if __name__ == "__main__":
    for test in (test_average_zone_temperature_roundoff_is_within_resolution,
                 test_operationally_visible_numeric_change_is_not_hidden,
                 test_non_numeric_fields_remain_exact):
        test()
        print(f"PASS {test.__name__}")
