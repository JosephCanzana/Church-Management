"""core.templatetags.ui_tags: small template helpers used across the app.

Load with {% load ui_tags %}.

  {{ value|title_case }}   shows stored lowercase text as a title ("sta. rosa" -> "Sta. Rosa")
  {% modal "id" title="..." %}...{% endmodal %}   a reusable modal dialog

The modal exists because an included template cannot receive a block of
content. The tag renders `includes/modal.html` around whatever sits between
{% modal %} and {% endmodal %}. Open it from Alpine with
$store.ui.openModal('id') and close it with $store.ui.closeModal().
"""
import re

from django import template

from core.text import title_case as _title_case

register = template.Library()

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_WIDTHS = {"sm": "max-w-sm", "md": "max-w-md", "lg": "max-w-lg", "xl": "max-w-xl", "2xl": "max-w-2xl"}


@register.filter(name="title_case")
def title_case_filter(value):
    """Title case for stored lowercase values (see core.text.title_case)."""
    return _title_case(value)


class ModalNode(template.Node):
    """Renders includes/modal.html with the tag's body as the dialog content."""

    def __init__(self, nodelist, modal_id, title, width):
        self.nodelist = nodelist
        self.modal_id = modal_id
        self.title = title
        self.width = width

    def render(self, context):
        body = self.nodelist.render(context)
        title = self.title.resolve(context) if self.title else ""
        width = self.width.resolve(context) if self.width else "md"
        shell = context.template.engine.get_template("includes/modal.html")
        with context.push(
            modal_id=self.modal_id,
            modal_title=title,
            modal_width=_WIDTHS.get(width, _WIDTHS["md"]),
            modal_body=body,
        ):
            return shell.render(context)


@register.tag(name="modal")
def do_modal(parser, token):
    """{% modal "filters" title="Filter extensions" width="md" %}...{% endmodal %}

    The id must be a literal (letters, digits, - and _). width is one of
    sm, md (default), lg, xl, 2xl.
    """
    bits = token.split_contents()[1:]
    if not bits:
        raise template.TemplateSyntaxError("modal needs an id, e.g. {% modal \"filters\" %}")
    modal_id = bits[0].strip("\"'")
    if not _ID.match(modal_id) or bits[0][0] not in "\"'":
        raise template.TemplateSyntaxError("modal id must be a quoted literal such as \"filters\"")
    options = {}
    for bit in bits[1:]:
        key, sep, value = bit.partition("=")
        if not sep or key not in ("title", "width"):
            raise template.TemplateSyntaxError(f"modal: unknown argument {bit!r}")
        options[key] = parser.compile_filter(value)
    nodelist = parser.parse(("endmodal",))
    parser.delete_first_token()
    return ModalNode(nodelist, modal_id, options.get("title"), options.get("width"))
