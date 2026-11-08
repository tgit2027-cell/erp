"""The HR Portal: what every member of staff sees, kept apart from the HR section (HR team only).

Tivoli's ask (from Sam, 5 Nov 2026): a portal for all staff with the procedures, memos and general
announcements, plus the letters each person asks for and downloads (salary certificate, bank loan
letters). It should grow later into a bigger staff portal: a suggestions box, events, photos.

Rules (synthetic demo only). Sam answered the first four on 5 Nov 2026; the rest are provisional:

- The portal is switched off until Sam says go (ERP_HR_PORTAL=on). While it is off, every portal
  address answers "not found" and the menu hides it.
- Everyone signed in sees the portal. A post is for one business unit or for the whole group, and
  people see the posts of the units they work in or have access to.
- Only the HR team members given "Post on the HR Portal" in a unit post there (Sam). A post for the
  whole group needs that permission in every business unit (the HR Admin and Master Admin levels have it).
- Posts go live at once, with no approval step (Sam). Each shows who posted it and when. Anyone allowed
  to post in that unit can take a post down, with a reason; nothing is deleted.
- A post can be marked "must read". Staff press "I have read this"; the posters see who has and who has
  not, and each confirmation is shown on the person's staff record (Sam).
- One picture (PNG or JPEG) or one PDF can be added to a post, up to 1.5 MB. An event also has a photo
  album (up to 30 photos of 1.5 MB each), added by the people who may post there.
- When a memo, announcement or procedure goes up, everyone it is for gets one email with a button that
  opens the portal (sent with the other alerts, every 15 minutes). Events are emailed too. Nobody gets the
  same post twice; people without an email address are skipped and recorded.
- Suggestions always carry the sender's name; there are no anonymous suggestions (Sam). They go to HR of
  the chosen unit, who mark them read and can answer.
"""

import base64
import datetime
import binascii
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access import active_grants, context_units, holders_filter
from .cash_accounts import _text
from .general_ledger import _day
from .leave import _me
from .models import (
    AlertEmail, AlertSent, BusinessUnit, DemoUserMarker, Employee, PortalPhoto, PortalPost, PortalRead, PortalSuggestion,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
K = PortalPost.Kind
P = PortalPost.Status
SG = PortalSuggestion.Status
POST_ACTIONS = (A.POST_PORTAL,)
MAX_FILE_BYTES = 1_500_000
MAX_PHOTOS = 30
FILE_TYPES = {"data:image/png;base64": ("png", "PNG"), "data:image/jpeg;base64": ("jpeg", "JPEG"),
              "data:application/pdf;base64": ("pdf", None)}
CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}
NEWS = (K.ANNOUNCEMENT, K.EVENT)
LIBRARY = (K.PROCEDURE, K.MEMO)
POLICY_LABEL = ("TEST ONLY rules: only the HR team given \"Post on the HR Portal\" posts; a post for the whole group needs it in "
                "every unit; posts go live at once and can be taken down with a reason; must-read posts are confirmed by each "
                "person and kept on their staff record; suggestions carry the sender's name and go to HR.")


def is_enabled():
    return bool(getattr(settings, "HR_PORTAL_ENABLED", False))


def _require_user(actor):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("The HR Portal runs only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=actor, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use the HR Portal.")
    if not is_enabled():
        raise WorkflowPermissionDenied("The HR Portal is not switched on yet.")


def is_portal_member(user):
    """Someone on the staff list who has not left: they may sign in to the portal even with no other access."""
    return Employee.objects.filter(user=user).exclude(status=Employee.Status.LEFT).exists() and \
        DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists()


def my_unit_ids(user):
    """The units someone belongs to: where they work, plus where they have any access."""
    ids = set(context_units(user).values_list("pk", flat=True))
    me = _me(user)
    if me is not None:
        ids.add(me.business_unit_id)
    return ids


def can_post(user, business_unit):
    if business_unit is None:
        return can_post_group(user)
    if getattr(user, "is_superuser", False):
        return True
    return active_grants(user).filter(business_unit=business_unit, action__in=POST_ACTIONS).exists()


def can_post_group(user):
    if getattr(user, "is_superuser", False):
        return True
    units = set(BusinessUnit.objects.filter(is_active=True, is_synthetic=True).values_list("pk", flat=True))
    held = set(active_grants(user).filter(action__in=POST_ACTIONS).values_list("business_unit_id", flat=True))
    return bool(units) and units <= held


