"""
Run this ONCE, on your own PC, to authorize the search app to act as your
Google account for Drive access. This is a one-time setup step, not part
of the deployed app itself.

Why this exists: instead of a "service account" (a robot account that
would need to be shared a Drive folder -- something your Workspace admin
has disabled for outside/robot accounts), the app acts as *you*, using an
OAuth refresh token you generate here. After this one-time step, nobody
else needs to log in to use the search tool; it always acts as you in the
background.

Setup before running this:
  1. In Google Cloud Console, for your project, go to "OAuth 同意画面"
     (OAuth consent screen) and configure it -- choose "内部" (Internal)
     if that option is available (it will be, since your project belongs
     to the horizon.co.jp organization), fill in an app name and your
     email, save.
  2. Go to "認証情報" (Credentials) -> "認証情報を作成" (Create Credentials)
     -> "OAuth クライアント ID". Application type: "デスクトップ アプリ"
     (Desktop app). Create it, then download the JSON (a button appears
     after creation, or click the download icon next to it in the list).
  3. Save that downloaded file as "client_secret.json" in this same
     folder (next to this script).
  4. Install the one extra dependency this script needs:
        pip install google-auth-oauthlib
  5. Run:
        python get_refresh_token.py
     A browser window will open asking you to log in with your Google
     account and approve Drive access. After you approve, come back to
     this terminal -- it will print three values.

Copy those three printed values into your secrets.toml (or Streamlit
Cloud's Secrets box) under a [gcp_oauth] section, exactly as shown in
.streamlit/secrets.toml.example.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\nSuccess! Add this to your secrets.toml (or Streamlit Cloud Secrets):\n")
    print("[gcp_oauth]")
    print(f'client_id = "{creds.client_id}"')
    print(f'client_secret = "{creds.client_secret}"')
    print(f'refresh_token = "{creds.refresh_token}"')
    print()


if __name__ == "__main__":
    main()
