# LIT Booking Lambda

This function serves the signed Shopify App Proxy calendar actions and Shopify's HMAC-signed `orders/create` webhook. It uses the existing `lit-booking-dev` Function URL, DynamoDB table, and SSM SecureStrings; no new AWS service is required.

## Booking lifecycle

- `slots` returns in-policy times after checking Google Calendar FreeBusy and DynamoDB locks.
- `hold` atomically reserves the appointment's time segments for 15 minutes.
- The product form submits the visible appointment time and private hold ID/token as line item properties.
- `orders/create` is verified against the raw request body with Shopify's client secret from `/lit-booking/dev/shopify-app-secret`.
- The Lambda confirms the hold, checks Calendar FreeBusy, then inserts a Google Calendar event using an order-derived stable event ID. Google Calendar sends guest updates; duplicate webhook deliveries resolve to the same event. The DynamoDB lock is made permanent so the slot stays unavailable in the booking grid.

Shopify subscription configuration is in `shopify-app/lit-booking/shopify.app.toml` and requests `read_orders` for the order webhook. The Lambda URL is also the app proxy target. Calendar OAuth JSON remains in `/lit-booking/dev/google-calendar-oauth` as a SecureString; calendar ID and table name remain stack configuration.

## Build and deploy

```powershell
sam validate --template-file template.yaml --region eu-north-1
sam build --template-file template.yaml
sam deploy
```

Use AWS MCP for AWS operations where available. Never put secret values in this repository or in CloudFormation parameters.
