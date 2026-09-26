# AlphaScan

Streamlit stock screener for personal use.

## Deploy to Streamlit Community Cloud

1. Community Cloud's GitHub deployment creates a **public app from a public GitHub repository**. Make this repository public, or create a separate public repository for the app and upload `app.py`, `requirements.txt`, `.gitignore`, and this README. Do not upload `.venv`, `.env`, or `.streamlit/secrets.toml`. If the source must stay private, use the private-app deployment option in Snowflake or another hosting provider that supports private repositories.
2. Sign in at [share.streamlit.io](https://share.streamlit.io/) with GitHub and choose **Create app**.
3. Select the public repository, branch, and `app.py` as the main file, then deploy.
4. In the app's **Settings > Secrets**, set a strong personal-use password:

   ```toml
   APP_PASSWORD = "choose-a-long-unique-password"
   ```

5. Open the deployed URL on your phone and enter that password.

The app reads `APP_PASSWORD` from Streamlit secrets, or from the environment variable of that name. If it is unset, password protection is disabled (the default for local development). The Yahoo Finance provider requires no API key.

## Local run

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```
