"""Server-rendered SVG line charts.

The project has no JavaScript and no charting dependency, and health data never leaves
the machine, so the chart is drawn here as plain SVG geometry. Keeping the arithmetic
in Python also keeps the templates free of maths.

Points are ordered by the date each reading was *measured* (`recorded_on`), oldest on
the left and newest on the right, and laid out along a real time axis so an uneven gap
between readings shows up as an uneven gap rather than being flattened out.

Ordering uses the measurement date as the primary key and the time of entry as a
tie-break, because several readings saved on one day share a date. A stable sort on the
date alone would leave them in whatever order the database returned them (newest first),
which draws the latest reading on the left.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

from markupsafe import Markup


@dataclass(frozen=True)
class Point:
    when: datetime
    value: float
    label: str = ""


def parse_date(raw: object) -> date | None:
    """Accept the ISO date strings stored in `recorded_on`."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def parse_instant(row) -> datetime | None:
    """The measurement instant for a reading, ordered by the date it was measured.

    `recorded_on` is the day the measurement belongs to and is the primary sort key, so
    a reading entered later but dated earlier still plots in its correct place. The
    `created_at` time of day is folded in only to separate several readings taken on the
    same date, which would otherwise have no ordering at all.
    """
    day = parse_date(row["recorded_on"])
    if day is None:
        return None

    saved_on = None
    try:
        saved_on = row["created_at"]
    except (KeyError, IndexError, TypeError):
        saved_on = None

    clock = datetime.min.time()
    if isinstance(saved_on, str) and saved_on.strip():
        try:
            stamp = datetime.fromisoformat(saved_on.strip())
        except ValueError:
            stamp = None
        if stamp is not None:
            clock = stamp.time()

    return datetime.combine(day, clock)


def build_series(rows, column: str) -> list[Point]:
    """Chronologically ordered points, oldest first, skipping missing or invalid values."""
    collected: list[tuple[datetime, int, float]] = []

    for index, row in enumerate(rows):
        raw = row[column]
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not isfinite(value):
            continue
        when = parse_instant(row)
        if when is None:
            continue
        collected.append((when, index, value))

    # Oldest measured first. The row index is a last-resort stable tie-break so readings
    # sharing a date and a clock time keep the order they were entered.
    collected.sort(key=lambda item: (item[0], item[1]))

    return [
        Point(when=when, value=value, label=when.date().isoformat())
        for when, _, value in collected
    ]


def _nice_bounds(values: list[float], floor: float | None, ceiling: float | None) -> tuple[float, float]:
    """Pin a comfortable vertical range, padded so the line never touches the frame."""
    if floor is not None and ceiling is not None:
        low, high = floor, ceiling
    else:
        low = min(values)
        high = max(values)
        if low == high:
            # A flat series would otherwise divide by zero; give it a band to sit in.
            margin = abs(low) * 0.1 or 1.0
            low -= margin
            high += margin
        else:
            margin = (high - low) * 0.15
            low -= margin
            high += margin

    if low == high:
        low -= 1.0
        high += 1.0
    return low, high


def _tick_labels(series: list[Point]) -> tuple[str, str]:
    """Axis captions for the first and last point, with a time when useful."""
    first = series[0]
    last = series[-1]
    same_day = first.when.date() == last.when.date()
    if same_day and first.when != last.when:
        fmt = "%d %b, %H:%M"
    else:
        fmt = "%d %b %Y"
    return first.when.strftime(fmt), last.when.strftime(fmt)


def line_chart(
    series: list[Point],
    *,
    width: int = 660,
    height: int = 240,
    y_min: float | None = None,
    y_max: float | None = None,
    grid_lines: int = 4,
    pad_left: int = 46,
    pad_right: int = 14,
    pad_top: int = 16,
    pad_bottom: int = 28,
    unit: str = "",
    decimals: int = 0,
) -> dict | None:
    """SVG geometry for a line chart, or None when there is nothing to plot."""
    if not series:
        return None

    values = [p.value for p in series]
    low, high = _nice_bounds(values, y_min, y_max)

    inner_w = max(width - pad_left - pad_right, 1)
    inner_h = max(height - pad_top - pad_bottom, 1)

    baseline = pad_top + inner_h
    elapsed = [(p.when - series[0].when).total_seconds() for p in series]
    span_seconds = elapsed[-1]

    def x_for(index: int, offset: float) -> float:
        if span_seconds <= 0:
            # Every reading shares one instant, so space them evenly by position.
            step = inner_w / max(len(series) - 1, 1)
            return pad_left + step * index
        return pad_left + inner_w * (offset / span_seconds)

    def y_for(value: float) -> float:
        ratio = (value - low) / (high - low)
        # Values beyond a pinned axis are pinned to the edge rather than escaping it.
        y = pad_top + inner_h * (1.0 - ratio)
        return max(pad_top, min(baseline, y))

    coords = [
        (x_for(index, offset), y_for(point.value))
        for index, (point, offset) in enumerate(zip(series, elapsed))
    ]

    grid = []
    for index in range(grid_lines + 1):
        ratio = index / grid_lines
        value = high - ratio * (high - low)
        grid.append(
            {
                "y": round(pad_top + inner_h * ratio, 2),
                "label": f"{value:.{decimals}f}{unit}",
            }
        )

    dots = [
        {
            "cx": round(x, 2),
            "cy": round(y, 2),
            "value": f"{point.value:.{decimals}f}",
            "title": f"{point.when.strftime('%d %b %Y, %H:%M')}: {point.value:.{decimals}f}{unit}",
        }
        for point, (x, y) in zip(series, coords)
    ]

    line_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
    area_points = (
        f"{coords[0][0]:.2f},{baseline:.2f} "
        + line_points
        + f" {coords[-1][0]:.2f},{baseline:.2f}"
    )

    x_first, x_last = _tick_labels(series)

    return {
        "width": width,
        "height": height,
        "baseline": baseline,
        "pad_left": pad_left,
        "inner_w": inner_w,
        "grid": grid,
        "dots": dots,
        "line_points": Markup(line_points),
        "area_points": Markup(area_points),
        "single": len(series) == 1,
        "point_count": len(series),
        "x_first": x_first,
        "x_last": x_last,
        "shared_instant": span_seconds == 0,
    }


def trend_note(series: list[Point]) -> str:
    """Short plain-language summary of the direction of travel."""
    if not series:
        return ""
    if len(series) < 2:
        return "One reading so far, so there is no trend to compare yet."
    first = series[0].value
    last = series[-1].value
    change = last - first
    if abs(change) < 0.05:
        return f"Flat across {len(series)} readings."
    direction = "up" if change > 0 else "down"
    return (
        f"{abs(change):.1f} points {direction} across {len(series)} readings "
        f"({first:.1f} to {last:.1f})."
    )