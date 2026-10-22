"""Read mobility measurements out of an Apple Health "Export All Health Data" ZIP.

Apple writes the export as a ZIP containing ``apple_health_export/export.xml``, a single
very large XML file holding one ``<Record>`` element per sample ever recorded. This module
streams that file, keeps only the sample types MotorScope scores, converts them into the
app's units, and reduces each metric to a single value.

What is deliberately NOT kept
-----------------------------
Everything else in the export. Heart rate, sleep, nutrition, clinical records and the rest
of the ZIP are skipped as they stream past. Only the twelve numbers that produce a Mobility
Score are ever held in memory, and the uploaded archive is deleted by the caller as soon as
parsing finishes.

Averaging
---------
Health records thousands of samples per metric per day. Each metric is reduced to the mean
of its samples on the most recent day that metric has data, which is the reading a person
would recognise as "how I walked today". Metrics last recorded on a different day keep that
day's average, so a rarely-updated value such as VO2 max is not thrown away just because
the export's final day happened to lack it.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError, XMLPullParser

from . import mobility

#: Position of each metric on the form, so imported values are listed in the same order
#: the user would have entered them by hand.
_DISPLAY_ORDER = {metric.key: index for index, metric in enumerate(mobility.METRICS)}

#: Name of the data member inside Apple's ZIP. Files are nested in a folder.
EXPORT_MEMBER = "export.xml"

#: Refuse absurd uploads. Apple's own guidance is that exports can reach several GB, so
#: this is generous, but it still bounds what a single request can consume.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

#: Refuse an export.xml larger than this, which guards against a decompression bomb.
MAX_XML_BYTES = 4 * 1024 * 1024 * 1024

#: A compressed-to-uncompressed ratio beyond this is not something Apple produces.
MAX_COMPRESSION_RATIO = 500

#: Read granularity while streaming. Large enough to keep syscall overhead low on a
#: multi-gigabyte file.
PROLOG_CHUNK_BYTES = 1024 * 256

#: How much of the file is held while looking for the root element. Apple's DTD runs to a
#: few kilobytes, so this leaves ample room while keeping memory fixed.
MAX_PROLOG_BYTES = 4 * 1024 * 1024

#: Matches the start of the root element. Deliberately requires "<" to be immediately
#: followed by the name, so a DTD line such as "<!ELEMENT HealthData ...>" cannot match.
_ROOT_RE = re.compile(rb"<HealthData[\s>]")


class ExportError(Exception):
    """A problem with the uploaded file that the user can do something about."""


#: HealthKit sample types that carry a number MotorScope knows what to do with.
HEALTHKIT_TYPES: dict[str, str] = {
    "HKQuantityTypeIdentifierWalkingSpeed": "walking_speed",
    "HKQuantityTypeIdentifierWalkingStepLength": "step_length",
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": "walking_asymmetry",
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": "double_support_time",
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": "walking_steadiness",
    "HKQuantityTypeIdentifierWalkingSteadiness": "walking_steadiness",
    "HKQuantityTypeIdentifierSixMinuteWalk": "six_minute_walk",
    "HKQuantityTypeIdentifierStairAscentSpeed": "stair_speed_up",
    "HKQuantityTypeIdentifierStairDescentSpeed": "stair_speed_down",
    "HKQuantityTypeIdentifierWalkingVerticalOscillation": "vertical_oscillation",
    "HKQuantityTypeIdentifierRunningGroundContactTime": "ground_contact_time",
    "HKQuantityTypeIdentifierRunningStrideLength": "running_stride_length",
    "HKQuantityTypeIdentifierVO2Max": "cardio_fitness",
}

#: Accepted HealthKit units per metric, mapped to the factor that converts them into the
#: unit the metric is displayed in. A sample in any other unit is skipped rather than
#: guessed at, so a mis-read export cannot quietly skew a score.
#:
#: The three percentage types are scaled by 100 because Apple's export.xml writes them
#: as fractions of 1 even though the unit says "%": across 63,949 double-support samples
#: in a real export every value sat between 0.207 and 0.393, i.e. 20.7-39.3%. Storing
#: them raw would file them far below their expected band and clamp every sub-score to
#: a perfect 100.
UNIT_CONVERSIONS: dict[str, dict[str, float]] = {
    "walking_speed": {"m/s": 1.0, "meter/second": 1.0, "meters/second": 1.0},
    "step_length": {"m": 100.0, "cm": 1.0, "meter": 100.0, "meters": 100.0},
    "walking_asymmetry": {"%": 100.0},
    "double_support_time": {"%": 100.0},
    "walking_steadiness": {"%": 100.0},
    "six_minute_walk": {"m": 1.0, "meter": 1.0, "meters": 1.0},
    "stair_speed_up": {"m/s": 1.0, "meter/second": 1.0, "meters/second": 1.0},
    "stair_speed_down": {"m/s": 1.0, "meter/second": 1.0, "meters/second": 1.0},
    "vertical_oscillation": {"cm": 1.0},
    "ground_contact_time": {"s": 1000.0, "ms": 1.0},
    "running_stride_length": {"m": 100.0, "cm": 1.0, "meter": 100.0, "meters": 100.0},
    "cardio_fitness": {
        "mL/(kg·min)": 1.0,
        "mL/kg/min": 1.0,
        "ml/(kg*min)": 1.0,
        "ml/kg/min": 1.0,
    },
}


@dataclass(frozen=True)
class MetricSummary:
    """One metric recovered from the export, with the day it was averaged from."""

    key: str
    value: float
    recorded_on: str
    sample_count: int


@dataclass(frozen=True)
class ImportResult:
    """Everything MotorScope needs to score an uploaded export."""

    #: The day the reading is filed under: the most recent sample of any metric.
    recorded_on: str
    values: dict[str, float]
    summaries: tuple[MetricSummary, ...]
    #: Every ``<Record>`` seen in export.xml, including the ones we ignored.
    records_scanned: int
    #: Samples whose unit we could not safely convert.
    skipped_units: int

    @property
    def found_keys(self) -> tuple[str, ...]:
        return tuple(summary.key for summary in self.summaries)


def _export_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """Find export.xml, refusing archives that are too big or oddly compressed."""
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name == EXPORT_MEMBER or name.endswith(f"/{EXPORT_MEMBER}"):
            if info.file_size > MAX_XML_BYTES:
                raise ExportError("That export is larger than MotorScope will read.")
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ExportError(
                        "That ZIP expands to an implausible size and was not read."
                    )
            return info
    raise ExportError(
        "No export.xml was found. Make sure you chose the ZIP Apple Health produced, "
        "not an individual file from it."
    )


def _records(raw) -> Iterator[bytes]:
    """Yield the export's bytes from its root element onwards, repairing known quirks.

    Two things are handled here, both of them necessary to read a real export.

    The prolog is discarded
    -----------------------
    Apple opens export.xml with an XML declaration and a ``<!DOCTYPE HealthData [ ... ]>``
    internal DTD describing every element and attribute. That DTD is *documentation* as
    far as MotorScope is concerned: nothing here validates against it. It is dropped for
    two reasons.

    First, Apple's own DTD is malformed in several HealthKit versions, and expat rejects
    the file outright with errors such as ``ATTLIST: no name for Attribute`` and
    ``DOCTYPE improperly terminated``. This is a long-standing, widely reported problem
    that other tools work around by patching the DTD by hand before parsing.

    Second, discarding the prolog removes the document type declaration entirely, so
    there is no internal subset and nothing for an entity-expansion attack to grow. An
    ``&name;`` reference that survives into the body then fails as an undefined entity
    rather than being expanded, which is the safe direction to fail.

    Only the prolog is ever buffered, and it is capped, so a multi-gigabyte export still
    streams through a fixed amount of memory.

    The vertical-tab quirk
    ---------------------
    Some exports contain U+000B, which is not a legal XML character and makes expat raise
    a parse error in the middle of an otherwise fine file. It is replaced with a space as
    it streams past. Mobility values are numeric, so this only ever affects a record that
    was already unreadable.
    """
    buffer = b""
    started = False

    while chunk := raw.read(PROLOG_CHUNK_BYTES):
        chunk = chunk.replace(b"\x0b", b" ")

        if started:
            yield chunk
            continue

        buffer += chunk
        match = _ROOT_RE.search(buffer)
        if match:
            started = True
            yield buffer[match.start() :]
            buffer = b""
        elif len(buffer) > MAX_PROLOG_BYTES:
            raise ExportError(
                "That file does not look like an Apple Health export; no <HealthData> "
                "element was found."
            )

    if not started:
        raise ExportError(
            "That file does not look like an Apple Health export; no <HealthData> "
            "element was found."
        )


def _records_from_archive(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[tuple]:
    """Stream (tag, attributes) for every <Record> in the export, then discard it.

    Attributes are handed over as a plain dict rather than an element because the
    caller only needs to read them once; keeping the elements would retain one object per
    record, which for a multi-million-record export is hundreds of megabytes.
    """
    parser = XMLPullParser(events=("end",))

    with archive.open(info) as raw:
        for chunk in _records(raw):
            try:
                parser.feed(chunk)
                # Iterated inside the guard on purpose. On Python 3.13+ read_events()
                # is itself a generator that re-raises a parse error as it is consumed,
                # so a failure surfacing here would otherwise escape uncaught.
                for _, element in parser.read_events():
                    if element.tag == "Record":
                        yield dict(element.attrib)
                    element.clear()
            except ParseError:
                # An undefined entity or a truncated record is only detected once the
                # parser reaches it, which for a large export can be a long way in.
                # Converted here so a damaged file produces a readable message instead
                # of a server error.
                raise ExportError(
                    "That export.xml is damaged and could not be read past the "
                    "measurements."
                ) from None


def _convert(metric_key: str, value: float, unit: str) -> float | None:
    """Convert a sample into the metric's display unit, or None if we cannot."""
    factor = UNIT_CONVERSIONS.get(metric_key, {}).get(unit.strip())
    if factor is None:
        return None
    return value * factor