def post_units(user):
    """Where this person may post."""
    units = BusinessUnit.objects.filter(is_active=True, is_synthetic=True).order_by("code")
    if not getattr(user, "is_superuser", False):
        held = active_grants(user).filter(action__in=POST_ACTIONS).values("business_unit_id")
        units = units.filter(pk__in=held)
    return list(units)


def can_read_suggestions(user, business_unit):
    if getattr(user, "is_superuser", False):
        return True
    return active_grants(user, A.MANAGE_STAFF).filter(business_unit=business_unit).exists()


def _visible_posts(user):
    return PortalPost.objects.filter(Q(business_unit__isnull=True) | Q(business_unit_id__in=my_unit_ids(user))) \
        .select_related("business_unit", "posted_by", "withdrawn_by")


def audience(post):
    """The people a post is for: everyone working in its unit (or the group) or with access there."""
    users = get_user_model().objects.filter(is_active=True, demo_marker__is_synthetic=True)
    staff = Employee.objects.exclude(status=Employee.Status.LEFT).exclude(user=None)
    if post.business_unit_id is not None:
        staff = staff.filter(business_unit_id=post.business_unit_id)
        return users.filter(Q(pk__in=staff.values("user_id")) | holders_filter(post.business_unit)).distinct()
    return users.filter(Q(pk__in=staff.values("user_id")) | holders_filter(None)).distinct()


def _row(post, user, read_ids=None, with_readers=False):
    managing = can_post(user, post.business_unit)
    read_ids = read_ids if read_ids is not None else set(PortalRead.objects.filter(user=user).values_list("post_id", flat=True))
    data = {
        "id": post.pk, "kind": post.kind, "kind_label": post.get_kind_display(), "title": post.title, "body": post.body,
        "unit": {"id": post.business_unit_id, "name": post.business_unit.name} if post.business_unit_id else None,
        "for_label": post.business_unit.name if post.business_unit_id else "Everyone in the group",
        "must_read": post.must_read, "pinned": post.pinned, "read_by_me": post.pk in read_ids,
        "event_on": post.event_on.isoformat() if post.event_on else None, "event_time": post.event_time,
        "event_place": post.event_place,
        "file": {"name": post.file_name, "type": post.file_type} if post.file_type else None,
        "status": post.status, "status_label": post.get_status_display(),
        "posted_by": _name(post.posted_by), "posted_at": post.posted_at.isoformat(),
        "withdrawn_by": _name(post.withdrawn_by), "withdrawn_at": post.withdrawn_at.isoformat() if post.withdrawn_at else None,
        "withdrawn_reason": post.withdrawn_reason, "can_manage": managing,
        "photos": [{"id": photo.pk, "caption": photo.caption} for photo in post.photos.all()] if post.kind == K.EVENT else [],
    }
    if managing and post.must_read:
        people = list(audience(post).exclude(pk=post.posted_by_id).order_by("first_name", "last_name", "username"))
        reads = {row.user_id: row.read_at for row in PortalRead.objects.filter(post=post)}
        data["reads"] = {"done": sum(1 for person in people if person.pk in reads), "total": len(people)}
        if with_readers:
            data["readers"] = [{"name": _name(person), "read_at": reads[person.pk].isoformat() if person.pk in reads else None}
                               for person in people]
    return data


