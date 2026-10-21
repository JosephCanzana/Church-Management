# Tithes doc snippets (Brick 4)

Ready to paste. Nothing here was applied to `AGENTS.md` or `README.md`.
The "Not built yet" bullet and the apps-table row were not in the upload, so (b) gives replacement wording
to match against whatever text is there today.

---

## (a) New section for `AGENTS.md`

```markdown
## Tithes

Tithes and offering for one attendance sheet: the digital "Reports on Tithes & Offering" paper form.
Code: `apps/tithes/` (`models.py`, `permissions.py`, `services.py`, `views.py`, `forms.py`, `area_urls.py`,
`urls.py`, templates in `templates/tithes/`, tests in `tests/`). `tithes` depends on `attendance`;
`accounts`, `audit` and `notifications` never import it.

### Models (all money is `Decimal(12,2)`, never float)

- `tithes_offering` (`TithesOffering`): ONE per attendance sheet (two services in a day = two reports).
  `status` draft/finalized, `offering_amount`, `denomination_breakdown` (JSON, nine text keys 1000..1).
  Frozen at finalize and NULL while a draft: `tithers_count`, `tithes_total`, `total_amount`,
  `breakdown_total`, `difference` (SIGNED, breakdown_total - total_amount), `finalized_by`, `finalized_at`,
  `bypass_reason` (only for a DIRECT admin bypass), `bypassed_by`. Every `*_by` is SET NULL.
- `tithe_entry` (`TitheEntry`): one envelope. `user` (SET NULL) + `giver_name` snapshot, `amount` > 0,
  unique(report, user).
- `tithe_entry_giver` (`TitheEntryGiver`): co-givers copied onto an entry (name snapshot, unique(entry, user)).
- `tithe_household` / `tithe_household_member`: a standing, dated household link. It ONLY pre-fills the
  co-givers of a new entry; saving copies them onto the entry, so later changes never rewrite history.
  One active membership per person (partial unique index).
- `tithes_bypass_request`: the coordinator's request to finalize a mismatch. `message`, signed `difference`
  as requested, `status` (pending, approved, rejected, withdrawn, voided, used), `decided_by`, `decided_at`,
  `decision_note`. At most one OPEN (pending or approved) request per report.

### Who may do what

| Action | Tithes special member (seat) | Coordinator (own extension) | Admin / super-admin |
| --- | --- | --- | --- |
| View / edit draft | Drafts of the extension where they hold the seat | Yes | Yes |
| Request bypass / withdraw it | No | **Only the coordinator** | No |
| Approve / reject a request | No | No | Yes |
| Finalize | No | Yes (balanced, or with an approved request) | Yes (may bypass directly with a reason) |
| Reopen | No | No | Yes |
| Manage households | No | Yes | Yes |
| Print | Drafts only | Yes | Yes |
| My tithes (own history) | Yes | Yes | Yes |

"Own extension" is the extension of the report's SHEET. `permissions.py` is the only place that decides;
`TithesOffering.objects.for_user(actor)` is the only scoping (views start from it, so an unreachable report
looks exactly like a missing one). A draft is LOCKED for special members and coordinators while a request is
open; only admin and super-admin can still edit it. A report whose sheet is archived is read-only.

### Services (`services.py`)

Each action runs in `transaction.atomic()`, locks the report row AND its sheet row, re-checks permission on
the LOCKED row (`require(can_..., actor, report)`) and refuses with `ServiceError` unless the report is a draft.
`build_totals(report)` is the ONE function for the live numbers and for the freeze; `frozen_totals_for(report)`
reads the stored numbers back. Working data (offering, entries, breakdown, households) is not audited.
`member_tithe_history(user)`, `entry_names(entry)` and `bypass_note_for(report)` are read-only helpers used by
My tithes and the print page.

### Finalize and bypass order

`finalize_report` (one transaction, one-time token consumed first):
1. consume the token (a replay returns `duplicate=True`);
2. lock the report, require a draft on an open sheet and `can_finalize`;
3. refuse an empty report, then `build_totals`;
4. if the difference is not 0: an APPROVED request with the SAME stored difference (it becomes `used`), or an
   admin with a typed `bypass_reason`; otherwise refuse with how much is short or over;
5. write the frozen numbers, `finalized_by`, `finalized_at`, status; void any other open request;
6. log `tithes.finalize` (tither count and `balanced` flag only).

`request_bypass` (coordinator only, difference not 0, no open request) -> `decide_bypass` (admin approves or
rejects; if the difference changed meanwhile the request is VOIDED and nothing is decided) -> `finalize_report`
uses the approval once. Any draft edit that changes the difference voids an open request (`_after_edit`).
`reopen_report` (admin only) clears every frozen field; entries, offering and breakdown stay.

### Screens and routes

One set of views serves four places (`superadmin`, `admin_area`, `coordinator_area`, and `tithes` for special
members). Routes are written ONCE in `area_urls.py` and mounted four times; `urls.py` adds the member-root-only
route. Guards: `tithes_screen` (login, right area, `can_use_area`, never cached), then `for_user`, then the
service re-check.

- `""` list, `requests/` pending requests, `households/...`, `sheets/<id>/start/`
- `<id>/` workspace (editable draft) or review, `<id>/review/`, `<id>/print/`
- JSON helpers `<id>/givers/`, `<id>/givers/prefill/`
- POST actions: `offering/`, `breakdown/`, `entries/add|update|remove/`, `bypass/request|withdraw|decide/`,
  `finalize/`, `reopen/`
- `mine/` ONLY in `tithes/urls.py` as `tithes:mine` (see My tithes)

### Print page

`report_print` (`<id>/print/`, template `tithes/print.html`) is a read-only GET that changes and logs nothing;
`can_print` has the same rule as `can_view` (a special member prints drafts only). It is STANDALONE: it does
not extend `base_app.html`, black text on white in any mode, inline CSS, no external fonts, no Tailwind, no
`includes/logo.html` (`images/black-logo.svg`). `@page { size: A4; margin: 12mm }`, `thead` repeats,
`tr { break-inside: avoid }`, no page numbers. "Save as PDF" is the browser's print destination; there is no
server-side PDF library. A finalized report prints its FROZEN numbers; a draft counts live and prints
"DRAFT, not finalized". A shared envelope prints once ("Juan Cruz & Maria Cruz") with its full amount. A
mismatch that was let through prints "Finalized with a difference of ..., approved by ..., reason ...".

### My tithes

`my_tithes` (`tithes:mine`, template `tithes/mine.html`): the signed-in person's own giving history, every role,
no seat needed. FINALIZED reports only (a draft, a reopened report and a report on an archived sheet are
excluded), entries where the person is the giver OR a co-giver, newest first, with "Shared with <names>" and
the FULL amount (never split). It reads `request.user` and nothing else: no person id from the URL, query string
or form. It skips `tithes_screen` on purpose and lives in `urls.py` only. Reached from the "Tithes" tab of the
"My journey" hub (`SECTION_TABS["journey"]`).

### Dashboards

`dashboards/services._scope_tithes(user)` is the one scoping helper: finalized reports on uploaded sheets, through
`for_user` (a coordinator sees only their own extension). The trend reads the FROZEN `tithes_total` and
`offering_amount`, never a recount, and adds reports on the same date together. Amounts use `CURRENCY` and appear
only on leader dashboards. Context keys: `tithe_bars` (tithes) and `offering_bars` (offering). The dashboard writes nothing.

### Privacy

Never write a tithe amount, bypass message, decision note or bypass reason to `audit_log` or
`notification.data`. Audit data is ids, counts and the `balanced` flag. Notifications say THAT something
happened; the linked page does the permission check. The message and the note live only on the request row.
The print page may show a request's message as the "reason" (only people who may print a finalized report
reach it: coordinator and admins) but never the decision note. Screens and print pages show names and amounts
only: never a birth date, phone number or address.

### Tests (`apps/tithes/tests/`, `apps/dashboards/tests/`)

Package, not `tests.py`. Test users: `must_change_password=False`, `person_type="member_account"`. Builders in
`tithes/tests/support.py` go through services only. `test_print` (draft, frozen numbers, shared entry, no
birth date/phone, direct and approved bypass notes, access), `test_history` (own entries only, co-giver sees the
shared one at full amount, draft/reopened/archived excluded, no id from the request), dashboards
`test_tithes_scope` (coordinator scoping, drafts and reopened reports out, frozen same-date sums).
Run: `docker compose exec web python manage.py test apps.tithes apps.dashboards`.
```

