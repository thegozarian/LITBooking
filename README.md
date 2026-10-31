# LIT Booking

Shopify theme and booking integration for LIT appointments.

## Booking flow

1. The product's `custom.booking_type` metafield selects its booking rules.
2. The theme extension shows available slots for the next four weeks. Selecting a slot creates a 15-minute DynamoDB hold.
3. The selected date/time and private hold credentials are submitted as Shopify line item properties with Shopify accelerated checkout. Booking products do not show the normal Add to cart button.
4. Shopify's `orders/create` webhook is HMAC-verified by the existing Lambda. The Lambda confirms the DynamoDB slot lock and creates a Google Calendar event with a stable event ID, so webhook retries do not create duplicates.
5. Shopify's normal order notifications continue for the customer and store. Google Calendar sends the customer an event invitation when an email address is available. The selected appointment time is stored as the public `Booking start` line item property for use in those emails.

The booking calendar is `Europe/Dublin`. Shopify app version `lit-booking-7` and the AWS stack `lit-booking-dev` contain the current implementation. The calendar extension source is in `shopify-app/lit-booking/scripts/availability.js`; its storefront asset is minified at `shopify-app/lit-booking/extensions/booking/assets/availability.js`.

## Theme publishing

The theme files are at the repository root and are connected to the Shopify theme through GitHub. Push theme changes on `main` to sync them to Shopify. The Shopify app extension is published separately with Shopify CLI from `shopify-app/lit-booking`.

## Product setup

Create a product metafield definition for `custom.booking_type` (single-line text), then set each booking product to one of `video_chat`, `phone_call`, `remote_service`, or `site_visit`. Assign booking products to the `product.booking` template and add the Calendar availability app block.

## Secrets

Keep Shopify and Google credentials in AWS Systems Manager Parameter Store as SecureStrings. Do not commit them or place them in theme code, template parameters, or chat.
