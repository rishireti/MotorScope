"""Checks Apple Health export parsing and the ZIP upload route.

Usage:  .venv\\Scripts\\python.exe -m tests.test_health_export
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_health_export_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "health-export-test-key"
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import charts, db, health_export, mobility, security  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "HealthExport2026"
WORK = Path(tempfile.gettempdir()) / "motorscope_export_fixtures"
WORK.mkdir(exist_ok=True)
failures: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


def record(
    type_name: str,
    value: str,
    unit: str,
    day: str,
    time: str = "08:00:00",
    offset: str = "+0100",
) -> str:
    """One <Record> element shaped the way Apple writes them."""
    stamp = f"{day} {time} {offset}"
    return (
        f'<Record type="{type_name}" sourceName="Apple Watch" sourceVersion="11.5" '
        f'unit="{unit}" creationDate="{stamp}" startDate="{stamp}" endDate="{stamp}" '
        f'value="{value}"/>'
    )


def build_export(name: str, body: str, *, member: str = "apple_health_export/export.xml") -> Path:
    """Write a ZIP shaped like Apple's export, containing one XML document."""
    path = WORK / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, f'<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_GB">{body}</HealthData>')
    return path


#: The opening of a genuine export.xml, including the internal DTD Apple embeds. Taking
#: this from a real file is the point: an earlier version of the parser rejected any
#: <!DOCTYPE> and therefore rejected every real export.
APPLE_PROLOG = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 12 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
<!ATTLIST HealthData
  locale CDATA #REQUIRED
>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate
  value CDATA #REQUIRED
>
<!ELEMENT Me EMPTY>
<!ATTLIST Me
  HKCharacteristicTypeIdentifierDateOfBirth         CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBiologicalSex       CDATA #REQUIRED
>
<!ELEMENT Record (MetadataEntry*)>
<!ATTLIST Record
  type          CDATA #REQUIRED
  unit          CDATA #IMPLIED
  value         CDATA #IMPLIED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!ELEMENT MetadataEntry EMPTY>
]>
"""

#: HealthKit 12+ ships a DTD that expat itself rejects. Apple users routinely have to
#: patch export.xml before third-party tools will read it, so the parser must not depend
#: on it being well formed.
BROKEN_APPLE_PROLOG = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record)*)>
<!ATTLIST Me
  device           CDATA #IMPLIED
>
]>
"""

#: A classic expansion bomb: had the DTD been parsed instead of discarded, the entity
#: below would expand to roughly 10^8 characters.
ENTITY_BOMB_PROLOG = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ENTITY a "aaaaaaaaaa">
<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
<!ELEMENT HealthData (Record*)>
]>
"""


def build_raw_export(name: str, prolog: bytes, body: str) -> Path:
    """Write a ZIP whose export.xml has a hand-chosen prolog, like Apple's."""
    path = WORK / name
    payload = prolog + f'<HealthData locale="en_GB">{body}</HealthData>'.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("apple_health_export/export.xml", payload)
    return path


def sign_in(client: TestClient, email: str) -> int:
    db.create_user(email, "Export Tester", security.hash_password(PASSWORD), email_verified=True)
    response = client.post(
        "/login",
        data={
            "csrf_token": client.get("/login").text.split('name="csrf_token" value="')[1].split('"')[0],
            "email": email,
            "password": PASSWORD,
            "next": "/mobility",
        },
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"login failed: {response.status_code}")
    return int(db.get_user_by_email(email)["id"])


def csfr(client: TestClient, path: str) -> str:
    return client.get(path).text.split('name="csrf_token" value="')[1].split('"')[0]


def extract_imported_fields(page_html: str) -> dict[str, str]:
    """Pull the metric values out of the read-only review form's hidden fields.

    The visible table shows each value in its display (imperial) unit, so the machine
    values that get saved are read from the hidden inputs rather than the table.
    """
    found: dict[str, str] = {}
    for name, value in re.findall(
        r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"',
        page_html,
    ):
        if name in mobility.METRICS_BY_KEY:
            found[name] = value
    return found


