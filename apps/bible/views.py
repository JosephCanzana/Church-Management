"""bible.views: book list, chapter list, chapter reader, version picker, Next (counts a read), bookmark.

Every view is open to all four roles (a person reads their own Bible), but each
still carries role_required, as the project rules ask. The chosen version lives
in a cookie on that device; it is checked against the catalog on every request,
so a tampered value just falls back to the default version.
"""
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role

from . import services
from .books import BOOKS, NEW, OLD, get_book, neighbor_chapters
from .services import BibleError, TextNotImported, VersionLocked
from .versions import SOURCE_LOCAL, VERSIONS_BY_CODE

ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
VERSION_COOKIE = "bible_version"
VERSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # one year


def _current_version(request):
    """The version saved in this device's cookie (default when missing or invalid)."""
    return services.resolve_version(request.COOKIES.get(VERSION_COOKIE))


def _book_or_404(osis_id):
    """Return the Book for the url, or raise a 404."""
    book = get_book(osis_id)
    if book is None:
        raise Http404("Unknown book")
    return book


@role_required(*ALL_ROLES)
def reader_view(request):
    """List every book (the page the Bible nav link opens)."""
    return render(request, "bible/reader.html", {
        "version": _current_version(request),
        "statuses": services.version_statuses(),
        "old_books": [b for b in BOOKS if b.testament == OLD],
        "new_books": [b for b in BOOKS if b.testament == NEW],
    })


@role_required(*ALL_ROLES)
def book_view(request, book):
    """List the chapters of one book."""
    book = _book_or_404(book)
    return render(request, "bible/book.html", {
        "version": _current_version(request),
        "statuses": services.version_statuses(),
        "book": book,
        "chapters": range(1, book.chapters + 1),
    })


@role_required(*ALL_ROLES)
def chapter_view(request, book, chapter):
    """Show one chapter in the person's chosen version."""
    book = _book_or_404(book)
    if not 1 <= chapter <= book.chapters:
        raise Http404("Unknown chapter")
    version = _current_version(request)
    context = {
        "version": version,
        "statuses": services.version_statuses(),
        "book": book,
        "chapter": chapter,
        "text": None,
        "locked_reason": "",
        "missing": False,
        "already_read": services.chapter_read_today(request.user, book.osis_id, chapter),
        "bookmarked": _is_bookmarked(request.user, book.osis_id, chapter),
    }
    previous, following = neighbor_chapters(book.osis_id, chapter)
    context["previous"] = _chapter_link(previous)
    context["following"] = _chapter_link(following)
    try:
        context["text"] = services.get_chapter(version.code, book.osis_id, chapter)
    except VersionLocked as locked:
        context["locked_reason"] = locked.reason
    except TextNotImported:
        context["missing"] = True
    return render(request, "bible/chapter.html", context)


def _chapter_link(target):
    """Turn (osis_id, chapter) from neighbor_chapters into a dict for the template."""
    if target is None:
        return None
    osis_id, number = target
    return {
        "book": get_book(osis_id),
        "chapter": number,
        "url": reverse("bible:chapter", args=[osis_id, number]),
    }

def _is_bookmarked(user, osis_id, chapter):
    """True when the person's bookmark sits on this chapter."""
    bookmark = services.get_bookmark(user)
    return bool(bookmark and bookmark.book == osis_id and bookmark.chapter == chapter)

@role_required(*ALL_ROLES)
@require_POST
def set_version_view(request):
    """Save the chosen version in a cookie, then go back to where the person was."""
    code = request.POST.get("version", "")
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("bible:reader")
    version = VERSIONS_BY_CODE.get(code)
    if version is None:
        messages.error(request, "That version does not exist.")
        return redirect(target)
    status = services.get_status(code)
    if not status.available:
        messages.error(request, f"{version.label} is locked. {status.reason}")
        return redirect(target)
    response = redirect(target)
    response.set_cookie(
        VERSION_COOKIE, version.code, max_age=VERSION_COOKIE_MAX_AGE, samesite="Lax", httponly=True,
    )
    return response

@role_required(*ALL_ROLES)
@require_POST
def finish_chapter_view(request, book, chapter):
    """Count the chapter as read and move the bookmark on; "Next" (and "Finish" on Rev 22) post here.

    Next is the only way a chapter counts as read. ``after=next`` goes straight to
    the following chapter. The target is worked out here, never taken from the
    form, so there is no redirect to validate.
    """
    book = _book_or_404(book)
    if not 1 <= chapter <= book.chapters:
        raise Http404("Unknown chapter")
    version = _current_version(request)
    try:
        _, _, following = services.finish_chapter(request.user, version.code, book.osis_id, chapter)
    except BibleError as error:
        messages.error(request, str(error))
        return redirect("bible:chapter", book=book.osis_id, chapter=chapter)
    if request.POST.get("after") == "next" and following:
        return redirect("bible:chapter", book=following[0], chapter=following[1])
    if following is None:
        messages.success(request, "You have finished the whole Bible. Well done!")
    else:
        messages.info(request, f"Saved. Your bookmark is at {get_book(following[0]).name} {following[1]}.")
    return redirect("bible:chapter", book=book.osis_id, chapter=chapter)


def _is_background_request(request):
    """True for a request sent by the page's own JavaScript (fetch), not a form submit.

    The auto bookmark sends ``X-Requested-With: XMLHttpRequest`` (a JSON ``Accept``
    header counts too), so it gets a quiet reply instead of a redirect and a toast.
    """
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


@role_required(*ALL_ROLES)
@require_POST
def bookmark_view(request, book, chapter):
    """Put the bookmark on this chapter without counting it as read.

    The Bookmark button posts a normal form (message and redirect). The 20-second
    auto bookmark posts from JavaScript and gets a plain 204 with no message. A GET
    never writes (``require_POST``).
    """
    book = _book_or_404(book)
    if not 1 <= chapter <= book.chapters:
        raise Http404("Unknown chapter")
    services.save_bookmark(request.user, book.osis_id, chapter)
    if _is_background_request(request):
        return HttpResponse(status=204)
    messages.success(request, f"Bookmarked {book.name} {chapter}.")
    return redirect("bible:chapter", book=book.osis_id, chapter=chapter)
