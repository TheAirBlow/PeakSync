import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]

client_secret_file = sys.argv[1] if len(sys.argv) > 1 else "data/client_secret.json"
token_file = sys.argv[2] if len(sys.argv) > 2 else "data/token.json"

flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
creds = flow.run_local_server(port=0)

with open(token_file, "w") as f:
    f.write(creds.to_json())

print(f"saved credentials to {token_file}")