def portal_overview(*, actor, section="home"):
    """The portal page: home (news and what to read), news, or the library of procedures and memos."""
    _require_user(actor)
    if section not in ("home", "news", "library", "photos", "manage"):
        raise InvalidTransaction("Choose home, news, library, photos or manage.")
    posts = _visible_posts(actor)
    managed = {unit.pk for unit in post_units(actor)}
    group_ok = can_post_group(actor)
    # Taken-down posts stay visible only to the people who may post there.
    posts = posts.filter(Q(status=P.PUBLISHED) | Q(business_unit_id__in=managed) | (Q(business_unit__isnull=True) if group_ok
                                                                                     else Q(pk__in=[])))
    read_ids = set(PortalRead.objects.filter(user=actor).values_list("post_id", flat=True))
    live = posts.filter(status=P.PUBLISHED)
    to_read = [post for post in live.filter(must_read=True).order_by("posted_at") if post.pk not in read_ids]
    today = timezone.localdate()
    if section == "home":
        shown = list(live.filter(kind=K.ANNOUNCEMENT)[:5])
    elif section == "news":
        shown = list(posts.filter(kind__in=NEWS)[:100])
    elif section == "manage":
        # HR's list in the HR section: every post where this person may post, taken-down ones included.
        shown = list(posts.filter(Q(business_unit_id__in=managed) | (Q(business_unit__isnull=True) if group_ok else Q(pk__in=[])))[:200])
    elif section == "photos":
        shown = list(live.filter(kind=K.EVENT, photos__isnull=False).distinct().order_by("-event_on", "-pk")[:50])
    else:
        shown = list(posts.filter(kind__in=LIBRARY)[:200])
    upcoming = list(live.filter(kind=K.EVENT, event_on__gte=today).order_by("event_on", "pk")[:5])
    album = live.filter(kind=K.EVENT, photos__isnull=False).distinct().order_by("-event_on", "-pk").first()
    me = _me(actor)
    return {
        "section": section,
        "me": {"name": me.full_name, "unit": me.business_unit.name} if me else {"name": _name(actor), "unit": ""},
        "posts": [_row(post, actor, read_ids, with_readers=False) for post in shown],
        "to_read": [_row(post, actor, read_ids) for post in to_read],
        "upcoming": [_row(post, actor, read_ids) for post in upcoming],
        "latest_album": _row(album, actor, read_ids) if album and section == "home" else None,
        "post_units": [{"id": unit.pk, "name": unit.name} for unit in post_units(actor)],
        "can_post_group": group_ok,
        "kinds": [{"value": value, "label": label} for value, label in K.choices],
        "policy": POLICY_LABEL,
    }


def _file(value, pictures_only=False):
    if value in (None, ""):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("data"), str):
        raise InvalidTransaction("Send the file as a picture or a PDF.")
    head, _, body = value["data"].partition(",")
    if head not in FILE_TYPES or not body:
        raise InvalidTransaction("Add a PNG or JPEG picture, or a PDF.")
    kind, image_format = FILE_TYPES[head]
    if pictures_only and not image_format:
        raise InvalidTransaction("Add a PNG or JPEG photo.")
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidTransaction("The file could not be read.") from error
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidTransaction("The file is too large. Use one under 1.5 MB.")
    if image_format:
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != image_format:
                    raise InvalidTransaction("The picture is not the type it says it is.")
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError) as error:
            raise InvalidTransaction("The picture could not be read.") from error
    elif not raw.startswith(b"%PDF-"):
        raise InvalidTransaction("The PDF could not be read.")
    name = _text(value.get("name"), "file name", 120) or f"attachment.{kind}"
    return name, kind, raw


@transaction.atomic
def create_post(*, actor, data):
    _require_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the post.")
    kind = data.get("kind")
    if kind not in K.values:
        raise InvalidTransaction("Choose announcement, memo, procedure or event.")
    unit = None
    if data.get("unit_id") not in (None, ""):
        unit = BusinessUnit.objects.filter(pk=data.get("unit_id") if type(data.get("unit_id")) is int else None,
                                           is_active=True, is_synthetic=True).first()
        if unit is None:
            raise InvalidTransaction("Choose a business unit, or the whole group.")
    if not can_post(actor, unit):
        raise WorkflowPermissionDenied("Only the HR team given \"Post on the HR Portal\" can post here."
                                       if unit else "A post for the whole group needs the right to post in every business unit.")
    title = _text(data.get("title"), "title", 150, required=True)
    body = data.get("body") if isinstance(data.get("body"), str) else ""
    body = body.strip()
    if not body:
        raise InvalidTransaction("Write what the post says.")
    if len(body) > 8000:
        raise InvalidTransaction("The text can be at most 8000 characters.")
    values = {"business_unit": unit, "kind": kind, "title": title, "body": body,
              "must_read": data.get("must_read") is True and kind != K.EVENT, "pinned": data.get("pinned") is True,
              "posted_by": actor}
    if kind == K.EVENT:
        if not data.get("event_on"):
            raise InvalidTransaction("Give the date of the event.")
        values["event_on"] = _day(data.get("event_on"), timezone.localdate(), "event date")
        values["event_time"] = _text(data.get("event_time"), "time", 40)
        values["event_place"] = _text(data.get("event_place"), "place", 120)
    attached = _file(data.get("file"))
    if attached:
        values["file_name"], values["file_type"], values["file_data"] = attached
    post = PortalPost.objects.create(**values)
    return _row(post, actor)


