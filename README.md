# LIT Booking

Project status as of 31 October 2026. This document records what is implemented and what has actually been observed. The intended Google Calendar and email behavior is not treated as complete until it has been verified end to end.

## Current status

- The live Shopify store uses the GitHub-connected theme in `thegozarian/LITBooking`; the theme files live at this repository root.
- A booking product page has a Calendar availability app block. It displays a four-week booking window as one week at a time, checks availability, and lets the customer select and temporarily hold a slot.
- Selecting a slot passes the appointment time and private hold details with the product line item. Booking products use a booking-specific **Buy now** action that submits directly to Shopify checkout; they do not show the normal Add to cart action. Guest checkout is enabled and the owner reports that it is working.
- The owner placed an order. Shopify created the order, and the selected slot now appears unavailable on the product page.
- Order #1043 (Shopify ID `7249091461326`) successfully created the Google Calendar appointment for 3 November 2026, 09:00–10:00 Europe/Dublin. DynamoDB records it as `BOOKED` with an event ID, and the owner confirmed the event is visible in Google Calendar.
- The missing step was approving the installed LIT Booking app's updated **View orders** permission. Publishing the app version alone did not update that store installation's permissions. After approval through **Open app**, the `orders/create` webhook completed the existing Lambda calendar flow without an additional code change. Landing on Shopify's default app home afterward is expected with the current application URL.
- No claim is made here that customer or admin appointment emails were sent.

## Open work

1. **Improve the date and time picker presentation.** The current picker works as a weekly grid, but its look, spacing, responsiveness, and week transition still need review on the live product page.
2. **Calendar event creation is verified.** Preserve the approved Orders access and existing `orders/create` webhook. Customer invitations and device sync still need separate confirmation.
3. **Show appointment details after purchase.** Add the confirmed date/time to the Shopify Thank you/order status page and the customer and store notification emails. Confirm whether the standard order confirmation already includes the public `Booking start` line item property; do not assume it does.
4. **Verify the complete lifecycle.** With a controlled test booking, confirm order properties, webhook success, event in the configured calendar, availability reflecting the event, customer/admin notifications, and appointment details on the confirmation page. Use Shopify Payments test mode for test orders.

## What is implemented

### Storefront and checkout

- Assign a booking product to the `product.booking` theme template and add the **Calendar availability** app block in the Shopify theme editor. The currently synced template keeps the app block in its own Apps section after the main product section; Shopify may rewrite this JSON when the theme editor is saved.
- Set the product metafield `custom.booking_type` to one of `video_chat`, `phone_call`, `remote_service`, or `site_visit`.
- The app proxy requests available slots from the Lambda. Selecting an available slot creates a 15-minute hold in DynamoDB. The form carries public `Booking start` and private `_Booking type`, `_Booking hold id`, and `_Booking hold token` line item properties.
- Booking products hide the standard Add to cart button. The booking Buy now action is hidden until a valid slot is held, then submits the product form with Shopify's `/checkout` return path. Shopify checkout is configured to allow guest checkout (owner-confirmed).
- The slot appearing unavailable after the owner's order confirms the storefront now sees a lock for that slot. It does not establish that the calendar event or notifications succeeded.

Relevant files:

- Theme product form: `snippets/buy-buttons.liquid`
- Booking product template: `templates/product.booking.json`
- Shopify app configuration and webhook: `shopify-app/lit-booking/shopify.app.toml`
- Theme app block markup/settings: `shopify-app/lit-booking/extensions/booking/blocks/calendar-availability.liquid`
- Readable storefront logic: `shopify-app/lit-booking/scripts/availability.js`
- Extension assets: `shopify-app/lit-booking/extensions/booking/`

### Booking service and calendar integration

- AWS SAM stack: `lit-booking-dev`, Region `eu-north-1`.
- Lambda Function URL: `https://wc73hkapedgmmwqcvvltpbygrq0cvggj.lambda-url.eu-north-1.on.aws/`
- The same Lambda handles signed Shopify App Proxy availability/hold actions and the Shopify `orders/create` webhook.
- The configured webhook endpoint is `https://wc73hkapedgmmwqcvvltpbygrq0cvggj.lambda-url.eu-north-1.on.aws/webhooks/orders/create`. The Shopify app configuration subscribes to `orders/create` and requests `read_orders`.
- DynamoDB table: `lit-booking-dev-bookings` (as configured by the stack).
- The order handler verifies Shopify's webhook HMAC, reads the booking line item properties, checks the hold and Google Calendar FreeBusy, makes the DynamoDB lock permanent, calls Google Calendar event insertion with an order-derived stable event ID, and then marks the hold `BOOKED` with the event ID.
- This flow was verified with test order #1043: the webhook completed, the hold became `BOOKED`, and the owner saw the resulting event in Google Calendar. If a later app release adds a scope, approve its permission update on the installed store app as well as releasing the version.
- Calendar OAuth credentials are stored in SSM Parameter Store SecureString `/lit-booking/dev/google-calendar-oauth`. Shopify's app secret is stored at `/lit-booking/dev/shopify-app-secret`. Never put either value in this repository, terminal output, or chat.
- Configured booking calendar ID: `1b31848155d5f67f60507e51f1fcdc79a8a197b78a48612e2464491ea5af3b74@group.calendar.google.com`.
- Google event insertion includes the shopper as an attendee when Shopify supplies an email and requests Google Calendar guest updates. This is intended behavior; it has not been confirmed for the latest order.
- The current Lambda code does not implement a separate custom email sender. Shopify's standard order notification is separate, and appointment-specific content in Shopify notifications/Thank you page has not yet been verified or completed.

### Booking rules

Source: `backend/aws-lambda/src/booking-policy.json`.

