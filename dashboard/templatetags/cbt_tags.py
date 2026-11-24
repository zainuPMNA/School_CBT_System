from django import template

register = template.Library()


@register.filter
def duration(seconds):
    """123 -> '2:03', 3725 -> '1:02:05'."""
    if seconds is None or seconds == "":
        return "—"
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@register.filter
def ago(seconds):
    """Seconds -> '12s ago' / '3m ago'."""
    if seconds is None:
        return "never"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"
