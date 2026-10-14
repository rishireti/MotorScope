"""Checks mobility metric parsing, normalisation, scoring, storage, and routes.

Usage:  .venv\\Scripts\\python.exe -m tests.test_mobility
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_mobility_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "mobility-test-key"
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, mobility, security  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "GaitAndStride2026"
failures: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


def csrf(client: TestClient, path: str) -> str:
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    return match.group(1) if match else ""


def sign_in(client: TestClient, email: str, display_name: str) -> int:
    """Create a verified account and sign it in, returning the user id."""
    db.create_user(email, display_name, security.hash_password(PASSWORD), email_verified=True)
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client, "/login"),
            "email": email,
            "password": PASSWORD,
            "next": "/mobility",
        },
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"login failed: {response.status_code}")
    return int(db.get_user_by_email(email)["id"])


def main() -> int:
    print("\nMetric catalogue")
    check("twelve metrics defined", len(mobility.METRICS) == 12, str(len(mobility.METRICS)))
    check("all keys unique", len(mobility.METRICS_BY_KEY) == 12)
    check("every metric has a unit", all(m.unit for m in mobility.METRICS))
    check("every metric is described", all(len(m.description) > 40 for m in mobility.METRICS))
    check("every range is ordered", all(m.high > m.low for m in mobility.METRICS))
    check("db columns match metric keys", set(db.MOBILITY_COLUMNS) == set(mobility.METRICS_BY_KEY))
    check(
        "eight metrics are watch-only",
        len(mobility.WATCH_ONLY_KEYS) == 8,
        str(len(mobility.WATCH_ONLY_KEYS)),
    )
    for key in (
        "cardio_fitness",
        "ground_contact_time",
        "running_stride_length",
        "six_minute_walk",
        "stair_speed_up",
        "stair_speed_down",
        "vertical_oscillation",
        "walking_steadiness",
    ):
        check(f"{key} flagged watch-only", key in mobility.WATCH_ONLY_KEYS)

    print("\nDirection handling")
    speed = mobility.METRICS_BY_KEY["walking_speed"]
    asymmetry = mobility.METRICS_BY_KEY["walking_asymmetry"]
    check("faster walking scores higher", mobility.normalise(speed, 1.8) > mobility.normalise(speed, 0.6))
    check("less asymmetry scores higher", mobility.normalise(asymmetry, 1.0) > mobility.normalise(asymmetry, 18.0))
    check("bottom of range is zero", round(mobility.normalise(speed, 0.4)) == 0)
    check("top of range is 100", round(mobility.normalise(speed, 2.0)) == 100)
    check("above range clamps to 100", round(mobility.normalise(speed, 99.0)) == 100)
    check("below range clamps to zero", round(mobility.normalise(speed, -5.0)) == 0)

    print("\nScoring")
    check("no readings scores None", mobility.score_values({}) is None)
    best = mobility.score_values(
        {m.key: (m.high if m.higher_is_better else m.low) for m in mobility.METRICS}
    )
    worst = mobility.score_values(
        {m.key: (m.low if m.higher_is_better else m.high) for m in mobility.METRICS}
    )
    check("all best values score 100", best == 100.0, str(best))
    check("all worst values score zero", worst == 0.0, str(worst))
    partial = mobility.score_values({"walking_speed": 1.2, "six_minute_walk": 500.0})
    check("partial entry still scores", partial is not None)
    check("partial score stays in range", 0 <= partial <= 100, str(partial))
    check(
        "unknown keys ignored",
        mobility.score_values({"walking_speed": 1.2, "bogus": 5.0})
        == mobility.score_values({"walking_speed": 1.2}),
    )
    check(
        "score is not a raw-value average",
        mobility.score_values({"walking_speed": 1.2, "six_minute_walk": 500.0}) != (1.2 + 500.0) / 2,
    )

    print("\nForm parsing")
    values, errors = mobility.parse_values(
        {"walking_speed": "1.25", "six_minute_walk": " 480 ", "step_length": ""}
    )
    check("numeric value parsed", values.get("walking_speed") == 1.25)
    check("surrounding space tolerated", values.get("six_minute_walk") == 480.0)
    check("blank skipped rather than zeroed", "step_length" not in values)
    check("no errors raised", errors == {})

    values, errors = mobility.parse_values({"walking_speed": "abc"})
    check("non-numeric rejected", "walking_speed" in errors and not values)

    values, errors = mobility.parse_values({"walking_speed": "-3"})
    check("negative rejected", "walking_speed" in errors and not values)

    values, errors = mobility.parse_values({"walking_speed": "nan"})
    check("NaN rejected", "walking_speed" in errors and not values)

    values, errors = mobility.parse_values({"walking_speed": "inf"})
    check("infinity rejected", "walking_speed" in errors and not values)

    values, errors = mobility.parse_values({"walking_speed": "1.0", "step_length": "oops"})
    check("good value survives bad neighbour", values.get("walking_speed") == 1.0)
    check("bad neighbour reported", "step_length" in errors)

    print("\nSub-scores")
    subs = mobility.sub_scores({"walking_speed": 1.3, "six_minute_walk": 500.0})
    check("one entry per reading", len(subs) == 2, str(subs))
    check("sub-scores in range", all(0 <= v <= 100 for v in subs.values()), str(subs))
    check("known metric scored correctly", subs["walking_speed"] == round(mobility.normalise(speed, 1.3), 1))
    check("empty input gives empty result", mobility.sub_scores({}) == {})
    check("unknown keys ignored", mobility.sub_scores({"bogus": 5.0}) == {})
    check(
        "sub-scores agree with the composite",
        round(sum(subs.values()) / len(subs), 1) == mobility.score_values({"walking_speed": 1.3, "six_minute_walk": 500.0}),
    )

    print("\nScore bands")
    check("five bands defined", len(mobility.SCORE_BANDS) == 5)
    check("bands start at 0", mobility.SCORE_BANDS[0].floor == 0)
    check("bands end at 100", mobility.SCORE_BANDS[-1].ceiling == 100)
    check(
        "bands are contiguous with no gaps or overlaps",
        all(
            mobility.SCORE_BANDS[i].ceiling + 1 == mobility.SCORE_BANDS[i + 1].floor
            for i in range(len(mobility.SCORE_BANDS) - 1)
        ),
    )
    check("every band is labelled", all(b.label for b in mobility.SCORE_BANDS))
    check("every band explains itself", all(len(b.summary) > 30 for b in mobility.SCORE_BANDS))
    check("caveat mentions not clinical", "not a clinical rating" in mobility.SCORE_CAVEAT)

    print("\nBand lookup")
    check("no score gives no band", mobility.score_band(None) is None)
    check("zero lands in the lowest band", mobility.score_band(0).label == "Very limited")
    check("fifty is mid-range", mobility.score_band(50).label == "Mid-range")
    check("one hundred is the top band", mobility.score_band(100).label == "Near the top")
    check("every whole score resolves", all(mobility.score_band(s) is not None for s in range(0, 101)))
    check("out-of-range high clamps to top", mobility.score_band(150).label == "Near the top")

    with TestClient(app, follow_redirects=False) as client:
        print("\nPage is protected")
        anonymous = client.get("/mobility")
        check("anonymous user is redirected", anonymous.status_code in (302, 303, 401))

        user_id = sign_in(client, "mobility@example.com", "Mobility Tester")
        check("signed in", user_id > 0)

        print("\nSigned-in form")
        page = client.get("/mobility")
        check("page renders", page.status_code == 200)
        body = page.text
        for label in ("Walking speed", "Cardio fitness", "Walking steadiness", "Vertical oscillation"):
            check(f"describes '{label}'", label in body)
        check("explains the Health app path", "Health app" in body and "Mobility" in body)
        check("shows units", "m/s" in body and "mL/(kg·min)" in body)
        check("marks watch metrics", "Apple Watch" in body)
        check("carries the medical disclaimer", "not a medical device" in body)
        check("scoring explainer removed", "sub-score" not in body.lower())
        check("summary explainer removed", "What your mobility summary means" not in body)
        check("no score bands shown", all(b.label not in body for b in mobility.SCORE_BANDS))
        check("says what is recorded", "record only the measurements" in body)

        print("\nStorage")
        from datetime import date as _date

        expected_today = _date.today().isoformat()
        page = client.get("/mobility")
        check(
            "date input defaults to the user's local day",
            f'value="{expected_today}"' in page.text,
        )

        reading_id = db.save_mobility_reading(user_id, "2026-03-01", {"walking_speed": 1.3}, 42.0)
        check("reading inserted", reading_id > 0)
        rows = db.get_mobility_readings(user_id)
        check("reading listed", len(rows) == 1)
        check("value stored", rows[0]["walking_speed"] == 1.3)
        check("unfilled metrics stay null", rows[0]["cardio_fitness"] is None)
        check("score stored", rows[0]["score"] == 42.0)
        check("date stored", rows[0]["recorded_on"] == "2026-03-01")

        db.save_mobility_reading(user_id, "2026-02-01", {"walking_speed": 1.1}, 35.0)
        rows = db.get_mobility_readings(user_id)
        check("two readings listed", len(rows) == 2)
        check("newest first", rows[0]["recorded_on"] == "2026-03-01", rows[0]["recorded_on"])
        check("latest helper agrees", db.get_latest_mobility_reading(user_id)["recorded_on"] == "2026-03-01")

        print("\nValidation through the route")
        response = client.post(
            "/mobility",
            data={"csrf_token": csrf(client, "/mobility"), "walking_speed": "not-a-number"},
        )
        check("invalid input re-renders", response.status_code == 200)
        check("validation error shown", "Enter a number." in response.text)
        check("nothing stored", len(db.get_mobility_readings(user_id)) == 2)

        response = client.post("/mobility", data={"csrf_token": csrf(client, "/mobility")})
        check("empty form rejected", response.status_code == 200)
        check("empty message shown", "at least one measurement" in response.text)

        print("\nMixed valid and invalid fields do not crash")
        response = client.post(
            "/mobility",
            data={
                "csrf_token": csrf(client, "/mobility"),
                "walking_speed": "1.3",
                "step_length": "oops",
            },
        )
        check("page still renders", response.status_code == 200, str(response.status_code))
        check("the bad field is flagged", "Enter a number." in response.text)
        check("the good value is kept", "1.3" in response.text)
        check("no sub-score displayed", "Sub-score" not in response.text)
        check("nothing stored from a rejected form", len(db.get_mobility_readings(user_id)) == 2)

        print("\nCSRF protection")
        response = client.post("/mobility", data={"walking_speed": "1.4", "csrf_token": "forged"})
        check("forged token rejected", "session expired" in response.text)
        check("nothing stored after forgery", len(db.get_mobility_readings(user_id)) == 2)

        print("\nValid submission")
        response = client.post(
            "/mobility",
            data={
                "csrf_token": csrf(client, "/mobility"),
                "walking_speed": "1.35",
                "six_minute_walk": "520",
                "cardio_fitness": "44",
                "walking_steadiness": "82",
                "recorded_on": "2026-04-15",
            },
        )
        check("redirects after save", response.status_code in (302, 303), str(response.status_code))
        rows = db.get_mobility_readings(user_id)
        check("third reading stored", len(rows) == 3)
        newest = rows[0]
        check("multiple metrics saved", newest["six_minute_walk"] == 520.0)
        check("date honoured", newest["recorded_on"] == "2026-04-15")
        expected = mobility.score_values(
            {
                "walking_speed": 1.35,
                "six_minute_walk": 520.0,
                "cardio_fitness": 44.0,
                "walking_steadiness": 82.0,
            }
        )
        check("score persisted", newest["score"] == expected, f"{newest['score']} vs {expected}")
        check("score stays in range", 0 <= newest["score"] <= 100)

        print("\nDate defaults to today when omitted")
        client.post(
            "/mobility",
            data={"csrf_token": csrf(client, "/mobility"), "walking_speed": "1.2"},
        )
        rows = db.get_mobility_readings(user_id)
        check("saved without a date", rows[0]["recorded_on"] == _date.today().isoformat(), rows[0]["recorded_on"])

        print("\nThe page no longer shows a score or band")
        body = client.get("/mobility").text
        check("no score echoed", "score-value" not in body)
        check("no current band highlight", "is-current" not in body)
        check("no summary explainer", "What your mobility summary means" not in body)
        check("history has no score column", "<th scope=\"col\">Score</th>" not in body)

        confirm = client.get("/mobility?saved=1")
        check("success banner after redirect", "Reading saved." in confirm.text)
        check("history rendered", "Past readings" in confirm.text)
        check("older reading listed", "2026-02-01" in confirm.text)

        print("\nReadings stay private per user")
        client.cookies.clear()
        other_id = sign_in(client, "someone.else@example.com", "Someone Else")
        check("new user starts empty", db.get_mobility_readings(other_id) == [])
        page = client.get("/mobility")
        check("new user sees no history", "Past readings" not in page.text)
        check("new user sees no saved score", "2026-03-01" not in page.text)

    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("Mobility checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())