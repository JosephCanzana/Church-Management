"""dashboards.charts: pure chart geometry. No Django, no database, no HTML.

Every function takes plain numbers and returns plain dicts and lists that a template (or the A4
print page) draws as inline SVG. Nothing here knows about colors or CSS classes: the screen
templates add token classes (``stroke-accent``...), the print templates add grayscale attributes.

Rules shared by all functions:

* Empty input, a single value and all zeros never divide by zero. They return a dict with
  ``"empty": True`` (or a sensible flat shape) so a template can draw "No data yet" instead.
* Values may be ``int``, ``float`` or ``Decimal``; they are turned into ``float`` ONLY to work out
  positions. Money is never stored or returned as a float: the caller keeps its Decimal and
  prints that as text.
* Negative, ``None`` and non-finite values are treated as 0 (these charts show counts and totals).
* Coordinates are rounded to 2 decimals so the SVG stays small and tests are stable.
* SVG y grows downward, so a bigger value has a SMALLER y.
"""
import math

# Space kept free around the plot area for tick labels (left, bottom) and the stroke (top, right).
PAD_TOP = 16
PAD_RIGHT = 16
PAD_BOTTOM = 32
PAD_LEFT = 40

#: Ticks aim for about this many intervals on the y axis.
TARGET_TICKS = 4
#: A non-zero bar is never thinner than this share of the full width (a tiny value must still show).
MIN_BAR_PERCENT = 4