def _local_day(timestamp: str | None) -> str | None:
    """The YYYY-MM-DD day an Apple timestamp falls on.

    Apple writes local time with a UTC offset, e.g. ``2026-10-04 08:14:22 +0100``. Taking
    the leading date keeps a walk that started at 00:30 filed under the day the person
    actually walked it, instead of shifting it across a date boundary.
    """
    if not timestamp or len(timestamp) < 10:
        return None
    day = timestamp[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return day
    return None


def parse_export(path: Path) -> ImportResult:
    """Extract a scorable set of mobility readings from an Apple Health ZIP.

    Raises ExportError with a message written for the person who uploaded the file.
    """
    size = path.stat().st_size
    if size == 0:
        raise ExportError("That file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ExportError("That file is too large to be a Health export.")

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ExportError(
            "That file is not a ZIP archive. Health exports always arrive as a ZIP."
        ) from None

    with archive:
        info = _export_member(archive)

        # key -> day -> [sum, count]
        collected: dict[str, dict[str, list[float]]] = {}
        records = 0
        skipped_units = 0

        for attributes in _records_from_archive(archive, info):
            records += 1

            key = HEALTHKIT_TYPES.get(attributes.get("type") or "")
            day = _local_day(attributes.get("startDate"))
            raw_value = attributes.get("value")

            if key is None or day is None or raw_value is None:
                continue
            try:
                number = float(raw_value)
            except ValueError:
                continue
            if number != number:  # NaN
                continue

            converted = _convert(key, number, attributes.get("unit") or "")
            if converted is None:
                skipped_units += 1
                continue

            days = collected.setdefault(key, {})
            slot = days.setdefault(day, [0.0, 0])
            slot[0] += converted
            slot[1] += 1

    if not collected:
        raise ExportError(
            "No mobility measurements were found in that export. Mobility data appears "
            "only after you have walked with an Apple Watch or iPhone for a while."
        )

    summaries: list[MetricSummary] = []
    for key, days in collected.items():
        latest_day = max(days)
        total, count = days[latest_day]
        summaries.append(
            MetricSummary(
                key=key,
                value=round(total / count, 4),
                recorded_on=latest_day,
                sample_count=count,
            )
        )
    summaries.sort(key=lambda summary: _DISPLAY_ORDER.get(summary.key, len(_DISPLAY_ORDER)))

    recorded_on = max(summary.recorded_on for summary in summaries)

    return ImportResult(
        recorded_on=recorded_on,
        values={summary.key: summary.value for summary in summaries},
        summaries=tuple(summaries),
        records_scanned=records,
        skipped_units=skipped_units,
    )


def format_note(summary: MetricSummary) -> str:
    """Human-readable provenance for one imported metric."""
    samples = "sample" if summary.sample_count == 1 else "samples"
    return f"{summary.sample_count} {samples} from {summary.recorded_on}"