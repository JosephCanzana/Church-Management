"""Shared Django template filters and UI tags used across the app."""

import html

from django import template
from django.template.base import TextNode
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="title_case")
def title_case(value):
    """Render lowercase names and place values in a readable title case."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    return text.title()


@register.tag(name="modal")
def modal(parser, token):
    """Render a modal panel that is opened and closed through the global UI store."""
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            "'modal' tag requires a modal id, e.g. {% modal 'filters' title='Filter items' %}...{% endmodal %}"
        )

    modal_id = parser.compile_filter(bits[1])
    kwargs = {}
    for bit in bits[2:]:
        if "=" not in bit:
            raise template.TemplateSyntaxError(
                "Modal tag arguments must use key=value syntax."
            )
        key, value = bit.split("=", 1)
        kwargs[key] = parser.compile_filter(value)

    nodelist = parser.parse(("endmodal",))
    return ModalNode(modal_id, kwargs, nodelist)


@register.tag(name="endmodal")
def endmodal(parser, token):
    """Terminal node for the modal block tag."""
    return TextNode("")


class ModalNode(template.Node):
    """Render a simple modal wrapper and bind it to the shared UI state."""

    def __init__(self, modal_id, kwargs, nodelist):
        self.modal_id = modal_id
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context):
        modal_id = str(self.modal_id.resolve(context))
        title = self.kwargs.get("title")
        title_text = title.resolve(context) if title is not None else ""
        body = self.nodelist.render(context)

        modal_id_attr = html.escape(modal_id, quote=True)
        modal_key = mark_safe(repr(str(modal_id)))
        title_id = html.escape(f"{modal_id}-title", quote=True)
        title_html = html.escape(str(title_text), quote=False)

        return mark_safe(
            f'''
            <div x-data x-show="$store.ui.modal === {modal_key}" x-cloak
                 @keydown.escape.window="$store.ui.closeModal()"
                 @click.self="$store.ui.closeModal()"
                 class="fixed inset-0 z-[70] flex items-center justify-center p-4"
                 role="dialog" aria-modal="true" aria-labelledby="{title_id}" data-modal="{modal_id_attr}">
              <div class="absolute inset-0 bg-primary/40"></div>
              <div class="relative w-full max-w-lg rounded-xl border border-border bg-surface p-6 shadow-xl">
                <div class="flex items-start justify-between gap-4">
                  <h2 id="{title_id}" class="text-lg font-semibold text-txt-primary">{title_html}</h2>
                  <button type="button" @click="$store.ui.closeModal()"
                          class="rounded-md p-1 text-txt-secondary hover:text-txt-primary"
                          aria-label="Close modal">×</button>
                </div>
                <div class="mt-4">{body}</div>
              </div>
            </div>
            '''
        )
