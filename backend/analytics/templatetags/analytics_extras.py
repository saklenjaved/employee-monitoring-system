from django import template

register = template.Library()

CATEGORY_LABELS = {
    "productive": "Productive",
    "unproductive": "Unproductive",
    "neutral": "Neutral",
    "unclassified": "Unclassified",
    "mixed": "Mixed",
}


@register.filter
def duration(seconds):
    """Seconds as a short text: 45s, 3m 05s, 2h 05m."""
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@register.filter
def percent(value):
    """A percentage such as 62.5%, or a dash when there is no data."""
    return "-" if value is None else f"{value:g}%"


@register.filter
def share(part, whole):
    """`part` as a whole-number percentage of `whole`, for the width of a bar."""
    if not whole:
        return 0
    return round(100 * part / whole)


@register.filter
def lookup(mapping, key):
    return mapping.get(key, 0)


@register.filter
def category_label(key):
    return CATEGORY_LABELS.get(key, key)
