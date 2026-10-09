"""Business logic for the public landing page.

`get_landing_context()` returns ONE dict that the template renders. Every block
has a default, so the page is complete before anyone has managed it. When an
admin fills `church_content`, `landing_image`, `donation_account`, the monthly
theme or the videos, the same template shows that content with no code change.

Where content comes from (managed content wins, the default is the fallback):
  hero intro     -> ChurchContent "landing_description"
  mission etc.   -> ChurchContent "mission", "vision", "core_values"
  photos         -> LandingImage (is_active, ordered by sort_order); none = DEFAULT_IMAGES
                    (used by the hero carousel and the "Come as you are" gallery)
  theme          -> resources.MonthlyTheme for the current Manila month (published, not archived)
  video          -> newest JilVideo
  giving         -> active DonationAccount rows (no default: nothing is invented)
  footer         -> FOOTER below (static; move it to `site_setting` when that is managed)

The default texts (hero intro, mission, vision, values, October 2026 theme, footer
contacts and links) were taken from the church's own landing page draft. Confirm
they are official before launch.
"""
from datetime import date

from django.utils import timezone
from django.utils.translation import get_language

DEFAULT_HERO = {
    "title": "Jesus Is Lord Church Worldwide",
    "intro": (
        "A church family committed to knowing God, growing in faith, "
        "serving others, and sharing the hope of Jesus Christ."
    ),
}

DEFAULT_ABOUT = {
    "mission": {
        "title": "Our Mission",
        "body": (
            "To lead people to a transforming relationship with God through His Word "
            "and to serve others with love, compassion, and humility."
        ),
    },
    "vision": {
        "title": "Our Vision",
        "body": (
            "To see people transformed by the grace of God, becoming faithful followers "
            "of Christ and bringing the hope of the Gospel to every community."
        ),
    },
    "values": {
        "title": "Our Core Values",
        "body": (
            "Reverence and love for God\nLove and care for one another\n"
            "Integrity and excellence\nFaithfulness in service\nCompassion for the community"
        ),
    },
}

# Shown when no published theme exists for the current month. It labels its own month,
# so it never claims to be a month it is not.
DEFAULT_THEME = {
    "month": date(2026, 10, 1),
    "title": "Breakthrough Joy That Strengthens",
    "description": (
        "This month, we are reminded to find strength and joy in the Lord, even while "
        "facing challenges. Let us continue to trust God, encourage one another, and "
        "move forward with faith."
    ),
    "url": "https://jilworldwide.org/theme/breakthrough-joy-that-strengthens/",
}

# Church photos shown (hero carousel and "Come as you are" gallery) until an admin uploads
# landing images. These are the church's own images from the landing page draft.
_IMG_BASE = "https://imagedelivery.net/HHwzmHgltKa2rO5RHhbhqQ"
DEFAULT_IMAGES = [
    {"url": f"{_IMG_BASE}/{image_id}/public", "caption": "Jesus Is Lord Church"}
    for image_id in (
        "5aa2eb7c-4113-4dc3-1b75-da495d75da00",
        "c49d15ea-5a65-41b3-1df2-d1c3c61a6f00",
        "bc4a6fd8-6ff6-42d9-0108-89537b9cb200",
        "8b283359-be1a-40ee-2757-70de1d3fa000",
        "8be2d580-6382-4d52-97af-91c8056c0e00",
    )
]

# Welcoming copy for the "Come as you are" section. It describes the spirit of the church
# and the app only; it makes no claims about size, history or schedules.
CHURCH_INTRO = {
    "title": "Come as you are. Grow as you go.",
    "body": (
        "Whether you are new to faith or have walked with God for years, there is a place "
        "for you here. We gather to worship, learn from God's Word, pray for one another, "
        "and serve our communities, and we would love for you to be part of it."
    ),
}
CHURCH_POINTS = [
    {"icon": "users", "title": "A family, not a crowd",
     "text": "Every person is welcome and every person is known. You were never meant to walk your faith alone."},
    {"icon": "book-open", "title": "Rooted in God's Word",
     "text": "Teaching and worship that point to Jesus Christ and help you grow, one day at a time."},
    {"icon": "hand-raised", "title": "A church that prays",
     "text": "Bring your needs to your coordinator and know that someone is standing with you."},
    {"icon": "fire", "title": "Faith you can live out",
     "text": "Set goals, build habits, and serve in ways that carry your worship into everyday life."},
]

# Fallback video when no JilVideo has been fetched yet.
DEFAULT_VIDEO_ID = "g-XZyLZRDPY"

# Product copy. `icon` must exist in static/icons/sprite.svg.
MEMBER_FEATURES = [
    {"icon": "fire", "title": "Faith goals and Bible streaks",
     "text": "Set goals, check in daily, and watch your Bible reading streak grow."},
    {"icon": "users", "title": "Accountability buddy",
     "text": "Partner with someone, see each other's goals, and send encouragement."},
    {"icon": "hand-raised", "title": "Prayer requests",
     "text": "Send a request to your coordinator, privately or anonymously, and read their reply."},
    {"icon": "calendar-days", "title": "Events and reminders",
     "text": "Know what is coming up, and get notified when a date or plan changes."},
    {"icon": "book-open", "title": "Worship resources",
     "text": "Find the monthly theme, the weekly presentation, and shared resources."},
]

