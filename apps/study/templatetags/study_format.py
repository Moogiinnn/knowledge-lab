import re

from django import template
from django.utils.html import escape, linebreaks
from django.utils.safestring import mark_safe

register = template.Library()

INLINE_CODE = re.compile(r"`([^`\n]+)`")


@register.filter
def prompt_format(text: str) -> str:
    """Question prompt: paragraphs and line breaks, `inline code` shown as code. Everything
    else is escaped, so teachers cannot inject HTML into student pages."""
    escaped = INLINE_CODE.sub(r"<code>\1</code>", escape(text or ""))
    return mark_safe(linebreaks(escaped, autoescape=False))
