# Booking backend (Google Apps Script)

Source for the prototype backend is kept here so it can live in the same Git repository as the Trade theme. The booking policy is in `booking-config.json`; secrets and deployment IDs must remain outside Git.

Prototype gate: deploy as a Google Apps Script web app and verify the Shopify App Proxy behavior (GET/POST, JSON bodies, redirect handling, and request signature validation) before implementing booking writes.