def _post_for(actor, post_id):
    post = _visible_posts(actor).filter(pk=post_id if type(post_id) is int else None).first()
    if post is None or (post.status != P.PUBLISHED and not can_post(actor, post.business_unit)):
        raise InvalidTransaction("That post is not on the portal.")
    return post


def post_detail(*, actor, post_id):
    _require_user(actor)
    return _row(_post_for(actor, post_id), actor, with_readers=True)


@transaction.atomic
def act_on_post(*, actor, post_id, action, reason="", photo=None, photo_id=None):
    """Confirm reading, pin or unpin, take a post down, or add or remove an event photo."""
    _require_user(actor)
    post = _post_for(actor, post_id)
    PortalPost.objects.select_for_update().filter(pk=post.pk).first()
    if action == "read":
        if post.status != P.PUBLISHED:
            raise InvalidTransaction("That post has been taken down.")
        PortalRead.objects.get_or_create(post=post, user=actor)
        return _row(post, actor)
    if not can_post(actor, post.business_unit):
        raise WorkflowPermissionDenied("Only the people who may post here can change this post.")
    if action in ("pin", "unpin"):
        if post.status != P.PUBLISHED:
            raise InvalidTransaction("That post has been taken down.")
        post.pinned = action == "pin"
        post.save(update_fields=["pinned"])
        return _row(post, actor)
    if action == "withdraw":
        if post.status != P.PUBLISHED:
            raise InvalidTransaction("That post is already taken down.")
        why = _text(reason, "reason", 200, required=True)
        post.status, post.withdrawn_by, post.withdrawn_at, post.withdrawn_reason = P.WITHDRAWN, actor, timezone.now(), why
        post.pinned = False
        post.save(update_fields=["status", "withdrawn_by", "withdrawn_at", "withdrawn_reason", "pinned"])
        return _row(post, actor)
    if action == "add_photo":
        if post.kind != K.EVENT or post.status != P.PUBLISHED:
            raise InvalidTransaction("Photos are added to events on the portal.")
        if post.photos.count() >= MAX_PHOTOS:
            raise InvalidTransaction(f"An event can have at most {MAX_PHOTOS} photos.")
        attached = _file(photo, pictures_only=True) if photo else None
        if attached is None:
            raise InvalidTransaction("Add a PNG or JPEG photo.")
        PortalPhoto.objects.create(post=post, image=attached[2], image_type=attached[1], added_by=actor,
                                   caption=_text(photo.get("caption"), "caption", 150))
        return _row(post, actor)
    if action == "remove_photo":
        removed, _ = PortalPhoto.objects.filter(post=post, pk=photo_id if type(photo_id) is int else None).delete()
        if not removed:
            raise InvalidTransaction("That photo is not in this album.")
        return _row(post, actor)
    raise InvalidTransaction("Choose read, pin, unpin, withdraw, add_photo or remove_photo.")


def post_file(*, actor, post_id):
    _require_user(actor)
    post = _post_for(actor, post_id)
    if not post.file_type:
        raise InvalidTransaction("That post has no file.")
    return post.file_name, CONTENT_TYPES[post.file_type], bytes(post.file_data)


def photo_file(*, actor, post_id, photo_id):
    _require_user(actor)
    post = _post_for(actor, post_id)
    photo = PortalPhoto.objects.filter(post=post, pk=photo_id if type(photo_id) is int else None).first()
    if photo is None:
        raise InvalidTransaction("That photo is not in this album.")
    return f"photo-{photo.pk}.{'jpg' if photo.image_type == 'jpeg' else 'png'}", CONTENT_TYPES[photo.image_type], bytes(photo.image)


def _suggestion_row(row, actor, inbox=False):
    data = {"id": row.pk, "unit": row.business_unit.name, "topic": row.topic, "topic_label": row.get_topic_display(),
            "text": row.text, "sent_at": row.sent_at.isoformat(),
            "status": row.status, "status_label": row.get_status_display(), "reply": row.reply,
            "replied_by": _name(row.replied_by), "replied_at": row.replied_at.isoformat() if row.replied_at else None}
    if inbox:
        data["sender"] = _name(row.sender)
    return data