# ------------------------------------------------------------------ helpers
def _num(value):
    """Return ``value`` as a safe float: None, NaN, infinity and negatives become 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def _r(number):
    """Round a coordinate to 2 decimals (and avoid ``-0.0``)."""
    return round(number, 2) + 0.0


def _labels(labels, count):
    """Return exactly ``count`` text labels: missing ones become 1, 2, 3... so a mark always has a name."""
    given = [str(label) for label in (labels or [])][:count]
    return given + [str(i + 1) for i in range(len(given), count)]


def _tick_text(number):
    """Return a tick as text: ``40`` not ``40.0``, ``0.5`` stays ``0.5``."""
    if float(number).is_integer():
        return str(int(number))
    return f"{number:g}"


def _nice_step(raw_step, integer_only):
    """Round a raw step up to 1, 2 or 5 times a power of ten (never below 1 when ``integer_only``)."""
    if raw_step <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in (1, 2, 5, 10):
        step = factor * magnitude
        if step >= raw_step:
            break
    return max(step, 1.0) if integer_only else step


def y_scale(top_value, integer_only=True):
    """Return ``(ticks, ceiling)``: round tick values from 0 and the top tick every chart scales to.

    ``top_value`` is the biggest value drawn. Zero (or less) still gives ``[0, 1]``, so an empty
    chart has an axis and nothing divides by zero.
    """
    top = _num(top_value)
    if top <= 0:
        return [0.0, 1.0], 1.0
    step = _nice_step(top / TARGET_TICKS, integer_only)
    count = max(1, math.ceil(top / step - 1e-9))
    ticks = [round(step * i, 10) for i in range(count + 1)]
    return ticks, ticks[-1]


def _y_ticks(ticks, ceiling, top, plot_height):
    """Turn tick values into ``{"value", "label", "y"}`` rows for the grid lines."""
    return [
        {"value": tick, "label": _tick_text(tick), "y": _r(top + plot_height - tick / ceiling * plot_height)}
        for tick in ticks
    ]


def _plot_box(width, height):
    """Return the plot rectangle inside the padding as a dict (left, top, right, bottom, width, height)."""
    left, top = PAD_LEFT, PAD_TOP
    right, bottom = max(left + 1, width - PAD_RIGHT), max(top + 1, height - PAD_BOTTOM)
    return {"left": left, "top": top, "right": right, "bottom": bottom, "width": right - left, "height": bottom - top}


# ---------------------------------------------------------------- line chart
def line_chart(values, labels, width=640, height=240):
    """Geometry for a line chart (attendance over the last gatherings).

    Returns ``{"width", "height", "plot", "points", "polyline", "area", "y_ticks", "baseline_y",
    "ceiling", "empty"}``:

    * ``points``: one ``{"x", "y", "label", "value"}`` per value, oldest first. ``label`` and ``value``
      are what the template writes in the ``<title>`` tooltip and in the text list.
    * ``polyline``: the ``points`` attribute string (``"x,y x,y"``); ``area``: a closed path
      (``d`` attribute) under the line, or ``""`` with fewer than two points.
    * ``y_ticks``: grid lines, 0 at the bottom up to ``ceiling``.
    * With no values ``points`` is ``[]`` and ``empty`` is True; one value sits in the middle.
    """
    numbers = [_num(v) for v in (values or [])]
    box = _plot_box(width, height)
    ticks, ceiling = y_scale(max(numbers, default=0))
    names = _labels(labels, len(numbers))
    count = len(numbers)

    points = []
    for index, number in enumerate(numbers):
        x = box["left"] + (box["width"] / 2 if count == 1 else index * box["width"] / (count - 1))
        y = box["bottom"] - number / ceiling * box["height"]
        points.append({"x": _r(x), "y": _r(y), "label": names[index], "value": values[index] if values else 0})

    polyline = " ".join(f"{p['x']},{p['y']}" for p in points)
    area = ""
    if count >= 2:
        area = (
            "M" + " L".join(f"{p['x']},{p['y']}" for p in points)
            + f" L{points[-1]['x']},{_r(box['bottom'])} L{points[0]['x']},{_r(box['bottom'])} Z"
        )
    return {
        "width": width, "height": height, "plot": box,
        "points": points, "polyline": polyline, "area": area,
        "y_ticks": _y_ticks(ticks, ceiling, box["top"], box["height"]),
        "baseline_y": _r(box["bottom"]), "ceiling": ceiling, "empty": count == 0,
    }


# ------------------------------------------------------------ grouped columns
def column_groups(groups, width=640, height=240, gap_ratio=0.3):
    """Geometry for grouped columns (for example age network groups, each with men and women).

    ``groups`` is a list of ``(label, [value per series])`` pairs (a ``{"label", "values"}`` dict also
    works). Every group should have the same number of series; a short one is padded with zeros.

    Returns ``{"width", "height", "plot", "groups", "y_ticks", "baseline_y", "ceiling", "series_count",
    "empty"}``. Each group is ``{"label", "label_x", "bars"}`` and each bar is
    ``{"x", "y", "width", "height", "value", "series", "group"}`` (``series`` is the index inside the
    group, so the template can color series 0 and 1 differently and write the legend).
    A zero value still returns a bar with ``height`` 0, so its tooltip and text stay in step.
    ``empty`` is True when there are no groups or every value is 0.
    """
    rows = []
    for group in groups or []:
        label, values = (group["label"], group["values"]) if isinstance(group, dict) else group
        rows.append((str(label), [_num(v) for v in values]))

    box = _plot_box(width, height)
    series_count = max((len(values) for _label, values in rows), default=0)
    top = max((v for _label, values in rows for v in values), default=0)
    ticks, ceiling = y_scale(top)

    group_count = len(rows)
    slot = box["width"] / group_count if group_count else box["width"]
    inner = slot * (1 - gap_ratio)                       # the part of a slot the bars use
    bar_width = inner / series_count if series_count else 0

    drawn = []
    for g_index, (label, values) in enumerate(rows):
        start = box["left"] + g_index * slot + (slot - inner) / 2
        bars = []
        for s_index in range(series_count):
            value = values[s_index] if s_index < len(values) else 0.0
            bar_height = value / ceiling * box["height"]
            bars.append({
                "x": _r(start + s_index * bar_width), "y": _r(box["bottom"] - bar_height),
                "width": _r(bar_width), "height": _r(bar_height),
                "value": value, "series": s_index, "group": label,
            })
        drawn.append({"label": label, "label_x": _r(box["left"] + g_index * slot + slot / 2), "bars": bars})

    return {
        "width": width, "height": height, "plot": box, "groups": drawn,
        "y_ticks": _y_ticks(ticks, ceiling, box["top"], box["height"]),
        "baseline_y": _r(box["bottom"]), "ceiling": ceiling, "series_count": series_count,
        "empty": top <= 0,
    }


# --------------------------------------------------------------------- donut
def donut(segments, radius=60, stroke=18):
    """Geometry for a donut (or a progress ring with two segments).

    ``segments`` is a list of ``(label, value)`` pairs (a ``{"label", "value"}`` dict also works).
    Each arc is a ``<circle>`` with the same ``r``: draw it with ``stroke-dasharray`` and
    ``stroke-dashoffset`` from here, inside a group rotated -90 degrees so the first arc starts at 12 o'clock.

    Returns ``{"size", "cx", "cy", "r", "stroke", "circumference", "total", "items", "arcs", "empty"}``.
    ``items`` has every segment (``label``, ``value``, ``pct`` with one decimal, ``index``) in the order given,
    for the text list and legend. ``arcs`` has the same fields plus ``dasharray`` and ``dashoffset`` for the
    segments above zero only. With a total of 0 there are no arcs and ``empty`` is True (draw the track alone).
    """
    parsed = []
    for segment in segments or []:
        label, value = (segment["label"], segment["value"]) if isinstance(segment, dict) else segment
        parsed.append((str(label), value, _num(value)))

    circumference = 2 * math.pi * radius
    total = sum(number for _l, _v, number in parsed)
    size = 2 * radius + stroke
    items, arcs, used = [], [], 0.0
    for index, (label, value, number) in enumerate(parsed):
        pct = round(number / total * 100, 1) if total else 0.0
        item = {"label": label, "value": value, "pct": pct, "index": index}
        items.append(item)
        if number <= 0 or not total:
            continue
        length = number / total * circumference
        arcs.append({
            **item,
            "dasharray": f"{_r(length)} {_r(max(circumference - length, 0))}",
            "dashoffset": _r(-used),
        })
        used += length
    return {
        "size": size, "cx": _r(size / 2), "cy": _r(size / 2), "r": radius, "stroke": stroke,
        "circumference": _r(circumference), "total": total, "items": items, "arcs": arcs, "empty": total <= 0,
    }


# ------------------------------------------------------- horizontal bars
def hbar(items, max_width=300, row_height=22, gap=8):
    """Geometry for horizontal bars (people or attendance per extension).

    ``items`` is a list of ``(label, value)`` pairs, already sorted by the caller. The biggest value gets
    ``max_width``; a non-zero value is never thinner than ``MIN_BAR_PERCENT`` of it.

    Returns ``{"rows", "max", "max_width", "height", "empty"}``; each row is ``{"label", "value", "width",
    "pct", "y", "height"}`` (``y`` stacks rows top to bottom; ``height`` of the whole chart is ``height``).
    All zeros give rows with ``width`` 0 and ``empty`` True.
    """
    parsed = [(str(label), value, _num(value)) for label, value in (items or [])]
    top = max((number for _l, _v, number in parsed), default=0.0)
    rows = []
    for index, (label, value, number) in enumerate(parsed):
        pct = number / top * 100 if top else 0.0
        if number > 0:
            pct = max(pct, MIN_BAR_PERCENT)
        rows.append({
            "label": label, "value": value, "pct": round(pct, 1), "width": _r(pct / 100 * max_width),
            "y": index * (row_height + gap), "height": row_height,
        })
    total_height = len(rows) * (row_height + gap) - gap if rows else 0
    return {"rows": rows, "max": top, "max_width": max_width, "height": max(total_height, 0), "empty": top <= 0}


# ----------------------------------------------------------------- sparkline
def sparkline(values, width=120, height=32):
    """Geometry for a tiny line with no axes (the giving trend).

    Returns ``{"width", "height", "points", "polyline", "last", "min", "max", "empty"}``. ``last`` is the
    final point (for a dot) or None. A flat series (all equal, or all zeros) is drawn straight through the
    middle; one value is a single point in the middle.
    """
    numbers = [_num(v) for v in (values or [])]
    pad = 3                                              # keeps the stroke and the last dot inside the box
    inner_w, inner_h = max(width - 2 * pad, 1), max(height - 2 * pad, 1)
    low, high = (min(numbers), max(numbers)) if numbers else (0.0, 0.0)
    span = high - low
    count = len(numbers)

    points = []
    for index, number in enumerate(numbers):
        x = pad + (inner_w / 2 if count == 1 else index * inner_w / (count - 1))
        y = pad + inner_h / 2 if span == 0 else pad + inner_h - (number - low) / span * inner_h
        points.append({"x": _r(x), "y": _r(y), "value": values[index]})
    return {
        "width": width, "height": height, "points": points,
        "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        "last": points[-1] if points else None, "min": low, "max": high, "empty": count == 0,
    }
