# Notes

A place for casual notes, like Google Keep: text notes and checklists with tags, pins and
an archive. It is the **Notes** tab of the console, so the Android app has it too.

Notes are not memories. Memories are few and chosen, and they are given to the chat
model with your messages. Notes are a large, messy pile, and **no note is ever sent to a
model or any other service**; they are only shown in the console.

## Using it

- **New note:** the "Take a note…" bar at the top. Click it for a title, tags and a
  Checklist box (each line becomes an item).
- **Cards:** pinned notes first, then the most recently edited; a card shows its edited
  date. The page fills the width in as many columns as fit (two on a phone).
- **On a card:** tick checklist items, click a tag to filter by it, and use the icons
  for tags, archive and delete (a second tap confirms). With a mouse the icons show on
  hover; on a phone they are always visible.
- **Opening a note** (click it) shows an editor: title, text or checklist rows, tag chips,
  and the icons for pin, turning it into a checklist or back into text, archive and delete.
  Everything is saved when it closes (Done, Escape, Ctrl/Cmd+Enter or a click outside),
  and only if something changed, so just looking at a note keeps its edited date.
- **Tags:** the field suggests your existing tags (most used first, without those the
  note already has) as you focus or type, with "+ New tag" when nothing matches. A tag
  typed in another case reuses the spelling already in use.
- **Finding notes:** the search box looks through titles, text, checklist items and tags
  of all notes. The row under it shows All, your five most-used tags, "All tags (N)"
  (a searchable list of every tag) and Archived.
- **Big collections:** the first 48 notes are drawn, then 48 more as you scroll (or with
  "Show more notes"). Search and tag filters still cover every note.

## Importing from Google Keep

1. In [Google Takeout](https://takeout.google.com) export only **Keep**.
2. In the Notes tab choose **Import from Google Keep** and pick the `.zip` as it is.
3. It first reports what it found without saving anything, for example "Found 1,604 new
   notes (skipping 44 images, 47 empty notes)". **Import** then saves them.

Only `Keep/*.json` is read. Mapped: title, text, checklist, labels (as tags), pinned,
archived, created and edited times, and links (added to the text unless the text has them
already). Skipped: trashed notes, images, colours, and notes with nothing in them. A title
over 300 characters (pasted text) keeps all of it, at the start of the note's text.
Importing the same export again adds only notes that are not there yet.

## How it is built

| Part | Where |
| --- | --- |
| Table `notes` (title, body, checklist, tags, pinned, archived, source and its key) | `migrations/022_notes.sql`, `NoteModel` in `app/persistence/models.py` |
| Rules, listing and the Keep import | `app/work/notes.py` (`NoteService`) |
| API: `GET/POST /notes`, `PATCH/DELETE /notes/{id}`, `POST /notes/import/google-keep` | `app/api/notes.py` |
| The tab | `app/web/static/notes.js`, `index.html`, `console.css` |

`GET /notes?include_archived=true` returns every note (about 1 MB for 1,600); the page
filters, sorts and draws them itself.
