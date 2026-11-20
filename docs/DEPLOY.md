# Deploying the public demo

The public demo runs at <https://payee-sentinel.streamlit.app>, on Streamlit Community Cloud from this repository, and
needs no key. Without `.env` it opens on the replay of the run recorded on real Meko (`recordings/`), Claude is off, and
the ledger is a local SQLite copy (`tests/test_app.py` clicks through exactly that, from the first screen to the end of
week 3).

1. **The repository is public.** The hackathon asks for it.
2. **Create the app** at share.streamlit.io: from GitHub, repository `alessandrorodriguez881/payee-sentinel`, branch
   `main`, main file `app.py`. In the advanced settings choose Python 3.12, the version `requirements.txt` was tested
   on, and pick the app's address.
3. **Secrets: none are needed.**
   - Not needed for the model either: a run recorded with a model key keeps the model's answers, and the replay shows
     them. Optional: `GROQ_API_KEY` (free) or `ANTHROPIC_API_KEY` turns a live model on for every visitor, within that
     key's limits or billed to it. `python scripts/token_table.py --run` measures what one Claude explanation costs.
   - Never put `MEKO_API_KEY` or the `MEKO_PG_*` values there. With them, visitors would get the "Real Meko, live"
     engine and write into the live Meko account and its Postgres ledger.
4. **Check it while logged out:** the first screen, Start, every step to the end (€0 against €85,250), then week 3.
5. **Put the link at the top of the README.**
6. **Keep it awake.** Free apps fall asleep after a while without visitors. A scheduled GitHub workflow can open the
   demo every few hours and press the wake-up button if it sleeps: `.github/workflows/keep-demo-awake.yml` does that
   every 6 hours.

Done on 17 Nov 2026: the app runs at `payee-sentinel.streamlit.app` (Python 3.12, no secrets) and was walked through
while logged out, from the first screen to the end of week 3, on a desktop and a phone-sized screen.
