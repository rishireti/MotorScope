"""Apple Watch and iPhone mobility metrics.

Each metric carries a unit, a plausible adult range used for sanity checks, and a
direction flag so a single composite score can be assembled from values that do not
share units.

Scoring note
------------
These metrics are measured in different units and are not directly comparable. A raw
arithmetic mean of, say, walking speed (m/s) and six-minute walk distance (metres)
is not a physiologically meaningful quantity. To keep a single "Mobility Score", each
entered value is first normalised against its expected range to a 0-100 sub-score,
and the composite is the mean of those sub-scores. Only the sub-scores are averaged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    description: str
    low: float
    high: float
    #: True when a higher raw value is a better (healthier) result.
    higher_is_better: bool
    #: True when only a smartwatch can produce this measurement.
    watch_only: bool
    #: Unit the metric is shown in. The value is stored in `unit`; this is only what the
    #: reader sees, so an imperial unit such as mph or in is used where one is familiar.
    display_unit: str = ""
    #: Multiplier from the stored unit to the display unit (1.0 when they are the same).
    display_factor: float = 1.0
    #: Decimal places used when formatting the displayed value and its range.
    display_decimals: int = 1

    @property
    def step(self) -> float:
        return round(self.high - self.low, 4)

    @property
    def shown_unit(self) -> str:
        return self.display_unit or self.unit


METRICS: tuple[Metric, ...] = (
    Metric(
        key="walking_speed",
        label="Walking speed",
        unit="m/s",
        description=(
            "How fast you cover ground while walking. A widely used marker of general "
            "mobility, and one of the first things that tends to slow with age, joint "
            "problems, or neurological change."
        ),
        low=0.4,
        high=2.0,
        higher_is_better=True,
        watch_only=False,
        display_unit="mph",
        display_factor=2.2369362920544,
        display_decimals=1,
    ),
    Metric(
        key="step_length",
        label="Walking step length",
        unit="cm",
        description=(
            "The distance between where one foot lands and then the other. Shorter "
            "steps at the same speed often signal a cautious or unsteady gait."
        ),
        low=40.0,
        high=90.0,
        higher_is_better=True,
        watch_only=False,
        display_unit="in",
        display_factor=0.39370078740157477,
        display_decimals=1,
    ),
    Metric(
        key="walking_asymmetry",
        label="Walking asymmetry",
        unit="%",
        description=(
            "The percentage of steps where the timing of one foot differs from the "
            "other, so one side spends more or less time on the ground than its pair. A "
            "lower number means the two sides move more evenly; asymmetry often appears "
            "when one side is being favoured or protected."
        ),
        low=0.0,
        high=100.0,
        higher_is_better=False,
        watch_only=False,
    ),
    Metric(
        key="double_support_time",
        label="Double support time",
        unit="%",
        description=(
            "The share of each step spent with both feet on the ground. Typical walking "
            "sits around 20-40%. Lower generally means better balance."
        ),
        low=15.0,
        high=45.0,
        higher_is_better=False,
        watch_only=False,
    ),
    Metric(
        key="cardio_fitness",
        label="Cardio fitness",
        unit="mL/(kg·min)",
        description=(
            "Estimated VO2 max: how much oxygen your body can use per kilogram per "
            "minute during exercise. Reflects overall cardiovascular fitness rather "
            "than gait specifically, but it shapes how much walking you manage."
        ),
        low=14.0,
        high=65.0,
        higher_is_better=True,
        watch_only=True,
    ),
    Metric(
        key="ground_contact_time",
        label="Ground contact time",
        unit="ms",
        description=(
            "How long each foot stays in contact with the ground while running. Shorter "
            "contact usually indicates a more efficient, lighter stride."
        ),
        low=200.0,
        high=350.0,
        higher_is_better=False,
        watch_only=True,
        display_decimals=0,
    ),
    Metric(
        key="running_stride_length",
        label="Running stride length",
        unit="cm",
        description=(
            "The distance covered per running stride. Grows with fitness and speed, "
            "though very large strides can also mean overstriding."
        ),
        low=70.0,
        high=140.0,
        higher_is_better=True,
        watch_only=True,
        display_unit="in",
        display_factor=0.39370078740157477,
        display_decimals=1,
    ),
    Metric(
        key="six_minute_walk",
        label="Six-minute walk",
        unit="m",
        description=(
            "How far you can walk in six minutes. A practical endurance measure, and "
            "one clinicians often track before and after an intervention."
        ),
        low=400.0,
        high=900.0,
        higher_is_better=True,
        watch_only=True,
        display_unit="ft",
        display_factor=3.280839895013123,
        display_decimals=0,
    ),
    Metric(
        key="stair_speed_up",
        label="Stair speed up",
        unit="m/s",
        description=(
            "Your pace climbing stairs. Stairs demand more of the legs and less visual "
            "feedback, so this often drops before flat-ground walking speed does."
        ),
        low=0.0,
        high=1.0,
        higher_is_better=True,
        watch_only=True,
        display_unit="ft/s",
        display_factor=3.280839895013123,
        display_decimals=1,
    ),
    Metric(
        key="stair_speed_down",
        label="Stair speed down",
        unit="m/s",
        description=(
            "Your pace descending stairs. Descending is usually slower than climbing "
            "and is heavily influenced by balance and confidence."
        ),
        low=0.0,
        high=1.0,
        higher_is_better=True,
        watch_only=True,
        display_unit="ft/s",
        display_factor=3.280839895013123,
        display_decimals=1,
    ),
    Metric(
        key="vertical_oscillation",
        label="Vertical oscillation",
        unit="cm",
        description=(
            "How much your body rises and falls with each step. Less movement usually "
            "means a smoother, more even gait."
        ),
        low=5.0,
        high=11.0,
        higher_is_better=False,
        watch_only=True,
        display_unit="in",
        display_factor=0.39370078740157477,
        display_decimals=1,
    ),
    Metric(
        key="walking_steadiness",
        label="Walking steadiness",
        unit="%",
        description=(
            "Apple's own steadiness rating, which combines gait features into an "
            "estimate of fall risk over the following year. Higher is better."
        ),
        low=0.0,
        high=100.0,
        higher_is_better=True,
        watch_only=True,
    ),
)

METRICS_BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}
WATCH_ONLY_KEYS: tuple[str, ...] = tuple(m.key for m in METRICS if m.watch_only)
MANUAL_ONLY_KEYS: tuple[str, ...] = tuple(m.key for m in METRICS if not m.watch_only)


# ---------------------------------------------------------------------------
# Display units
#
# Everything is stored and scored in the metric unit Apple reports (metres,
# centimetres, metres per second). Where a familiar imperial unit exists it is used
# for display only, so the reader sees mph rather than m/s while the stored history,
# the expected range arithmetic, and the score all stay in one consistent system.
# ---------------------------------------------------------------------------


def to_display(metric: Metric, value: float) -> float:
    """Convert a stored value into the unit the reader is shown."""
    return value * metric.display_factor


def format_value(metric: Metric, value: float) -> str:
    """Format a stored value in its display unit, at the metric's precision."""
    return f"{to_display(metric, value):.{metric.display_decimals}f}"