def suggestions_overview(*, actor):
    _require_user(actor)
    mine = PortalSuggestion.objects.filter(sender=actor).select_related("business_unit", "replied_by")[:50]
    units = BusinessUnit.objects.filter(pk__in=my_unit_ids(actor), is_active=True).order_by("code")
    inbox_units = [unit for unit in BusinessUnit.objects.filter(is_active=True, is_synthetic=True).order_by("code")
                   if can_read_suggestions(actor, unit)]
    inbox = PortalSuggestion.objects.filter(business_unit__in=inbox_units) \
        .select_related("business_unit", "sender", "replied_by")[:200]
    me = _me(actor)
    return {
        "units": [{"id": unit.pk, "name": unit.name} for unit in units],
        "default_unit": me.business_unit_id if me else (units[0].pk if units else None),
        "topics": [{"value": value, "label": label} for value, label in PortalSuggestion.Topic.choices],
        "mine": [_suggestion_row(row, actor) for row in mine],
        "inbox": [_suggestion_row(row, actor, inbox=True) for row in inbox],
        "can_read": bool(inbox_units),
    }


@transaction.atomic
def send_suggestion(*, actor, data):
    _require_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the suggestion.")
    unit_id = data.get("unit_id")
    if type(unit_id) is not int or unit_id not in my_unit_ids(actor):
        raise InvalidTransaction("Choose the business unit the suggestion is for.")
    unit = BusinessUnit.objects.get(pk=unit_id)
    topic = data.get("topic") or PortalSuggestion.Topic.IDEA
    if topic not in PortalSuggestion.Topic.values:
        raise InvalidTransaction("Choose what the suggestion is about.")
    text = data.get("text").strip() if isinstance(data.get("text"), str) else ""
    if not text:
        raise InvalidTransaction("Write your suggestion.")
    if len(text) > 2000:
        raise InvalidTransaction("A suggestion can be at most 2000 characters.")
    row = PortalSuggestion.objects.create(business_unit=unit, sender=actor, topic=topic, text=text)
    return _suggestion_row(row, actor)


@transaction.atomic
def answer_suggestion(*, actor, suggestion_id, action, reply=""):
    _require_user(actor)
    row = PortalSuggestion.objects.select_for_update().filter(pk=suggestion_id if type(suggestion_id) is int else None).first()
    if row is None or not can_read_suggestions(actor, row.business_unit):
        raise WorkflowPermissionDenied("Only HR of that business unit reads its suggestions.")
    if action == "seen":
        if row.status == SG.NEW:
            row.status = SG.SEEN
            row.save(update_fields=["status"])
    elif action == "answer":
        if row.status == SG.CLOSED:
            raise InvalidTransaction("That suggestion is already answered.")
        row.reply = _text(reply, "answer", 1000, required=True)
        row.status, row.replied_by, row.replied_at = SG.CLOSED, actor, timezone.now()
        row.save(update_fields=["reply", "status", "replied_by", "replied_at"])
    else:
        raise InvalidTransaction("Choose seen or answer.")
    return _suggestion_row(row, actor, inbox=True)


def reads_for_employee(employee):
    """The must-read posts this person has confirmed, for their staff record."""
    if employee.user_id is None:
        return []
    rows = PortalRead.objects.filter(user_id=employee.user_id).select_related("post").order_by("-read_at", "-pk")[:200]
    return [{"post_id": row.post_id, "title": row.post.title, "kind_label": row.post.get_kind_display(),
             "read_at": row.read_at.isoformat()} for row in rows]


PAGE = {K.ANNOUNCEMENT: "news", K.EVENT: "news", K.MEMO: "library", K.PROCEDURE: "library"}


