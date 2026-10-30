# Booking backend (Google Apps Script)

Source for the prototype backend is kept here so it can live in the same Git repository as the Trade theme. The booking policy source is `booking-config.json`; run `node sync-config.mjs` to generate the Apps Script-compatible `BookingConfig.gs`. Secrets and deployment IDs must remain outside Git.

`Code.gs` is a read-only connectivity probe. It reports request shape only and intentionally does not echo request bodies or create reservations. Prototype gate: deploy as a Google Apps Script web app and verify the Shopify App Proxy behavior (GET/POST, JSON bodies, redirect handling, and request signature validation) before implementing booking writes.