def display_range(metric: Metric) -> tuple[float, float]:
    """The expected range in the display unit."""
    return to_display(metric, metric.low), to_display(metric, metric.high)


def range_text(metric: Metric) -> str:
    """Human-readable expected range in the display unit, e.g. `0.9-4.5 mph`."""
    low, high = display_range(metric)
    decimals = metric.display_decimals
    return f"{low:.{decimals}f}\u2013{high:.{decimals}f} {metric.shown_unit}"


def normalise(metric: Metric, value: float) -> float:
    """Map a raw reading onto 0-100, respecting the metric's direction.

    Values outside the expected range are clamped rather than rejected, so a single
    odd reading cannot inflate or deflate the composite beyond its bounds.
    """
    span = metric.high - metric.low
    if span <= 0:
        return 50.0
    ratio = (value - metric.low) / span
    ratio = max(0.0, min(1.0, ratio))
    return ratio * 100.0 if metric.higher_is_better else (1.0 - ratio) * 100.0


def score_values(values: dict[str, float]) -> float | None:
    """Return the composite Mobility Score for a set of readings.

    Returns None when nothing has been entered. The composite is the mean of the
    per-metric sub-scores, never the mean of the raw values.
    """
    subs = [normalise(METRICS_BY_KEY[k], v) for k, v in values.items() if k in METRICS_BY_KEY]
    if not subs:
        return None
    return round(sum(subs) / len(subs), 1)


def parse_values(raw: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    """Split submitted form values into clean readings and per-field errors.

    Blank fields are skipped rather than treated as zero, so a partially completed
    entry still produces a score from what was actually measured.
    """
    values: dict[str, float] = {}
    errors: dict[str, str] = {}

    for metric in METRICS:
        submitted = (raw.get(metric.key) or "").strip()
        if not submitted:
            continue
        try:
            number = float(submitted)
        except ValueError:
            errors[metric.key] = "Enter a number."
            continue
        if number != number or number in (float("inf"), float("-inf")):
            errors[metric.key] = "Enter a real number."
            continue
        if number < 0:
            errors[metric.key] = "Cannot be negative."
            continue
        values[metric.key] = number

    return values, errors


def sub_scores(values: dict[str, float]) -> dict[str, float]:
    """Per-metric 0-100 sub-scores, for display alongside the composite."""
    return {
        key: round(normalise(metric, value), 1)
        for key, value in values.items()
        if (metric := METRICS_BY_KEY.get(key)) is not None
    }


# ---------------------------------------------------------------------------
# Score interpretation
#
# These bands describe where a composite score falls within the ranges configured
# above. They are NOT clinical categories and are not calibrated against any
# published outcome. The underlying ranges are MotorScope's own conservative
# defaults, not values taken from a clinical standard; see SCORE_CAVEAT.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreBand:
    floor: int
    ceiling: int
    label: str
    summary: str


SCORE_BANDS: tuple[ScoreBand, ...] = (
    ScoreBand(
        0,
        19,
        "Very limited",
        "Most readings sit near the weaker end of the built-in ranges.",
    ),
    ScoreBand(
        20,
        39,
        "Below mid-range",
        "Several readings sit below the middle of the built-in ranges.",
    ),
    ScoreBand(
        40,
        59,
        "Mid-range",
        "Readings are spread around the middle of the built-in ranges.",
    ),
    ScoreBand(
        60,
        79,
        "Above mid-range",
        "Most readings sit comfortably above the middle of the ranges.",
    ),
    ScoreBand(
        80,
        100,
        "Near the top",
        "Readings sit toward the healthier end of the built-in ranges.",
    ),
)

SCORE_CAVEAT = (
    "MotorScope places each reading on its configured range, then averages those "
    "0-100 sub-scores. The bands above describe where that average falls. They are "
    "not a clinical rating, and they are not compared against other people, other "
    "apps, or any published outcome."
)


def score_band(score: float | None) -> ScoreBand | None:
    """The band a composite score falls into, or None when there is no score."""
    if score is None:
        return None
    for band in SCORE_BANDS:
        if band.floor <= score <= band.ceiling:
            return band
    return SCORE_BANDS[-1]