- Time zone: `Europe/Dublin`.
- General availability: Monday to Friday, 09:00–17:00, excluding Irish public holidays.
- Minimum notice: 24 hours. Maximum lead time: 30 days. Storefront view: 28 days, one week displayed at a time.
- Holds expire after 15 minutes; checkout grace period is 15 minutes.
- Video chat: 60 minutes plus a 30-minute buffer.
- Phone call: 60 minutes plus a 30-minute buffer.
- Remote service: 120 minutes plus a 30-minute buffer.
- Site visit: 240 minutes, up to two per day, with starts at 08:00, 08:30, 14:30, or 15:00; policy also lists service areas IE and XI and variant-based pricing.

## Developer setup and operations

### Repository and theme sync

- GitHub repository: [thegozarian/LITBooking](https://github.com/thegozarian/LITBooking), branch `main`.
- The repository root is the Shopify theme. Git Bash was used to sync the GitHub-connected theme; changes to theme files on `main` are the theme publishing path. Confirm GitHub and Shopify theme sync status before editing the same theme files in both places.
- The Shopify theme editor can create commits directly to GitHub. The latest observed theme-editor commit is `48ad606` (`Update from Shopify for theme LITBooking/main...`). Fetch before pushing local work so that this theme-editor change is not overwritten.
- The local clone at `C:\Users\Sean\Documents\Codex\2026-10-30\at` was not caught up to that GitHub commit when this README was written; its `origin/main` tracking ref was stale and local `main` showed two commits ahead of that stale ref. The local GitHub push had previously been rejected for non-fast-forward, and local HTTPS authentication needed troubleshooting. Fetch/reconcile before pushing; do not force-push.
- Git identity can be configured in Git Bash if needed:

  ```bash
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```

- Typical safe sync sequence after GitHub authentication is working:

  ```bash
  git status --short --branch
  git fetch origin
  git log --oneline --left-right main...origin/main
  ```

  Reconcile local work with the current remote history before committing/pushing. Do not use `git push --force` to resolve a divergence.

### Shopify CLI app extension

- App project directory: `shopify-app/lit-booking`.
- Shopify CLI is installed and authenticated. Version `lit-booking-10` is released, including the existing calendar picker and the new Booking details Thank you extension. The Shopify app build passed.
- Theme files and app extension have separate publishing paths: GitHub sync handles the theme; Shopify CLI deploy publishes the app extension.
- From PowerShell or Git Bash, change to the app directory before running:

  ```bash
  cd shopify-app/lit-booking
  shopify app config validate --json
  shopify app build
  shopify app deploy --allow-updates
  ```

  Deploy only after reviewing the CLI's release/update prompt. Do not include secrets in app config or source.

### AWS SAM and AWS CLI

- AWS CLI and AWS SAM CLI are installed. The configured AWS profile is `lit-booking`; the Region is `eu-north-1`.
- AWS credentials were obtained with `aws login --profile lit-booking` and may expire. If an AWS command reports an expired session, sign in again with that command.
- Template and Lambda source: `backend/aws-lambda/`.
- Validate and build from the repository root:

  ```powershell
  aws --profile lit-booking --region eu-north-1 sts get-caller-identity
  sam validate --template-file backend/aws-lambda/template.yaml --region eu-north-1
  sam build --template-file backend/aws-lambda/template.yaml
  ```

- The deployed stack is `lit-booking-dev`. No AWS command is needed for theme-only changes. Review the SAM change set carefully before any deployment; do not deploy simply to investigate the missing event.
- Prefer the configured AWS MCP for AWS operations where available. Do not print or paste SSM SecureString values.

## Notifications and confirmation status

- Shopify order creation has been observed for the owner's order.
- Shopify can send its regular order confirmation, but whether it includes the appointment date/time has not been checked.
- Google Calendar event creation is confirmed for test order #1043. The code requests attendee updates, but receipt of the customer invitation has not been confirmed.
- The Thank you/order status page and customer/admin notifications still need appointment-specific date/time and time-zone details. No separate email integration is currently implemented in this Lambda.

## Diagnostic information to collect next

The order-to-calendar issue is resolved by approving the installed app's updated View orders access. For future failures, check installed permissions, webhook delivery, Lambda logs, and the hold's `status`, `orderId`, and `eventId`. A temporary hold can make a slot unavailable even when no event was created; `BOOKED` plus a returned event ID and the visible Calendar event confirmed success in this test. The initial order #1042 was placed before the permission update and has not been backfilled.

The Thank you page now has a separate **Booking details** block from LIT Booking, saved in active checkout profile `20709582` immediately after the confirmation heading. Source: `shopify-app/lit-booking/extensions/booking-confirmation/src/ThankYou.jsx`. It reads the purchased line items through Shopify's Thank you extension API, displays the product title and public `Booking start` property, and labels the time Europe/Dublin. It makes no external API request and does not expose private hold properties. Orders without `Booking start` render no block. The build and published placement are verified; its appointment content still needs checking on a booking checkout because the editor's example order has no booking properties. The same Appointment details component now targets both Thank you and Order status. It displays start, end, duration, and Europe/Dublin. The Order status block is saved in active profile 20709582 and verified on order #1044 (4 November 2026, 09:00–10:00, 60 minutes). New checkout line items also save Booking end and Booking duration; earlier orders derive these from the stored booking type and current policy duration. Appointment duration excludes the buffer. Customer phone numbers are not currently included in Google events; phone collection and phone-call requirements are deferred.

A hidden block named `assessment-thankyou` from the older integration remains hidden and unchanged. Do not edit or remove it as part of this work.

## Secrets and customer data

Never commit Shopify secrets, OAuth client secrets, refresh tokens, hold tokens, or customer details. Keep AWS credential values in SSM SecureString, use redacted logs, and share only the order number or non-sensitive error text when troubleshooting.
