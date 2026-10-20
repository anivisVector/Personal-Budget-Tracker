"""Display helpers (also registered as Jinja filters)."""

from __future__ import annotations

from datetime import date


def format_inr(value, decimals: int = 2, *, sign: bool = False) -> str:
    """Format a number with Indian digit grouping: 1234567.5 -> ₹12,34,567.50."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    negative = number < 0
    whole, _, frac = f'{abs(number):.{decimals}f}'.partition('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups + [tail])
    text = f'₹{whole}' + (f'.{frac}' if decimals else '')
    if negative:
        return f'−{text}'
    if sign and number > 0:
        return f'+{text}'
    return text


def format_compact_inr(value) -> str:
    """Short form for tight spaces: ₹950, ₹12.4K, ₹3.2L, ₹1.1Cr."""
    number = float(value or 0)
    sign = '−' if number < 0 else ''
    n = abs(number)
    if n >= 1e7:
        return f'{sign}₹{n / 1e7:.1f}Cr'
    if n >= 1e5:
        return f'{sign}₹{n / 1e5:.1f}L'
    if n >= 1e3:
        return f'{sign}₹{n / 1e3:.1f}K'
    return f'{sign}₹{n:.0f}'


def format_percent(value, decimals: int = 0) -> str:
    if value is None:
        return '—'
    return f'{value * 100:.{decimals}f}%'


def nice_date(value) -> str:
    """'2026-10-05' -> '5 Oct 2026' (leaves anything unparseable untouched)."""
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or '')
    return f'{parsed.day} {parsed.strftime("%b %Y")}'


_CATEGORY_ICONS = {
    'food': 'fa-utensils', 'groceries': 'fa-basket-shopping', 'transport': 'fa-car-side', 'bills': 'fa-file-invoice',
    'shopping': 'fa-bag-shopping', 'entertainment': 'fa-film', 'health': 'fa-heart-pulse', 'education': 'fa-graduation-cap',
    'rent': 'fa-house', 'others': 'fa-shapes', 'salary': 'fa-briefcase', 'freelance': 'fa-laptop-code',
    'business': 'fa-store', 'investment': 'fa-chart-line', 'gift': 'fa-gift', 'other income': 'fa-coins',
    'travel': 'fa-plane', 'gym': 'fa-dumbbell', 'fuel': 'fa-gas-pump', 'insurance': 'fa-shield-heart', 'emi': 'fa-building-columns',
}


def category_icon(name, type_: str = 'expense') -> str:
    icon = _CATEGORY_ICONS.get(str(name or '').strip().lower())
    return icon or ('fa-arrow-down-long' if type_ == 'income' else 'fa-receipt')


def spoken_inr(value) -> str:
    """Amounts for speech synthesis: '1,250 rupees' reads better than '₹1,250.00'."""
    number = float(value or 0)
    rounded = round(abs(number))
    words = f'{rounded:,} rupees'
    return f'minus {words}' if number < 0 else words
