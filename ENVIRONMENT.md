# Environment file for `orchestrator.py`

This repo includes a `.env` file at the project root with the variables used by `orchestrator.py`.

Files created:

- `.env` — contains placeholders for the required variables.

How to use (Windows `cmd.exe` temporary session):

```
set DO_INFERENCE_URL=https://inference.do-ai.run/v1
set MODEL_ACCESS_KEY=sk-do-REPLACE_ME
set BRIDGE_SECRET=gastown-demo-2026
set VERCEL_TOKEN=your-vercel-token
set VERCEL_PROJECT=gastown-demo

python orchestrator.py "Your meeting notes here"
```

Or, load the `.env` file from Python using `python-dotenv` (recommended for local development):

```
pip install python-dotenv

# in your Python script
from dotenv import load_dotenv
load_dotenv('.env')
# then run orchestrator.py; or modify orchestrator.py to call load_dotenv()
```

Security note: replace `MODEL_ACCESS_KEY` and `VERCEL_TOKEN` with real secrets before running. Do not commit real secrets to public repositories.