---

## (b) Replacement lines

**Apps table, `tithes` row.** Replace the existing description with:

```markdown
| `tithes` | Tithes and offering per attendance sheet: draft report, entries (shared envelopes), bills-and-coins breakdown, bypass requests, finalize/reopen, A4 print page, "My tithes" history, households | `accounts`, `attendance`, `audit`, `notifications` |
```

(Keep your table's column order and the dependency column style if it differs.)

**"Not built yet" bullet.** Delete the tithes mention from that bullet. If tithes was its own bullet, remove it.
If you want a replacement that records what is still out of scope:

```markdown
- Tithes: in-app "verified by" confirmations, receipts and yearly giving statements are not built (the print page has blank signature lines).
```

---

## (c) `README.md` lines

```markdown
- **Tithes and offering**: record the offering and each person's tithe per service, count the bills and coins,
  and finalize when the cash matches the total (or an admin approves the difference).
- **Print or save as PDF**: every report has an A4 page that copies the paper form. Choose "Save as PDF" in the
  browser's print dialog.
- **My tithes**: every member can see their own giving history (finalized reports only). Shared envelopes show
  the full amount and who it was shared with.
- Tests: `docker compose exec web python manage.py test apps.tithes apps.dashboards`
```

---

## (d) Footguns found

1. **`attendance.services.edit_first_timer` will raise `NameError`.** It calls `permissions.can_edit_draft(...)`
   but the module only imports individual names (`from .permissions import can_edit_draft, ...`); `permissions`
   itself is not imported. `sheet_first_timer_edit` calls it, so that button fails at runtime. Fix: use
   `can_edit_draft(actor, locked)` (already imported). Not touched, outside this brick.
2. **Sidebar "Tithes" lights up on My tithes for seat holders.** `NavItem("Tithes", "tithes:report_list", roles=(MEMBER,))`
   resolves to `/tithes/`, and `_is_active` uses `path.startswith(href)`, so `/tithes/mine/` marks it active
   next to the "My journey" hub. `exact=True` would stop that but also unlight it on `/tithes/<id>/`. Left as is.
3. **"My tithes" is a hub tab, not a `NAV_ITEMS` line.** The brick text says `NAV_ITEMS`, but `attendance:mine`
   already lives as a `TabItem` in `SECTION_TABS["journey"]`, so the tithes one follows it (it replaced the
   member-only `tithes:report_list` tab). Consequence: `_quick_links` (built from `NAV_ITEMS`) does not show a
   My tithes shortcut, same as My attendance.
4. **`_report_url` is hardcoded to `/tithes/<id>/`.** Notifications link there; `tithes_screen` forwards a
   wrong-area visit of `report_detail` to the reader's own area. Any rename of that route breaks old notifications.
5. **Archived sheet: print opens, review does not.** `can_print` is `can_view`, but `can_review` also needs an
   open sheet. The print page's "Back" link then points at the workspace route, which redirects to the list with a
   toast. Harmless; a finalized report on an archived sheet is also left out of My tithes and the dashboards.
6. **The dashboard template needs a hook for `offering_bars`.** `_leader_overview` now returns it next to
   `tithe_bars`. The leader-overview templates were not in the upload, so nothing renders it yet. `tithe_bars`
   changed meaning: it is now the frozen TITHES total (it used to be `total_amount`, tithes plus offering).
7. **`dashboards/services._scope_tithes` changed signature** from `(queryset, user)` to `(user)` (the old one was
   unused and filtered by hand). Nothing else called it in the upload; grep before merging.
8. **Bypass note lookup is keyed on the frozen difference.** `bypass_note_for` shows the latest USED request whose
   stored difference equals the report's. A direct admin bypass wins when `bypass_reason` is set. After a reopen
   and a fresh finalize with a different difference the old request cannot match, so no stale note prints.
9. **Duplicate block in `attendance.views._workspace_context`:** `_returning_first_timers(...)` and the loop
   under it appear twice. Harmless (one extra query), worth deleting.
10. **Templates not in the upload were written from the pattern, not checked:** `mine.html` extends
    `base_app.html` with `{% block title %}` and `{% block content %}` and uses guessed utility classes
    (`stack`, `card`). Adjust to match `attendance/mine.html`.
11. **Review page button (`tithes/report_review.html`, not in the upload).** `_report_context` now supplies
    `can_print`. Add near the other actions (the `role_url` argument form is assumed from the attendance pages):

    ```django
    {% if can_print %}
      <a class="btn" href="{% role_url 'report_print' report.pk %}" target="_blank" rel="noopener">Print or save as PDF</a>
    {% endif %}
    ```

12. **Not run.** There is no Django, PostgreSQL or Docker in this workspace, so `check`, `makemigrations --check`
    and `test apps.tithes apps.dashboards` have NOT been run. The tests assume `User.objects.create_user` accepts
    the keyword arguments used in `tests/support.py`, that the `tithes_offering` special role row exists in the
    test database, that `Extension` needs only `name`, and that `consume_submission_token` accepts a token from
    `new_submission_token()` outside a request.
