"""dashboards.templatetags.chart_tags: one filter that picks a chart color.

    {% load chart_tags %}
    <circle class="chart-stroke-{{ forloop.counter0|chart_slot }}" ...>

Colors are never written in a template or in Python. They are the ``--color-chart-1`` ... ``--color-chart-5``
tokens in ``static/css/input.css``, reached through the ``chart-fill-N``, ``chart-stroke-N`` and ``chart-bg-N``
classes defined next to them, so light and dark mode (and any other theme) recolor every chart at once.
"""
from django import template

register = template.Library()

#: How many chart colors exist. Keep equal to the number of ``--color-chart-N`` tokens in input.css.
CHART_COLORS = 5


@register.filter
def chart_slot(index):
    """Turn a 0-based item index into a color number 1..5 (it starts over after the fifth item).

    A chart never relies on color alone: every item also has its label and value as text.
    """
    try:
        return int(index) % CHART_COLORS + 1
    except (TypeError, ValueError):
        return 1