def send_portal_emails():
    """Email each new post once to everyone it is for, with a button-style link that opens the portal."""
    if not is_enabled() or getattr(settings, "APP_ENV", None) != "demo":
        return []
    emails = []
    since = timezone.now() - datetime.timedelta(days=14)
    for post in PortalPost.objects.filter(status=P.PUBLISHED, posted_at__gte=since).select_related("business_unit"):
        key = f"portal:{post.pk}"
        done = set(AlertSent.objects.filter(key=key).values_list("user_id", flat=True))
        link = f"{settings.ERP_BASE_URL}/#/portal/{PAGE[post.kind]}"
        for person in audience(post).exclude(pk__in=done).exclude(pk=post.posted_by_id):
            name = person.get_full_name() or person.username
            subject = f"HR Portal: new {post.get_kind_display().lower()}: {post.title}"[:200]
            lines = [f"Hello {name},", "", f"HR has posted a new {post.get_kind_display().lower()} for "
                     f"{post.business_unit.name if post.business_unit_id else 'everyone in the group'}:", "", post.title]
            if post.kind == K.EVENT:
                lines.append(f"When: {post.event_on:%d %B %Y}{' ' + post.event_time if post.event_time else ''}"
                             f"{' at ' + post.event_place if post.event_place else ''}")
            if post.must_read:
                lines.append("Please read it and press \"I have read this\".")
            lines += ["", f"Open the HR Portal: {link}", "",
                      "This is a synthetic test email from the Tivoli ERP demo."]
            status, error = AlertEmail.Status.SENT, ""
            if not person.email:
                status = AlertEmail.Status.NO_EMAIL
            else:
                try:
                    send_mail(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [person.email])
                except Exception as failure:  # noqa: BLE001 - a mail server problem must not stop the other emails
                    status, error = AlertEmail.Status.FAILED, str(failure)[:300]
            with transaction.atomic():
                email = AlertEmail.objects.create(kind=AlertEmail.Kind.PORTAL, user=person, to_address=person.email or "",
                                                  subject=subject, body="\n".join(lines),
                                                  items=[{"key": key, "title": post.title, "link": f"#/portal/{PAGE[post.kind]}"}],
                                                  status=status, error=error)
                if status != AlertEmail.Status.FAILED:
                    AlertSent.objects.get_or_create(user=person, key=key, defaults={"email": email})
            emails.append(email)
    return emails


@transaction.atomic
def give_portal_sign_in(*, actor, business_unit, employee_id, username="", password=""):
    """HR gives a member of staff their own sign-in. It opens only the HR Portal: it carries no permissions at all.

    Sam (5 Nov 2026): every employee uses their own sign-in. Giving permissions stays with the admins (setup lock).
    """
    from .staff_records import _load, can_manage
    from .user_admin import _set_password

    _require_user(actor)
    if business_unit is None or not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("Only HR (Add and change staff records) gives staff their portal sign-in.")
    employee = _load(business_unit, employee_id, lock=True)
    if employee.user_id is not None:
        raise InvalidTransaction("This person already has a sign-in.")
    if employee.status == Employee.Status.LEFT:
        raise InvalidTransaction("This person has left.")
    username = (username or employee.work_email or "").strip()
    if not username or len(username) > 150 or not all(ch.isalnum() or ch in "@.+-_" for ch in username):
        raise InvalidTransaction("Give a username of letters, digits and @ . + - _ only, such as the work email.")
    if get_user_model().objects.filter(username__iexact=username).exists():
        raise InvalidTransaction(f"The username {username} is already taken.")
    first, _, last = employee.full_name.partition(" ")
    person = get_user_model()(username=username, first_name=first[:150], last_name=last[:150], email=employee.work_email)
    _set_password(person, password)
    person.save()
    DemoUserMarker.objects.create(user=person)
    employee.user = person
    employee.save(update_fields=["user"])
    return {"username": person.username, "employee_id": employee.pk, "portal_only": True}


@transaction.atomic
def reset_portal_password(*, actor, business_unit, employee_id, password=""):
    """HR sets a new temporary password, only for a sign-in that opens nothing but the portal."""
    from .staff_records import _load, can_manage
    from .user_admin import _set_password

    _require_user(actor)
    if business_unit is None or not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("Only HR (Add and change staff records) resets portal passwords.")
    employee = _load(business_unit, employee_id, lock=True)
    person = employee.user
    if person is None:
        raise InvalidTransaction("This person has no sign-in yet.")
    if person.is_staff or person.is_superuser or active_grants(person).exists():
        raise WorkflowPermissionDenied("This sign-in opens more than the portal; an admin resets it in Users and permissions.")
    _set_password(person, password)
    person.save(update_fields=["password"])
    return {"done": True}


@transaction.atomic
def change_my_password(*, actor, current="", new=""):
    """Anyone signed in changes their own password, after giving the current one."""
    from .user_admin import _set_password

    _require_user(actor)
    if not isinstance(current, str) or not actor.check_password(current):
        raise InvalidTransaction("The current password is not right.")
    if current == new:
        raise InvalidTransaction("Choose a password different from the current one.")
    person = get_user_model().objects.select_for_update().get(pk=actor.pk)
    _set_password(person, new)
    person.save(update_fields=["password"])
    actor.password = person.password
    return person
