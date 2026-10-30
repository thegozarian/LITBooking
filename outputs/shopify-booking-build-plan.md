# Loewen IT Shopify Booking System — Theme Audit and Build Plan

## Step 1 complete: theme inspection and implementation plan

### Theme findings

- The supplied ZIP contains 346 files and is a Shopify Trade theme export.
- Product pages use `templates/product.json` and `sections/main-product.liquid`.
- The product form is Shopify's native `product` form rendered in `snippets/buy-buttons.liquid`; `assets/product-form.js` intercepts submission and adds items through Shopify's AJAX cart endpoint.
- Variant selection is native Trade behavior through `snippets/product-variant-picker.liquid`. This fits the Ireland / Northern Ireland site-visit price variants.
- The theme supports app blocks through `sections/apps.liquid`, and the main product section is block-based. This makes a Theme App Extension block a plausible integration point, subject to checking the live app block placement and preview behavior.
- Cart UI is split across Trade's cart drawer and cart page (`snippets/cart-drawer.liquid`, `sections/cart-drawer.liquid`, `sections/main-cart-items.liquid`, `sections/main-cart-footer.liquid`). Booking data must enter as line-item properties and survive the existing AJAX add/update/render behavior.
- The product form includes dynamic checkout button support. Appointment products will need accelerated checkout disabled or reliably gated so it cannot bypass reservation validation.
- Theme settings already live in `config/settings_schema.json` and saved values in `config/settings_data.json`.

### Central settings/configuration recommendation

Use two layers rather than putting authoritative booking rules in a public theme file:

1. **Operational source of truth:** a versioned `booking-config.json` beside the Apps Script/backend source. It can hold business hours, timezone, holiday rules, durations, buffers, hold/grace periods, advance window and site-visit start times. The backend validates and applies these values. Keep credentials and tokens in protected script properties / secret storage, never in the theme or public JSON.
2. **Theme Editor controls:** add a small “Booking system” group to `config/settings_schema.json` only for presentation choices (for example labels, colors, and whether to show an explanatory note). Shopify stores those values in `config/settings_data.json`. Do not use theme settings as the authority for duration, price, reservation expiry, availability, or checkout validation.

This gives a central, editable configuration file now and leaves a clean path to an admin interface later: the future interface can read/write the same backend configuration rather than depending on a theme deployment. The app can return safe public settings to the theme block through the app proxy.

### Build stages

1. **Step 1 — Inspect and plan (complete).** Audit the supplied theme structure and flows; document the architecture and configuration boundary. No theme files have been changed.
2. **Step 2 — Prepare a versioned working copy and backend prototype.** Establish a baseline of the supplied theme; confirm the live development theme and app access; create the Apps Script project and `booking-config.json`; prove Shopify App Proxy → Apps Script availability/reservation request behavior and Shopify Flow paid-order callback. Stop to review the prototype gates before building the complete booking flow.
3. **Step 3 — Build the reservation core.** Google-native reservation store, lock-protected overlap checks, expiry, checkout validation/grace, idempotency and recovery states.
4. **Step 4 — Connect payment and services.** Paid-order callback, Google Calendar insertion/retry, admin/customer booking-confirmed email, then Zoom API capability and meeting creation. Keep paid bookings visible for manual recovery if an integration fails.
5. **Step 5 — Integrate Trade storefront.** Theme app block or narrowly scoped theme integration, four product types, native variants for site-visit location pricing, booking form fields, calendar, cart line properties, hold timer and checkout guard.
6. **Step 6 — Walk through verification and launch preparation.** Validate the defined booking scenarios on the development store/theme, document connection/setup actions, and prepare changes for review. Do not publish or switch production traffic without explicit approval.

### Confirmed operating choices

- Shopify Plus; Shopify Flow HTTP action is available.
- Monday–Friday, 09:00–17:00, Europe/Dublin; exclude Republic of Ireland public holidays.
- 24-hour minimum notice; 30-day maximum horizon; seven-day scrolling calendar view.
- 30-minute buffer for video chat, phone call and remote service.
- Site visits: Republic of Ireland / Northern Ireland variants, different prices; island-of-Ireland service area; at most two per day; starts at 08:00, 08:30, 14:30 or 15:00; reserve four hours per visit. Travel and lunch are not separately booked.
- 15-minute initial hold and 15-minute checkout-pending grace.
- One appointment per order.
- Shopify standard new-order email plus separate booking-confirmed email to customer and admin after calendar event creation.
- Zoom account exists; verify API eligibility during setup and revisit any paid requirement then.

### Inputs needed for Step 2

- The actual customised development theme or confirmation that the supplied ZIP is its baseline.
- Access to the Shopify development store/theme and permission to create/connect the custom app / Theme App Extension.
- Google account/calendar access when we reach calendar connection; Zoom authorization when we reach meeting creation.
- Product IDs, variant prices and product metafield configuration can be captured when setting up the four products; they are not needed to complete this audit.

## Step 2 update: source control and deployment route

The supplied theme is now copied into the project at `theme/` and committed as an untouched baseline. The repository also contains `backend/apps-script/booking-config.json`, the initial Apps Script manifest, and a secrets ignore list. The booking settings JSON is backend-side and will be the authoritative policy source.

### GitHub and Shopify deployment

A GitHub repository is a good home for the project source. Shopify's GitHub integration can connect a GitHub-hosted theme to a Shopify store; Shopify CLI can also push local theme files to an unpublished theme. The GitHub theme connection applies to theme files, not to a custom app server or Apps Script deployment.

- Theme: Shopify GitHub integration, or Shopify CLI `theme push` to an unpublished theme. No production publish step without explicit approval.
- Shopify app configuration / Theme App Extension: Shopify CLI app workflow and `shopify app deploy`; this releases app configuration and extensions, not backend hosting.
- Google Apps Script: `clasp` can upload local source, create versions, and deploy the web app; GitHub Actions can automate it after credentials are stored as secrets.

Official current references:
- [Shopify GitHub-hosted themes and theme upload options](https://help.shopify.com/en/manual/online-store/themes/adding-themes)
- [Shopify CLI theme push](https://shopify.dev/docs/api/shopify-cli/theme/theme-push)
- [Shopify App Proxy](https://shopify.dev/docs/apps/build/online-store/app-proxies)
- [Shopify Flow Send HTTP request](https://help.shopify.com/en/manual/shopify-flow/reference/actions/send-http-request)
- [Google clasp and Apps Script deployment](https://developers.google.com/apps-script/guides/clasp)
- [Apps Script Content Service redirects](https://developers.google.com/apps-script/guides/content)

### Step 2 status

Local baseline and configuration are ready. The integration prototype is not complete: this environment currently has no Shopify CLI, GitHub CLI, or clasp command, and no GitHub remote is configured. We can still use this local Git repository and connect it to GitHub when a repository URL and authenticated access are available. Before implementing booking writes, we must deploy a minimal Apps Script web app and verify the real App Proxy request/response path and request authentication against the development store. Shopify's documentation notes that App Proxy requests forward the method and body, while Apps Script Content Service responses redirect to a one-time `script.googleusercontent.com` URL; this is the compatibility point the prototype needs to prove.
