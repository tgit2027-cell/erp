# Internal chat (first version)

Staff message each other inside the ERP instead of Microsoft Teams. Chat uses the
ERP sign-in, so it needs no Teams licence.

## What it does

- **Messages** in the sidebar, with a red count of unread messages (checked every 20 seconds).
- **Direct chats** between two people. Two people always share one chat.
- **Group chats** with a name. Any member can add people or rename the group; the person who
  started it can remove members; anyone can leave. Someone added later sees messages from the
  moment they joined, not the earlier history. Someone who leaves loses access.
- An open chat checks for new messages every 4 seconds while the page is visible.
- Enter sends, Shift+Enter starts a new line. Messages are limited to 4,000 characters.
- Works on phones: the list and the open chat show one at a time.

## Business rules

Decided by the business owner (31 Oct 2026):

- Sign-in stays on the agreed Microsoft account with MFA for now; this may change later.
- No voice or video calls.
- Chats are private. No screen or API lets anyone, including administrators, read other
  people's chats. Message text is not written to the audit log; the audit log records only that
  a chat action happened, by whom and when.
- Every active ERP user can message every other ERP user, across all companies and units.
- Messages are kept for **3 years**. `python manage.py purge_old_chat_messages` deletes older ones
  (`--dry-run` shows the count first). It should run daily once hosting is set up.

Provisional (default chosen to keep work moving, open for review):

- Any group member can add people; only the group's starter can remove others.

## Sharing files

- The 📎 button shares up to 5 files per message, 25 MB each: PDF, Word, Excel, PowerPoint
  (new and old formats), pictures (PNG, JPG, GIF, WEBP), text and CSV. Anything else, such as
  programs or scripts, is refused, and a file whose content does not match its type is refused too.
- Pictures show as a preview in the chat; everything else downloads. Downloads only work for people
  in that chat (someone added to a group later cannot open files shared before they joined).
- Files are stored under random names in the ERP's protected file storage (`DJANGO_MEDIA_ROOT/chat/`),
  never served directly, with a SHA-256 fingerprint to prove they were not changed.
- **Kept 3 years**, like messages: `purge_old_chat_messages` removes old files from storage too.
- **Backups**: the existing backup bundle (`scripts/backup_local_demo.sh`, see
  PORTABILITY_BACKUP_AND_LOAD.md) already archives the whole file storage, so chat files are included
  and restore-tested with the database. On Azure, the plan is to keep files in Azure Blob Storage with
  soft delete and Azure Backup, with backups kept at least 3 years (to be set up with hosting).
- Approved chat reviews can open shared files too, and each download is recorded.
- Note for hosting: the web server in front of the ERP must allow uploads of at least 130 MB per
  request (5 × 25 MB).

## Approved, recorded chat reviews

The owner's rule: no one reads someone else's chats except with very high-level approval, for a
specific reason, and every look is recorded. How it works (Messages → **Chat reviews**, shown only to
people given a review role):

1. A **requester** names the person, picks a reason (HR investigation, legal case, regulator or
   authorities, suspected fraud or theft), describes the specific case (30+ characters) and gives the
   period of messages needed (one year at most, not in the future).
2. An **approver** approves, or rejects with a note. The requester, approver and the person reviewed
   must be three different people.
3. Once approved, only the requester can read, read-only, for **72 hours**, and only messages sent in
   the approved period, in chats the person was part of.
4. Every request, decision, list view, chat opened and file downloaded is written to a permanent, unchangeable record
   (approvers can see it under "Show record") and to the security audit log. Message text is never
   copied into either.

Roles are given by name only. Being a system administrator does **not** grant them:

```sh
python manage.py set_chat_review_role --username <name> --role approver   # intended: business owner only
python manage.py set_chat_review_role --username <name> --role requester  # e.g. HR or legal lead
python manage.py set_chat_review_role --username <name> --role requester --remove
```

Giving or removing a role is itself recorded in the audit log.

Provisional: the 72-hour reading window, the approver being the business owner only, and the person
reviewed not being told (so an investigation is not compromised).

## Not in this version

- Instant delivery (today new messages appear within about 4 seconds), browser notifications,
  editing or deleting sent messages.
- Virus scanning of shared files (planned with hosting, e.g. Microsoft Defender for Storage).

## Technical notes

- Separate Django app `chat` with its own migrations, so it does not collide with `foundation`.
- API under `/api/chat/`: `people/`, `unread/`, `conversations/`, `conversations/<id>/`,
  `.../messages/` (`?after=` and `?before=` for paging), `.../read/`, `.../members/`,
  `.../members/<user_id>/`, `attachments/<id>/` (`?inline=1` for pictures), and the restricted
  `access-review/...` routes.
- Tests: `python manage.py test chat`, `npm run test:chat-ui`.