SERVING_FEATURES = [
    {"icon": "clipboard-document-check", "title": "Attendance",
     "text": "Record who came, add first timers, and lock the sheet once it is uploaded."},
    {"icon": "banknotes", "title": "Tithes and offering",
     "text": "Count by denomination, see live totals, and finalize each record."},
    {"icon": "building-office-2", "title": "Extensions and members",
     "text": "Manage accounts and roles for your own extension, and nobody else's."},
    {"icon": "bell", "title": "Notifications",
     "text": "Get alerted when something needs your attention, then open the page to act on it."},
]

BUDDY_POINTS = [
    {"icon": "users", "title": "Stay connected",
     "text": "Keep in touch with your buddy and build a meaningful church friendship."},
    {"icon": "bell", "title": "Encourage one another",
     "text": "Share encouragement, reminders, and support during the week."},
    {"icon": "fire", "title": "Grow together",
     "text": "Pray, learn, and take steps of faith together as accountability buddies."},
]

# The long footer shown at the bottom of the landing page.
FOOTER = {
    "brand": "Jesus Is Lord Church Worldwide",
    "blurb": "A church family growing in faith, love, worship, prayer, and service.",
    "more": [
        {"label": "Copyright", "url": "#"},
        {"label": "Join Us", "url": "#"},
        {"label": "Privacy Policy", "url": "#"},  # placeholder until the page exists
    ],
    "affiliates": [
        {"label": "Light TV", "url": "#"},
        {"label": "JILCF", "url": "#"},
        {"label": "iCare", "url": "#"},
    ],
    "offices": [
        {"address": "101 MacArthur Highway, Bunlo, Bocaue, Bulacan 3018 Philippines",
         "phone": "+63 (044) 931 3063", "email": ""},
        {"address": "841 EDSA South Triangle, Quezon City, Metro Manila 1103 Philippines",
         "phone": "+63 2 8661 2858", "email": "info@jilworldwide.org"},
    ],
    "social": {
        "facebook": "https://www.facebook.com/jesusislordchurch",
        "x": "#",
        "instagram": "#",
        "youtube": "https://www.youtube.com/user/JILWorldwide",
    },
}


def _language_order():
    """Content languages to try, best first: the visitor's, then English, then Filipino."""
    current = (get_language() or "en").split("-")[0]
    current = "fil" if current == "tl" else current  # Django's Tagalog code
    order = []
    for code in (current, "en", "fil"):
        if code not in order:
            order.append(code)
    return order


def _content_block(key):
    """Return the church_content row for `key` in the best available language, or None."""
    from apps.pages.models import ChurchContent  # lazy: keeps imports one-way

    order = _language_order()
    rows = {r.language: r for r in ChurchContent.objects.filter(key=key, language__in=order)}
    for code in order:
        row = rows.get(code)
        if row is not None and row.body.strip():
            return row
    return None


def _hero():
    """Hero title and intro. The intro is the managed landing description when present."""
    row = _content_block("landing_description")
    return {
        "title": DEFAULT_HERO["title"],
        "intro": row.body if row else DEFAULT_HERO["intro"],
    }


def _hero_images():
    """Church photos as {url, caption} dicts: active landing images, else the default photos."""
    from apps.pages.models import LandingImage

    rows = LandingImage.objects.filter(is_active=True).order_by("sort_order", "id")
    images = [{"url": r.image.url, "caption": r.caption} for r in rows]
    return images or [dict(i) for i in DEFAULT_IMAGES]


def _about_block(key, default_key):
    """One of mission / vision / values as a {title, body} dict, managed or default."""
    default = DEFAULT_ABOUT[default_key]
    row = _content_block(key)
    if row is None:
        return dict(default)
    return {"title": row.title or default["title"], "body": row.body}


def _current_theme():
    """The published, non-archived theme for the current Manila month, else the default."""
    from apps.resources.models import MonthlyTheme

    first = timezone.localdate().replace(day=1)  # TIME_ZONE is Asia/Manila
    row = MonthlyTheme.objects.filter(
        month=first, is_published=True, archived_at__isnull=True
    ).first()
    if row is None:
        return dict(DEFAULT_THEME)
    return {"month": row.month, "title": row.title, "description": row.description, "url": None}


def _video_dict(video_id, title, thumbnail_url=""):
    """Shape a video for the template; builds the YouTube thumbnail when none is stored."""
    return {
        "youtube_video_id": video_id,
        "title": title,
        "thumbnail_url": thumbnail_url or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }


def _latest_video():
    """The newest video fetched by `fetch_jil_videos`, else the default video."""
    from apps.pages.models import JilVideo

    row = JilVideo.objects.order_by("-published_at").first()
    if row is None:
        return _video_dict(DEFAULT_VIDEO_ID, "Latest message")
    return _video_dict(row.youtube_video_id, row.title, row.thumbnail_url)


def _give_accounts():
    """Active giving instructions in the admin's order. Empty list = nothing published."""
    from apps.pages.models import DonationAccount

    return list(DonationAccount.objects.filter(is_active=True).order_by("sort_order", "id"))


def get_landing_context():
    """Everything the landing template needs, with a default for every block."""
    return {
        "hero": _hero(),
        "hero_images": _hero_images(),
        "about": {
            "mission": _about_block("mission", "mission"),
            "vision": _about_block("vision", "vision"),
            "values": _about_block("core_values", "values"),
        },
        "theme": _current_theme(),
        "video": _latest_video(),
        "give_accounts": _give_accounts(),
        "church_intro": CHURCH_INTRO,
        "church_points": CHURCH_POINTS,
        "member_features": MEMBER_FEATURES,
        "serving_features": SERVING_FEATURES,
        "buddy_points": BUDDY_POINTS,
        "footer": FOOTER,
    }