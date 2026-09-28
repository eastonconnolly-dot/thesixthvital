# Integrating the content engine (Phase 3)

Everything in `content/` is standalone — none of it is imported by the Flask
app, and nothing in `api/app.py` was touched (another engineer is actively
working in/around it). Three small additions wire it in.

## 1. Register the blueprint in `api/app.py`

Add alongside the other blueprint imports/registrations:

```python
from routes.content_admin import bp as content_admin_bp
...
app.register_blueprint(content_admin_bp)
```

Until this is added, `api/tests/conftest.py`'s `app` fixture registers the
blueprint itself (guarded so it becomes a no-op the moment `app.py` also
registers it) — so the test suite exercises `/admin/content` today even
though it isn't wired into the real running app yet.

## 2. Config additions (already made — `api/config.py`, `.env.example`)

```python
BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY", "")
PUBLER_API_KEY = os.environ.get("PUBLER_API_KEY", "")
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
CONTENT_UPLOADS_DIR = os.environ.get("CONTENT_UPLOADS_DIR", ".../content/output/uploads")
CONTENT_CLIPS_DIR = os.environ.get("CONTENT_CLIPS_DIR", ".../content/output/clips")
```

No model changes were needed — `ContentItem`/`Asset` already existed exactly
as specified and are reused as-is. `ContentItem.status` also now takes the
value `"rejected"` (set by `POST /admin/content/<id>/reject`) in addition to
the four already documented in `models.py`'s comment; the column itself is
an unconstrained `String(20)` so this needed no migration.

## 3. Nothing else

No new tables, no changes to `routes/admin.py`, `routes/practice.py`,
`routes/public.py`, `routes/esign.py`, or `app.py` itself.

---

## What each piece does

- `content/ingest.py` — video/URL → local Whisper transcript → one
  Claude call (JSON-schema-constrained, same `output_config` pattern as
  `services/patient_sim.py`) → a batch of draft `ContentItem` rows (clips,
  5 LinkedIn posts, 1 newsletter, 3 captions, 2 proof snippets), all tagged
  with a shared `batch_id` in `item_metadata`.
- `content/cut.py` — ffmpeg wrapper: 9:16 + 1:1 brand-styled crops with
  burned-in captions per clip, recorded as `Asset` rows.
- `content/schedule.py` — pushes `status="approved"` items to Buffer or
  Publer (LinkedIn's own API can't post to a personal profile); no key ->
  marks `status="scheduled"` locally and logs a clear warning that nothing
  was actually pushed.
- `content/send_newsletter.py` — finds this week's approved newsletter and
  sends it via `services.gmail_client` to `Lead`s with
  `status in ("nurture", "qualified")`, skipping anyone in
  `SuppressedEmail`.
- `api/routes/content_admin.py` + `api/templates/admin/content.html` — the
  only path from `draft` to `approved`/`rejected`. Nothing publishes
  unapproved, ever.

## What needs something this environment doesn't have

- **A real Whisper model.** Neither `faster-whisper` nor `openai-whisper`
  is installed here (by design — see `api/requirements.txt`, both are
  commented as optional installs). `content/ingest.py._transcribe()`
  raises a clear `IngestError` instead of crashing; this is exercised for
  real (not mocked) in `test_content_ingest.py`.
- **`ffmpeg` on PATH.** Not installed in this environment.
  `content/cut.py._run_ffmpeg()` raises a clear `CutError` instead of a raw
  `FileNotFoundError`; also exercised for real in `test_content_cut.py`.
- **`yt-dlp`**, for YouTube-URL ingestion — same story, gated cleanly.
- **A `BUFFER_API_KEY` or `PUBLER_API_KEY`** — without one,
  `content/schedule.py` still works end-to-end (marks `scheduled` locally),
  just doesn't push anywhere; this is the tested default path.
- **A sample video** to actually run `content/ingest.py` against end-to-end
  with a real model — the pipeline logic itself (transcript → plan →
  persisted `ContentItem`s → cut → schedule → newsletter send) is fully
  unit-tested with Whisper/Claude/ffmpeg/yt-dlp/Buffer/Publer all mocked,
  but nobody has watched real footage come out the other end as a captioned
  vertical clip.
- **Real brand font files** — same situation as
  `api/services/pdf/base.py`: drop `SourceSans3-SemiBold.ttf` into
  `shared/brand/fonts/` and `content/cut.py` picks it up automatically for
  burned-in captions (`fontfile=`); until then it falls back to the generic
  fontconfig alias `Sans`.
- **An `/unsubscribe/<lead_id>` route.** `send_newsletter.py` builds this
  URL for the CAN-SPAM footer link, but no route currently serves it — that
  page (and the actual unsubscribe write to `SuppressedEmail`) is Phase
  2/5 territory, not built here. The link is inert until that lands.
