# Adeus — open items

Running note of things still to do. Captured 2026-08-28. Items 2–5 need a
decision before any code gets written.

## 1. Reading an email in Adeus doesn't mark it read in Gmail

Right now opening a letter is preview-only. Adeus reads the `UNREAD` label
(`app/gmail_client.py`, `app/classify.py`) and shows a Read/Unread state in the
list, but nothing ever removes that label, so the real mailbox is untouched.

This was a deliberate design promise — the README says Adeus "never sends,
deletes, or marks email as read" — but it means the inbox stays exactly as
noisy after a session as it was before, and the same mail keeps resurfacing.

We already hold the `gmail.modify` scope (it's used for archiving, see
`_batch_archive` in `app/gmail_client.py`), so no new permission is needed.

**To decide:** does opening a letter mark it read immediately, is it an explicit
button, or a bulk "mark this category read" action? And do we drop the
never-marks-read promise from the README, or keep it as an opt-out default?

## 2. Tasks and emails should be one view, not two

The two concepts are tracked separately today and it makes the whole thing
harder to follow than it needs to be. A task comes out of an email, so the
combined view should be singular rather than making you hold both models in
your head at once.

**To decide:** what "singular" means concretely — one merged list where a task
is a decorated email row, or an email list where tasks are a facet you can
filter to.

## 3. Duplicated category counts

Per-category counts appear in two places: the category selector (where the count
sits next to something you can actually click, which works well) and again in
the overall analytics block. Same numbers, two renderings, and they can drift.

**To settle:** keep the count on the selector since it's actionable there, and
decide what analytics is actually *for* — if it's just the same totals again, it
may not need to exist in its current form.

## 4. No way to preview tasks

Tasks get extracted and pushed to Notion, but there's nowhere in Adeus to look
at them. Ideally they'd live somewhere of their own, not hanging off the email
reader.

Note this pulls against item 2 — that one wants tasks and emails merged, this
one wants tasks viewable independently. Probably reconcilable (one combined
inbox view *plus* a dedicated task surface), but worth resolving deliberately
rather than by accident.

**To discuss.**

## 5. Categories are not mutually exclusive

Current set is invoice, private, social, travel, work, other
(`DEFAULT_CATEGORIES` in `app/settings.py`). The classifier is told to pick
exactly one and to "pick the more specific one" on a tie, which is papering over
a taxonomy problem rather than fixing it.

The clearest overlap is work vs. private — depending on how you look at it those
can be the same thing, and the prompt currently resolves it with a pile of
hand-written exclusions ("never private if unsubscribe or mailing_list is
true...").

**To decide:** a genuinely disjoint set of categories. Possibly split the axes
instead of forcing one label — e.g. *who it's from* (person / machine / list)
separate from *what it's about* (money / travel / work) — so things stop
competing for one slot.

## Minor

- `static/app.js:315` and `:898` both read `message.unread || message.unread`.
  The operand is repeated, so the fallback does nothing. Likely meant to check a
  second field; harmless today but worth a look when item 1 gets touched.