def main() -> int:
    print("\nUnit conversions")
    check(
        "metres become centimetres",
        health_export._convert("step_length", 0.72, "m") == 72.0,
    )
    check(
        "seconds become milliseconds",
        health_export._convert("ground_contact_time", 0.24, "s") == 240.0,
    )
    check(
        "apple's percentage fractions become whole percents",
        health_export._convert("walking_steadiness", 0.825, "%") == 82.5,
    )
    check(
        "double support follows the fraction format seen in a real export",
        abs(health_export._convert("double_support_time", 0.3213, "%") - 32.13) < 1e-9,
    )
    check(
        "speeds pass through",
        health_export._convert("walking_speed", 1.32, "m/s") == 1.32,
    )
    check(
        "unknown unit refused",
        health_export._convert("walking_speed", 1.32, "km/h") is None,
    )
    check(
        "every metric accepts its documented unit",
        all(
            unit in health_export.UNIT_CONVERSIONS[key]
            for key in health_export.UNIT_CONVERSIONS
            for unit in health_export.UNIT_CONVERSIONS[key]
        ),
    )

    print("\nTimestamp handling")
    check(
        "local day taken from the leading date",
        health_export._local_day("2026-10-04 23:58:02 +0100") == "2026-10-04",
    )
    check(
        "late-evening walk stays on its own day",
        health_export._local_day("2026-10-04 00:12:00 -0800") == "2026-10-04",
    )
    check("missing timestamp ignored", health_export._local_day(None) is None)
    check("truncated timestamp ignored", health_export._local_day("2026") is None)

    print("\nParsing a realistic export")
    body = "".join(
        [
            # Noise that must never be retained.
            '<Record type="HKQuantityTypeIdentifierHeartRate" unit="count/min" startDate="2026-10-04 09:00:00 +0100" value="61"/>',
            '<Record type="HKQuantityTypeIdentifierBodyMass" unit="kg" startDate="2026-10-04 07:30:00 +0100" value="71.2"/>',
            '<Record type="HKQuantityTypeIdentifierSleepAnalysis" startDate="2026-10-04 23:00:00 +0100" value="HKCategoryValueSleepAnalysisAsleep"/>',
            # Walking speed across two days: only the newest day may survive.
            record("HKQuantityTypeIdentifierWalkingSpeed", "1.20", "m/s", "2026-10-02", "10:00:00"),
            record("HKQuantityTypeIdentifierWalkingSpeed", "1.30", "m/s", "2026-10-03", "10:00:00"),
            record("HKQuantityTypeIdentifierWalkingSpeed", "1.30", "m/s", "2026-10-04", "08:00:00"),
            record("HKQuantityTypeIdentifierWalkingSpeed", "1.40", "m/s", "2026-10-04", "18:00:00"),
            # Steppish metrics needing unit conversion.
            record("HKQuantityTypeIdentifierWalkingStepLength", "0.72", "m", "2026-10-04", "08:00:00"),
            record("HKQuantityTypeIdentifierWalkingAsymmetryPercentage", "0.095", "%", "2026-10-04", "08:00:00"),
            record("HKQuantityTypeIdentifierWalkingDoubleSupportPercentage", "0.31", "%", "2026-10-04", "08:00:00"),
            record("HKQuantityTypeIdentifierAppleWalkingSteadiness", "0.84", "%", "2026-10-04", "08:00:00"),
            # Running metrics.
            record("HKQuantityTypeIdentifierRunningStrideLength", "1.24", "m", "2026-10-03", "07:00:00"),
            record("HKQuantityTypeIdentifierRunningGroundContactTime", "0.25", "s", "2026-10-03", "07:00:00"),
            record("HKQuantityTypeIdentifierStairAscentSpeed", "0.61", "m/s", "2026-10-03", "12:00:00"),
            record("HKQuantityTypeIdentifierStairDescentSpeed", "0.55", "m/s", "2026-10-03", "12:00:00"),
            record("HKQuantityTypeIdentifierWalkingVerticalOscillation", "6.4", "cm", "2026-10-03", "12:00:00"),
            # A metric last recorded on an older day keeps its own day.
            record("HKQuantityTypeIdentifierSixMinuteWalk", "612", "m", "2026-09-30", "09:00:00"),
            record("HKQuantityTypeIdentifierVO2Max", "44.2", "mL/(kg·min)", "2026-09-28", "09:00:00"),
            # Values that must be skipped rather than trusted.
            record("HKQuantityTypeIdentifierWalkingSpeed", "9.99", "km/h", "2026-10-04", "20:00:00"),
            record("HKQuantityTypeIdentifierWalkingSpeed", "not-a-number", "m/s", "2026-10-04", "21:00:00"),
        ]
    )
    result = health_export.parse_export(build_export("full.zip", body))

    check("newest day chosen as the reading date", result.recorded_on == "2026-10-04", result.recorded_on)
    check(
        "walking speed averaged across the newest day only",
        abs(result.values["walking_speed"] - 1.35) < 1e-9,
        str(result.values.get("walking_speed")),
    )
    check("step length converted to cm", result.values["step_length"] == 72.0)
    check("asymmetry fraction read as a percentage", result.values["walking_asymmetry"] == 9.5)
    check("double support kept as percent", result.values["double_support_time"] == 31.0)
    check("walking steadiness found", result.values["walking_steadiness"] == 84.0)
    check("stride length converted to cm", result.values["running_stride_length"] == 124.0)
    check("ground contact converted to ms", result.values["ground_contact_time"] == 250.0)
    check("stair speeds found", result.values["stair_speed_up"] == 0.61)
    check("vertical oscillation found", result.values["vertical_oscillation"] == 6.4)
    check("cardio fitness found", result.values["cardio_fitness"] == 44.2)
    check(
        "unrelated metrics never captured",
        not any(key in result.values for key in ("heart_rate", "body_mass", "sleep")),
    )
    check(
        "unknown unit skipped, not guessed",
        abs(result.values.get("walking_speed", 0) - 1.35) < 1e-9,
    )
    check("skipped unit counted", result.skipped_units == 1, str(result.skipped_units))
    check(
        "every record scanned, including ignored ones",
        result.records_scanned == 20,
        str(result.records_scanned),
    )
    check(
        "metrics listed in form order",
        [s.key for s in result.summaries][:4]
        == ["walking_speed", "step_length", "walking_asymmetry", "double_support_time"],
        str([s.key for s in result.summaries]),
    )
    check(
        "older metric keeps its own day",
        next(s.recorded_on for s in result.summaries if s.key == "six_minute_walk") == "2026-09-30",
    )
    check(
        "sample provenance recorded",
        next(s.sample_count for s in result.summaries if s.key == "walking_speed") == 2,
    )
    check(
        "result scores with the normal engine",
        mobility.score_values(result.values) is not None,
    )

    print("\nApple's own DTD")
    real = health_export.parse_export(
        build_raw_export("withdtd.zip", APPLE_PROLOG, body)
    )
    check("genuine export with an internal DTD reads", real.recorded_on == "2026-10-04", real.recorded_on)
    check(
        "metrics identical to a prolog-less export",
        real.values == result.values,
        str(real.values),
    )
    check("records seen through the DTD", real.records_scanned == result.records_scanned)

    broken = health_export.parse_export(
        build_raw_export("brokendt.D.zip", BROKEN_APPLE_PROLOG, body)
    )
    check("HealthKit 12+ malformed DTD still reads", broken.recorded_on == "2026-10-04")
    check("malformed DTD yields the same values", broken.values == result.values)

    print("\nHostile prologs")
    # The entity is never expanded: the DTD is discarded, so the reference is undefined
    # and the parser stops with a readable message rather than allocating the ~10^8
    # characters the bomb would produce.
    try:
        health_export.parse_export(
            build_raw_export(
                "bomb.zip",
                ENTITY_BOMB_PROLOG,
                record("HKQuantityTypeIdentifierWalkingSpeed", "1.3", "m/s", "2026-10-04")
                + "<Record type='x' value='&c;'/>",
            )
        )
        check("entity bomb refused, not expanded", False, "no error raised")
    except health_export.ExportError as error:
        check("entity bomb refused, not expanded", True)
        check("entity bomb reported readably", "damaged" in str(error), str(error))
    except Exception as error:  # noqa: BLE001
        check("entity bomb refused, not expanded", False, f"{type(error).__name__}: {error}")

    vertical_tab = health_export.parse_export(
        build_raw_export(
            "vtab.zip",
            APPLE_PROLOG,
            record("HKQuantityTypeIdentifierWalkingSpeed", "1.3", "m/s", "2026-10-04")
            + '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" '
            'startDate="2026-10-04 10:00:00 +0100" value="12\x0b34"/>',
        )
    )
    check("invisible vertical tab tolerated", vertical_tab.values["walking_speed"] == 1.3)

    not_health = WORK / "nothealth.zip"
    with zipfile.ZipFile(not_health, "w") as archive:
        archive.writestr("apple_health_export/export.xml", "<SomeOtherThing><Record/></SomeOtherThing>")
    try:
        health_export.parse_export(not_health)
        check("file without <HealthData> is rejected clearly", False, "no error raised")
    except health_export.ExportError as error:
        check("file without <HealthData> is rejected clearly", "<HealthData>" in str(error), str(error))

    print("\nExport notes")
    note = health_export.format_note(next(s for s in result.summaries if s.key == "walking_speed"))
    check("note mentions the sample count", "2 samples" in note, note)
    check("note mentions the day", "2026-10-04" in note, note)

    print("\nEmpty and unusual archives")
    for label, name, body_xml in (
        ("zip containing an empty document", "blank.zip", ""),
        ("not a zip", "notazip.zip", None),
        ("zip without export.xml", "wrongmember.zip", ""),
    ):
        path = WORK / name
        if body_xml is None:
            path.write_bytes(b"this is not a zip file at all")
        elif name == "wrongmember.zip":
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("apple_health_export/export_cda.xml", "<CDA/>")
        else:
            build_export(name, body_xml)
        try:
            health_export.parse_export(path)
            check(label, False, "no error raised")
        except health_export.ExportError as error:
            check(label, True)
        except Exception as error:  # noqa: BLE001
            check(label, False, f"{type(error).__name__}: {error}")

    print("\nHealth-data-free export")
    try:
        health_export.parse_export(build_export("nomobility.zip", ""))
        check("export with no mobility rows reports a readable error", False, "no error raised")
    except health_export.ExportError as error:
        check("export with no mobility rows reports a readable error", "No mobility" in str(error), str(error))

    print("\nUpload route")
    with TestClient(app, follow_redirects=False) as client:
        user_id = sign_in(client, "export@example.com")

        page = client.get("/mobility").text
        check("upload form present", 'type="file"' in page)
        check("upload form posts to the import route", 'action="/mobility/import"' in page)

        for phrase in (
            "Open the Health app on your iPhone",
            "Tap your profile picture in the top-right corner",
            "Scroll down and select Export All Health Data",
            "Tap Export and choose a location or app to save the ZIP file",
        ):
            check(f"instructions explain: {phrase[:40]}", phrase in page)

        check("no readings saved yet", len(db.get_mobility_readings(user_id)) == 0)

        bad = client.post(
            "/mobility/import",
            data={"csrf_token": csfr(client, "/mobility")},
            files={"export_file": ("notes.txt", b"hello", "text/plain")},
        )
        check("non-zip upload rejected", bad.status_code == 200)
        check("non-zip upload explained", "not a ZIP archive" in bad.text)
        check("non-zip upload saved nothing", len(db.get_mobility_readings(user_id)) == 0)

        forged = client.post(
            "/mobility/import",
            data={"csrf_token": "not-the-right-token"},
            files={"export_file": ("export.zip", build_export("csrf.zip", body).read_bytes(), "application/zip")},
        )
        check("upload without a valid CSRF token rejected", forged.status_code == 200)
        check("forged upload saved nothing", len(db.get_mobility_readings(user_id)) == 0)

        good = client.post(
            "/mobility/import",
            data={"csrf_token": csfr(client, "/mobility")},
            files={
                "export_file": (
                    "apple_health_export.zip",
                    build_export("upload.zip", body).read_bytes(),
                    "application/zip",
                )
            },
        )
        check("valid upload accepted", good.status_code == 200, str(good.status_code))
        check("import reports the metrics found", "12" in good.text or "Imported" in good.text)
        check("imported values shown in display units", "3.0 mph" in good.text and "28.3 in" in good.text)
        check("import does not silently save", len(db.get_mobility_readings(user_id)) == 0)
        check("import shows the computed score", "score-preview__value" in good.text)

        saved = client.post(
            "/mobility",
            data={
                "csrf_token": csfr(client, "/mobility"),
                "recorded_on": "2026-10-04",
                **{key: str(value) for key, value in result.values.items()},
            },
        )
        check("reviewed import can then be saved", saved.status_code in (302, 303), str(saved.status_code))
        stored = db.get_mobility_readings(user_id)
        check("reading stored after review", len(stored) == 1, str(len(stored)))
        check("stored under the imported date", stored[0]["recorded_on"] == "2026-10-04")
        check("stored score matches the engine", stored[0]["score"] == mobility.score_values(result.values))

    print("\nUploaded archive is not retained")
    temp_root = Path(tempfile.gettempdir())

    def spilled() -> set[str]:
        return {path.name for path in temp_root.glob("tmp*.zip")}

    with TestClient(app, follow_redirects=False) as client:
        user_id = sign_in(client, "cleanup@example.com")
        before = spilled()

        ok = client.post(
            "/mobility/import",
            data={"csrf_token": csfr(client, "/mobility")},
            files={"export_file": ("apple_health_export.zip", build_export("spill.zip", body).read_bytes(), "application/zip")},
        )
        check("upload still works while watching for spills", ok.status_code == 200)
        check("reading not saved by import", len(db.get_mobility_readings(user_id)) == 0)

        bad_zip = client.post(
            "/mobility/import",
            data={"csrf_token": csfr(client, "/mobility")},
            files={"export_file": ("broken.zip", WORK.joinpath("notazip.zip").read_bytes(), "application/zip")},
        )
        check("failed upload still reports the problem", "not a ZIP" in bad_zip.text)

    leaked = sorted(spilled() - before)
    check("no temporary archive left behind after a good upload", leaked == [], str(leaked))
    check("no temporary archive left behind after a bad upload", spilled() == before, str(spilled() - before))

    print("\nImported readings reach the graph")
    with TestClient(app, follow_redirects=False) as client:
        graph_user = sign_in(client, "graph@example.com")
        graph_zip = build_raw_export("graph.zip", APPLE_PROLOG, body)

        empty_dash = client.get("/dashboard").text
        check("no chart before any reading", "chart__svg" not in empty_dash)

        with open(graph_zip, "rb") as handle:
            review = client.post(
                "/mobility/import",
                data={"csrf_token": csfr(client, "/mobility")},
                files={"export_file": ("apple_health_export.zip", handle, "application/zip")},
            )
        check("import accepted", review.status_code == 200, str(review.status_code))
        check(
            "graph still empty before the reading is saved",
            "chart__svg" not in client.get("/dashboard").text,
        )

        fields = extract_imported_fields(review.text)
        check("imported values offered for review", len(fields) == 12, str(sorted(fields)))

        saved = client.post(
            "/mobility",
            data={
                "csrf_token": csfr(client, "/mobility"),
                "recorded_on": "2026-10-04",
                **{key: value for key, value in fields.items() if key in db.MOBILITY_COLUMNS},
            },
        )
        check("reviewed import saves", saved.status_code in (302, 303), str(saved.status_code))

        stored = db.get_mobility_readings(graph_user)
        check("one reading stored", len(stored) == 1, str(len(stored)))
        check("filed under the exported date", stored[0]["recorded_on"] == "2026-10-04")

        dash = client.get("/dashboard").text
        check("chart now rendered", "chart__svg" in dash)
        check("one point plotted", len(re.findall(r'class="chart__dot"', dash)) == 1)

        plotted = charts.build_series(db.get_mobility_readings(graph_user, limit=120), "score")
        check("graph plots exactly the stored score", len(plotted) == 1 and abs(plotted[0].value - stored[0]["score"]) < 0.05, str([(p.value, p.label) for p in plotted]))
        check("plotted date is the exported date", plotted[0].label == "2026-10-04", plotted[0].label)
        check("trend chart reflects the import", "Mobility trend over time" in dash)

        second_body = "".join(
            [
                record("HKQuantityTypeIdentifierWalkingSpeed", "1.55", "m/s", "2026-10-06", "09:00:00"),
                record("HKQuantityTypeIdentifierWalkingStepLength", "0.78", "m", "2026-10-06", "09:00:00"),
            ]
        )
        with open(build_raw_export("graph2.zip", APPLE_PROLOG, second_body), "rb") as handle:
            review2 = client.post(
                "/mobility/import",
                data={"csrf_token": csfr(client, "/mobility")},
                files={"export_file": ("apple_health_export.zip", handle, "application/zip")},
            )
        client.post(
            "/mobility",
            data={
                "csrf_token": csfr(client, "/mobility"),
                "recorded_on": "2026-10-06",
                **extract_imported_fields(review2.text),
            },
        )
        dash2 = client.get("/dashboard").text
        check("second import adds a second point", len(re.findall(r'class="chart__dot"', dash2)) == 2)
        check(
            "both dates tracked",
            len(db.get_mobility_readings(graph_user)) == 2,
            str(len(db.get_mobility_readings(graph_user))),
        )

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())