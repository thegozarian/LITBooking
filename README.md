# Shopify booking project

- Shopify theme source is at the repository root so Shopify's GitHub theme integration can recognize the standard theme structure.
- `backend/apps-script/` — Google Apps Script source and version-controlled booking configuration.
- `outputs/` — audit and build plan.

Never commit OAuth credentials, webhook secrets, `.clasprc.json`, `.clasp.json`, store tokens, or deployment secrets. Shopify's theme integration should connect only to this repository's `main` branch and the intended development theme. Backend and output folders are separate from Shopify theme files.
