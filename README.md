# Technical Manual Search — Streamlit + Google Drive version

This is a second version of the PDF search tool, built on Streamlit instead
of the original FastAPI + plain HTML app. The search logic (TF-IDF ranking,
synonym/abbreviation expansion, page-level results, thumbnails) is the same
proven code from the first version — only the web framework and the file
storage changed.

## Why this version exists

The original version runs on your own Windows PC. That caused two real
problems: a self-signed HTTPS certificate was needed just so colleagues'
browsers would allow microphone access for voice search, and the app broke
every time your PC's IP address changed after being offline for a while.

Deploying this version to **Streamlit Community Cloud** (streamlit.io,
free) solves both automatically: your app gets a permanent address like
`your-app-name.streamlit.app` with a real, trusted HTTPS certificate — no
warnings, no IP dependency, and it works whether or not your PC is even
turned on.

The one thing that changes: the PDF library can no longer live only on your
PC's disk, because Streamlit Cloud's own storage is temporary (wiped
whenever the app restarts or redeploys). So this version stores the actual
PDF files in a **shared Google Drive folder**, and only keeps a small
search-index file cached alongside them — see `drive_store.py` for exactly
how. This mirrors the reference system you showed me (Google login, files
listed from a real backing store).

## What's reused from the original app

`pdf_processor.py`, `query_expand.py`, and `synonyms.json` are copied over
unchanged — same PDF text extraction, same chunking, same synonym
dictionary, so anything you added to `synonyms.json` before can be copied
straight into this folder too. `search_engine.py` is the same TF-IDF logic,
just adapted to save/load its index through a storage backend instead of a
hardcoded local file path.

## Setup checklist

### 1. Try it locally first (no Google Cloud needed yet)

```
pip install -r requirements.txt
streamlit run app.py
```

Without any Drive credentials configured, the app automatically falls back
to storing files in a local `local_drive_cache/` folder next to the code
(see `drive_store.py`). This lets you test uploading, searching, voice
input (once deployed with HTTPS — see step 4) and deleting before touching
Google Cloud at all.

### 2. Connect Google Drive — via your own account, not a service account

A "service account" (a robot Google account) would normally be the
simplest way to do this, but many company Google Workspace setups (this
one included) block sharing a Drive folder with an account that looks
external to the organization, which is exactly what a service account's
`...iam.gserviceaccount.com` address looks like. So instead, the app acts
as *your own* Google account via OAuth (the same kind of "Sign in with
Google" flow you've seen elsewhere) — one person authorizes it once, and
after that nobody else needs to log in to use the search tool itself.

1. Go to https://console.cloud.google.com/ and create a new project (or
   reuse one you already have).
2. Enable the **Google Drive API** for that project (APIs & Services →
   Enable APIs and Services → search "Google Drive API" → Enable).
3. Go to APIs & Services → **OAuth 同意画面** (OAuth consent screen) and
   configure it. Choose **内部 (Internal)** as the user type if it's
   offered (it will be, since the project belongs to your organization) —
   this means only your own company's accounts can ever use it, and
   avoids Google's app-verification process entirely. Fill in an app name
   and your email, save.
4. Go to APIs & Services → Credentials → Create Credentials → **OAuth
   client ID**. Application type: **Desktop app**. Create it, then
   download the JSON.
5. Save that file as `client_secret.json` in this project folder.
6. In Google Drive, create a folder (e.g. "Technical Manuals") in your own
   Drive that will hold every PDF. Copy its id from the URL:
   `drive.google.com/drive/folders/<THIS PART>`.
7. On your own PC, install one extra package and run the one-time
   authorization script included in this folder:
   ```
   pip install google-auth-oauthlib
   python get_refresh_token.py
   ```
   A browser window opens — log in with your Google account and approve
   Drive access. The script then prints three values (`client_id`,
   `client_secret`, `refresh_token`).

### 3. Configure secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and
fill in `drive_folder_id` (from step 2.6) and the three values printed by
`get_refresh_token.py` under `[gcp_oauth]`, following the example's
formatting. **Never commit `secrets.toml`, `client_secret.json`, or the
printed refresh token to a public repository or share them with anyone —
they grant Drive access as you.**

When deploying to Streamlit Community Cloud, you don't upload this file —
you paste the same content into the app's Settings → Secrets box in the
web dashboard instead.

### 4. Deploy to Streamlit Community Cloud

1. Push this folder to a GitHub repository (a private repo is fine and
   recommended, since it will contain your synonym dictionary and app
   logic even though secrets stay out of it).
2. Go to https://share.streamlit.io/, sign in, click "New app", point it
   at the repo and `app.py`.
3. In the app's Settings → Secrets, paste the contents of your
   `secrets.toml`.
4. Deploy. You'll get a URL like `https://your-app-name.streamlit.app`.

### 5. Restrict who can open it

Streamlit Community Cloud has a built-in "who can view this app" setting
(App settings → Sharing) where you can restrict access to specific email
addresses or your organization's domain, using Google sign-in — this is
almost certainly what the reference app you showed me was using, and it
means you don't need to build any custom login screen yourself.

## About voice input

Streamlit has no built-in microphone widget. This app embeds the same
browser Web Speech API used in the original prototype via
`st.components.v1.html` (see the `_VOICE_HTML` block in `app.py`):
recognized speech gets written into the page's URL and read back by
Python. This needs a real secure (HTTPS) origin to access the microphone —
which Streamlit Community Cloud provides automatically, so once deployed
there, voice search works for every colleague with zero certificate setup
on your end (an improvement over the original version's self-signed
certificate workaround).

## Known limitations / things to decide before relying on this

- **Cold starts after long idle periods**: Streamlit Community Cloud's free
  tier puts an app to sleep after inactivity; the next visitor triggers a
  ~30-60 second wake-up while it reloads. This is a real trade-off against
  the original PC-hosted version, which (once running) had no such delay.
- **Search index rebuild timing**: the index file is stored in Drive
  alongside the PDFs, so a cold start only downloads that one small file,
  not every PDF — this stays fast even with hundreds of manuals. Full PDF
  bytes are only fetched (and cached) the moment someone actually opens a
  result or its thumbnail.
- **Anyone who can open the app can currently upload or delete PDFs** in
  the shared library, same as the original version. Use the Streamlit
  Cloud sharing restriction in step 5 to control who that is; there's no
  additional per-action permission layer in the app itself.
- **OCR is not included**: scanned/image-only PDFs won't be indexed, same
  limitation as the original version.
