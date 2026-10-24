"""Checks dashboard chart geometry and the dashboard's empty-state guidance.

Usage:  .venv\\Scripts\\python.exe -m tests.test_dashboard
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_dashboard_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "dashboard-test-key"
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import charts, db, mobility, security  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "DashboardTrend2026"
failures: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


class Row(dict):
    """Minimal stand-in for a sqlite3.Row, which charts only reads by key."""


def series_of(values: list[tuple[str, float]]) -> list[charts.Point]:
    """Build a series from (ISO datetime, value) pairs."""
    return [
        charts.Point(
            when=charts.datetime.fromisoformat(stamp),
            value=value,
            label=stamp[:10],
        )
        for stamp, value in values
    ]


def squash(text: str) -> str:
    """Collapse whitespace so wrapped template copy can be matched as one string."""
    return re.sub(r"\s+", " ", text)


def sign_in(client: TestClient, email: str, display_name: str) -> int:
    db.create_user(email, display_name, security.hash_password(PASSWORD), email_verified=True)
    response = client.post(
        "/login",
        data={
            "csrf_token": client.get("/login").text.split('name="csrf_token" value="')[1].split('"')[0],
            "email": email,
            "password": PASSWORD,
            "next": "/dashboard",
        },
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"login failed: {response.status_code}")
    return int(db.get_user_by_email(email)["id"])


def main() -> int:
    print("\nDate parsing")
    check("ISO date parsed", charts.parse_date("2026-04-15") == date(2026, 4, 15))
    check("datetime string parsed", charts.parse_date("2026-04-15T10:30:00") == date(2026, 4, 15))
    check("datetime object parsed", charts.parse_date(charts.datetime(2026, 4, 15, 8, 0)) == date(2026, 4, 15))
    check("date object passed through", charts.parse_date(date(2026, 4, 15)) == date(2026, 4, 15))
    check("garbage rejected", charts.parse_date("not-a-date") is None)
    check("empty rejected", charts.parse_date("") is None)
    check("None rejected", charts.parse_date(None) is None)

    print("\nSeries building")
    rows = [
        Row(recorded_on="2026-03-01", created_at="2026-03-01T09:00:00+00:00", score=70.0),
        Row(recorded_on="2026-01-01", created_at="2026-01-01T09:00:00+00:00", score=50.0),
        Row(recorded_on="2026-02-01", created_at="2026-02-01T09:00:00+00:00", score=None),
        Row(recorded_on="2026-04-01", created_at="2026-04-01T09:00:00+00:00", score=90.0),
        Row(recorded_on="bad-date", created_at="", score=99.0),
    ]
    built = charts.build_series(rows, "score")
    check("null values skipped", len(built) == 3, str(len(built)))
    check("bad dates skipped", [p.value for p in built] == [50.0, 70.0, 90.0])
    check("oldest first", built[0].when.date() == date(2026, 1, 1))
    check(
        "timestamps ascend",
        all(built[i].when <= built[i + 1].when for i in range(len(built) - 1)),
    )

    print("\nOrdering is chronological, not database order")
    scrambled = [
        Row(recorded_on="2026-03-01", created_at="2026-03-01T09:00:00+00:00", score=70.0),
        Row(recorded_on="2026-01-01", created_at="2026-01-01T09:00:00+00:00", score=50.0),
        Row(recorded_on="2026-02-01", created_at="2026-02-01T09:00:00+00:00", score=60.0),
    ]
    ordered = charts.build_series(scrambled, "score")
    check("sorted oldest to newest", [p.value for p in ordered] == [50.0, 60.0, 70.0])

    print("\nRegression: same-day readings must not reverse")
    same_day_rows = [
        Row(recorded_on="2026-05-20", created_at="2026-05-20T18:00:00+00:00", score=72.0),
        Row(recorded_on="2026-05-20", created_at="2026-05-20T08:00:00+00:00", score=61.0),
        Row(recorded_on="2026-05-20", created_at="2026-05-20T13:00:00+00:00", score=66.0),
    ]
    same_day = charts.build_series(same_day_rows, "score")
    check("same-day values ascend", [p.value for p in same_day] == [61.0, 66.0, 72.0])
    chart = charts.line_chart(same_day)
    xs = [float(dot["cx"]) for dot in chart["dots"]]
    ys = [float(dot["cy"]) for dot in chart["dots"]]
    check("leftmost point is the oldest", xs[0] < xs[-1], str(xs))
    check("time of day positions them", xs == sorted(xs))
    check("lowest score sits leftmost", ys[0] > ys[-1], str(ys))

    print("\nIdentical timestamps fall back to input order")
    identical = [
        Row(recorded_on="2026-05-20", created_at="2026-05-20T09:00:00+00:00", score=20.0),
        Row(recorded_on="2026-05-20", created_at="2026-05-20T09:00:00+00:00", score=80.0),
    ]
    tied = charts.build_series(identical, "score")
    check("tie-break keeps input order", [p.value for p in tied] == [20.0, 80.0])

    print("\nRows without created_at still order by recorded_on")
    legacy = [
        Row(recorded_on="2026-03-01", score=70.0),
        Row(recorded_on="2026-01-01", score=50.0),
    ]
    fallback = charts.build_series(legacy, "score")
    check("fallback sorts by date", [p.value for p in fallback] == [50.0, 70.0])

    print("\nEmpty chart")
    check("no series gives None", charts.line_chart([]) is None)

    print("\nSingle reading")
    single = charts.line_chart(series_of([("2026-04-15", 62.0)]))
    check("chart built", single is not None)
    check("flagged as single point", single["single"] is True)
    check("one dot drawn", len(single["dots"]) == 1)

    print("\nMultiple readings")
    chart = charts.line_chart(series_of([("2026-01-01", 40.0), ("2026-02-01", 55.0), ("2026-03-01", 50.0)]))
    check("chart built", chart is not None)
    check("not flagged single", chart["single"] is False)
    check("three dots", len(chart["dots"]) == 3)
    check("line has three points", chart["line_points"].count(" ") == 2)
    check("grid rows generated", len(chart["grid"]) == 5)
    check("axis labels bound dates", chart["x_first"].startswith("01 Jan 2026") and chart["x_last"].startswith("01 Mar 2026"), f"{chart['x_first']} / {chart['x_last']}")

    xs = [float(dot["cx"]) for dot in chart["dots"]]
    ys = [float(dot["cy"]) for dot in chart["dots"]]
    check("x increases left to right", xs == sorted(xs))
    check("higher value sits higher on screen", ys[1] < ys[0])
    check("points stay inside the frame", all(chart["pad_left"] <= x <= chart["width"] for x in xs))
    check("points stay inside vertically", all(0 <= y <= chart["height"] for y in ys))

    print("\nIrregular gaps are not flattened")
    gapped = charts.line_chart(series_of([("2026-01-01", 40.0), ("2026-02-01", 50.0), ("2026-03-11", 60.0)]))
    gaps = [float(dot["cx"]) for dot in gapped["dots"]]
    check("dates are not equally spaced", abs((gaps[1] - gaps[0]) - (gaps[2] - gaps[1])) > 1.0)

    print("\nPinned axis for the score")
    pinned = charts.line_chart(series_of([("2026-01-01", 78.0), ("2026-02-01", 81.0)]), y_min=0, y_max=100)
    labels = [line["label"] for line in pinned["grid"]]
    check("axis runs 0 to 100", labels[0] == "100" and labels[-1] == "0", str(labels))
    check("labels evenly spaced", labels == ["100", "75", "50", "25", "0"], str(labels))

    print("\nFlat series does not divide by zero")
    flat = charts.line_chart(series_of([("2026-01-01", 50.0), ("2026-02-01", 50.0), ("2026-03-01", 50.0)]))
    check("chart built", flat is not None)
    ys = {float(dot["cy"]) for dot in flat["dots"]}
    check("flat line is horizontal", len(ys) == 1)

    print("\nSame-day readings")
    same = charts.line_chart(series_of([("2026-01-01T08:00", 30.0), ("2026-01-01T20:00", 60.0)]))
    check("chart built", same is not None)
    check("axis caption includes the time", "08:00" in same["x_first"] and "20:00" in same["x_last"])
    xs = [float(dot["cx"]) for dot in same["dots"]]
    check("spread apart rather than stacked", xs[0] != xs[1])
    check("earlier time is leftmost", xs[0] < xs[1], str(xs))

    print("\nIdentical instants are spaced evenly")
    twin = charts.line_chart(series_of([("2026-01-01T09:00", 30.0), ("2026-01-01T09:00", 60.0)]))
    check("shared instant flagged", twin["shared_instant"] is True)
    xs = [float(dot["cx"]) for dot in twin["dots"]]
    check("points separated", xs[0] != xs[1], str(xs))

    print("\nOut-of-range values are clamped to the frame")
    outlying = charts.line_chart(series_of([("2026-01-01", 50.0), ("2026-02-01", 5000.0)]), y_min=0, y_max=100)
    ys = [float(dot["cy"]) for dot in outlying["dots"]]
    check("outlier stays inside the plot", all(0 <= y <= outlying["height"] for y in ys))
    check("outlier marker is fully visible", ys[-1] >= outlying["pad_top"] + 3.5)

    print("\nTrend wording")
    check("no trend without data", charts.trend_note([]) == "")
    check("single reading explained", "no trend" in charts.trend_note(series_of([("2026-01-01", 50.0)])))
    rising = charts.trend_note(series_of([("2026-01-01", 50.0), ("2026-02-01", 62.0)]))
    check("rise described", "12.0 points up" in rising, rising)
    falling = charts.trend_note(series_of([("2026-01-01", 70.0), ("2026-02-01", 55.0)]))
    check("fall described", "15.0 points down" in falling, falling)
    check("flat described", "Flat" in charts.trend_note(series_of([("2026-01-01", 50.0), ("2026-02-01", 50.0)])))

    with TestClient(app, follow_redirects=False) as client:
        print("\nDashboard is protected")
        check("anonymous redirected", client.get("/dashboard").status_code in (302, 303, 401))

        user_id = sign_in(client, "trend@example.com", "Trend Tester")

        print("\nEmpty dashboard")
        page = client.get("/dashboard")
        check("renders", page.status_code == 200)
        body = page.text
        check("prompts to use the mobility tab", "Go to" in body and "My Mobility" in body)
        check("prompt links to mobility", 'href="/mobility"' in body)
        check("prompt mentions the button", "Go to My Mobility" in body)
        check("explains the Health app path", "Health app" in body and "Browse" in body)
        check("shows no readings yet", "No readings recorded yet" in body)
        check("no chart when empty", "chart__svg" not in body)
        check("score summary removed from empty state", "Mobility summary" not in body)
        check("score bands removed", "What your mobility summary means" not in body)
        check("no band highlighting", "is-current" not in body)
        check("old placeholder gone", "Connect a device" not in body)
        check("disclaimer kept", "not a medical device" in body)

        print("\nDashboard with a single reading")
        db.save_mobility_reading(
            user_id,
            "2026-04-01",
            {"walking_speed": 1.2, "walking_steadiness": 70.0},
            mobility.score_values({"walking_speed": 1.2, "walking_steadiness": 70.0}),
        )
        body = client.get("/dashboard").text
        check("chart drawn", "chart__svg" in body)
        check("single reading shows a dot, not a line", "chart__dot" in body and "chart__line" not in body)
        check("prompt gone once data exists", "Go to My Mobility" not in body)
        check("latest readings listed", "Latest readings" in body)
        check("metric card shows value", "Walking steadiness" in body and "70" in body)
        check("sub-score removed from tiles", "Sub-score" not in body)
        check("one reading counted", "1 reading" in body)
        check("no trend claim yet", "no trend to compare" in body, "")

        print("\nDashboard with several readings")
        # Entered newest-first, and each saved later than the date it is dated, so this
        # only plots left-to-right correctly if the measurement date drives the order.
        for day, speed in (("2026-03-01", 1.0), ("2026-02-01", 1.05), ("2026-01-01", 0.95)):
            score = mobility.score_values({"walking_speed": speed, "walking_steadiness": 70.0})
            db.save_mobility_reading(user_id, day, {"walking_speed": speed, "walking_steadiness": 70.0}, score)

        body = client.get("/dashboard").text
        check("chart drawn", "chart__svg" in body)
        check("trend described", "points up" in body)
        check("reading count plural", "4 readings" in body)
        check("axis spans the range", "01 Jan 2026 to 01 Apr 2026" in body)
        check("one dot per reading", body.count('class="chart__dot"') == 4, str(body.count('class="chart__dot"')))
        check("caveat about gaps", "not that nothing changed" in body)
        check("prompt still reachable in footer", "View metrics" in body)
        check("guidance to use the mobility tab shown with data", "My Mobility" in body and "extend this chart" in body)
        check("guidance links to mobility", 'href="/mobility"' in body)

        print("\nScore headline and bands are gone")
        body = client.get("/dashboard").text
        check("no score value headline", "score-value" not in body)
        check("no mobility summary copy", "Mobility summary" not in body)
        check("no summary bands", "What your mobility summary means" not in body)
        check("no current band highlight", "is-current" not in body)
        check("latest reading still dated", "Measured 2026-04-01" in body)
        check("trend still labelled", "Your Mobility Trend" in body)

        print("\nChart reads oldest to newest")
        body = client.get("/dashboard").text
        check("one dot per reading", body.count('class="chart__dot"') == 4, str(body.count('class="chart__dot"')))
        xs = [
            float(cx)
            for cx in __import__("re").findall(r'class="chart__dot" cx="([\d.]+)"', body)
        ]
        check("dot x positions increase left to right", xs == sorted(xs), str(xs))
        check("axis starts at the oldest", "01 Jan 2026" in body)
        check("axis ends at the newest", "01 Apr 2026" in body)

        print("\nChart markup safety")
        check("svg carries a role", 'role="img"' in body)
        check("svg has an aria-label", "aria-label=" in body)
        # The page ships an external script for hover readouts; what must never appear is an
        # inline <script> without a src, which is the shape an injected payload takes.
        check("no inline script injected", re.search(r"<script(?![^>]*\bsrc=)", body, re.I) is None)

        print("\nStatic chart and hover readout")
        check("score chart has no zoom controls", "data-zoomable" not in body)
        check("chart.js loaded", "/static/js/chart.js" in body)
        check("chart.js served", client.get("/static/js/chart.js").status_code == 200)
        check("points carry hover data", "data-value=" in body and "data-date=" in body)
        check("tooltip labelled with the score", 'data-tooltip-prefix="Mobility score"' in body)
        chart_script = client.get("/static/js/chart.js").text
        check("chart script has no zoom behavior", "makeZoomable" not in chart_script and "addEventListener(\"wheel\"" not in chart_script)

        print("\nUsers only see their own trend")
        client.cookies.clear()
        other_id = sign_in(client, "empty@example.com", "Empty User")
        check("second user has no readings", db.get_mobility_readings(other_id) == [])
        other_body = client.get("/dashboard").text
        check("second user sees the prompt", "Go to My Mobility" in other_body)
        check("second user sees no chart", "chart__svg" not in other_body)
        check("second user is told to use the mobility tab", "My Mobility" in other_body)
        check("first user's dates hidden", "2026-04-01" not in other_body)

        print("\nApplication shell replaces marketing navigation")
        client.cookies.clear()
        signed_in = sign_in(client, "shell@example.com", "Shell User")
        body = client.get("/dashboard").text
        check("application nav present", 'aria-label="Application"' in body)
        check("marketing header gone", "View My Mobility" not in body)
        check("footer marketing nav gone", 'aria-label="Footer"' not in body)
        for label in ["Overview", "My Mobility", "Trends", "Learn", "Account", "Sign Out"]:
            check(f"nav has {label}", f">{label}<" in body)
        check("current page marked", 'aria-current="page"' in body)
        # Trends is a section of the overview and Learn leaves the app shell, so neither
        # may claim to be a current page.
        check("only one nav item marked current", body.count('aria-current="page"') == 1)
        check("trends not marked as a page", "Trends</a>" in body and 'href="/dashboard#trend">Trends</a>' in body)
        check("learn not marked as a page", 'href="/movement">Learn</a>' in body)
        check("sign out posts a csrf token", 'name="csrf_token"' in body)
        check("mobility page uses the app shell", 'aria-label="Application"' in client.get("/mobility").text)
        check("account page uses the app shell", 'aria-label="Application"' in client.get("/account").text)

        print("\nEvery app page centres its panels in the same column")
        # All three signed-in pages must be wrapped in `.app-shell`, the flex column that
        # centres each panel. A bare panel under `.app-main` (display:block) would sit hard
        # against the left edge - the bug this guards against.
        for path in ["/dashboard", "/mobility", "/account"]:
            page = client.get(path).text
            check(f"{path} wrapped in app-shell", page.count('<div class="app-shell">') == 1)

        print("\nGreeting and trend wording stay non-clinical")
        check("greeting names the user", re.search(r"Good (morning|afternoon|evening), Shell User", body) is not None)
        check("trend heading present", "Your Mobility Trend" in body)
        check("no mobility summary copy", "Mobility summary" not in body)
        check("no bare 'Mobility score' headline", ">Mobility score<" not in body)
        check("no clinical rating claim", "not a clinical rating" not in body)
        check("disclaimer shown", "does\n          not diagnose" in body or "does not diagnose" in squash(body))
        check("doctor conversation card", "Notice a meaningful change?" in body)
        check("prepare for a doctor visit action", "Prepare for a doctor visit" in body)
        check("no diagnostic wording", "diagnose you" not in squash(body).lower())

        print("\nMetric tiles compare against the previous reading")
        db.save_mobility_reading(signed_in, "2026-01-01", {"walking_speed": 1.0, "walking_steadiness": 70.0}, 40.0)
        db.save_mobility_reading(signed_in, "2026-02-01", {"walking_speed": 1.3, "walking_steadiness": 80.0}, 55.0)
        body = client.get("/dashboard").text
        flat = squash(body)
        check("rise described as a rise", "30.0% vs previous" in flat, flat[flat.find("vs previous") - 60 : flat.find("vs previous") + 40])
        check("arrow accompanies the change", "trend--up" in body)
        check("sub-score removed", "Sub-score" not in body)
        check("arrow direction explained", "does not mean better or worse" in flat)

        print("\nPrint sheet gives a usable paper summary")
        body = client.get("/dashboard").text
        sheet = client.get("/static/css/style.css").text
        check("print-only document header", "print-only__title" in body and "MotorScope Mobility Report" in body)
        check("print-only identifies the reader", "Shell User" in body and "print-only__meta" in body)
        check("print header is hidden on screen", ".print-only { display: none" in sheet)

        # The first rule inside @media print is the list of selectors dropped from paper.
        start = sheet.index("@media print {")
        head = sheet[start : sheet.index("display: none !important;", start)]
        dropped = set(re.findall(r"\.[a-z][\w-]*", head))
        check("print hidden-list parsed", bool(dropped), str(dropped))
        for chrome in [".app-bar", ".site-header", ".site-footer", ".btn", ".panel--callout", ".trend-actions"]:
            check(f"print drops {chrome}", chrome in dropped)
        # Measurement content must survive onto paper.
        for kept in [".tile", ".chart__svg", ".score-scale", ".dash-grid", ".panel", ".disclaimer"]:
            check(f"print keeps {kept.lstrip('.')}", kept not in dropped)
        check("print forces ink colours", "print-color-adjust: exact" in sheet)
        check("print chart drawn in black", "fill: #000 !important" in sheet)
        check("page margins set", "@page" in sheet)

        print("\nSingle reading has nothing to compare against")
        client.cookies.clear()
        solo_id = sign_in(client, "solo@example.com", "Solo User")
        db.save_mobility_reading(solo_id, "2026-03-01", {"walking_speed": 1.1}, 50.0)
        solo_body = client.get("/dashboard").text
        check("no fabricated percentage", "vs previous" not in solo_body)
        check("no arrow direction", "trend--" not in solo_body)

        print("\nMobile and account pages share the shell")
        check("mobility has no footer marketing nav", 'aria-label="Footer"' not in client.get("/mobility").text)

    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("Dashboard chart checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())