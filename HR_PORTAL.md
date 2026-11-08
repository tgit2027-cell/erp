# HR Portal (for all staff)

The HR Portal is the employees' own area of the ERP, kept apart from the **HR & Payroll** section, which stays with the HR team. It is **switched on** (the owner said go for the live test on 6 Nov 2026). Setting `ERP_HR_PORTAL=off` hides it again: the menu hides it and every `/api/portal/...` address answers "not found".

## What staff see

- **Portal home** shaped like Tivoli's current HR portal: a dark banner, a search box and tiles. Tiles for what the ERP already does open straight away (Policies and Procedures, Newsletter, Events and Photos, Leave Request, Attendance, Employee Overtime, E-docs and Letters, Suggestion Box, My Password). The other tiles from today's portal show "Coming soon".
- **Please read**: memos and procedures marked "must read" that the person has not confirmed yet, with an "I have read this" button.
- **Latest announcements**, the **latest event's photo album** and **upcoming events**.
- **Policies and procedures**, **Newsletter and events**, **Events and photos** pages.
- **My leave, My attendance, My overtime and E-docs and letters** reuse the existing screens in the person's own view (no business unit), so staff see only their own records.
- **Suggestion box**: suggestions go to HR of the chosen unit, always with the sender's name. HR's answer shows under "My suggestions".
- **My password**: anyone signed in changes their own password (current password needed).

## What HR does, in the HR section

- **HR Portal posts**: write an announcement, memo, procedure or event for one business unit or the whole group; add a picture or PDF; mark it must-read; pin it; take it down with a reason; add photos to an event; see who has and has not read a must-read post.
- **Staff suggestions**: read and answer suggestions.
- **Portal sign-in** from a staff record: `POST /api/portal/sign-ins/<employee>/` creates the person's own sign-in (username = company email) with a temporary password. It carries no permissions at all. HR can also reset the temporary password of such a sign-in (never of one that opens more than the portal).

## Rules

Sam answered (5 Nov 2026):

1. Only the HR team members given the new permission **"Post on the HR Portal"** post. A post for the whole group needs it in every business unit (the HR Admin and Master Admin levels have it).
2. Posts go live at once, with no approval step.
3. Must-read posts are confirmed by each person, and the confirmations are shown on the person's staff record ("Procedures and memos read").
4. Suggestions always carry the sender's name. No anonymous suggestions.
5. Staff use their own sign-in: company email and their own password. Staff with no other access can sign in while the portal is on and land straight on it.

Still provisional:

- A post is seen by everyone who works in its unit (staff record) or has any access there; group posts by everyone.
- When a post goes up, everyone it is for gets one email with a link that opens the portal (sent by `send_alerts`, every 15 minutes; `--only portal` sends just these). The person who posted is not emailed. People without an email address are recorded and skipped.
- One picture (PNG or JPEG) or one PDF per post, up to 1.5 MB. An event album holds up to 30 photos of up to 1.5 MB each.
- HR creates portal sign-ins and resets their passwords; giving any permission stays with the admins (setup lock).

## Later

- Sign in with a one-time code sent to the company email instead of a password (changes sign-in for the whole ERP).
- The "Coming soon" tiles from the current portal.
