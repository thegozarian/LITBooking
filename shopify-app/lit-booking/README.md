# LIT Booking Shopify extension

The `booking` theme app extension renders the four-week availability picker, holds a selected slot, and attaches the booking time and hold credentials to the Shopify product form. Booking products use Shopify accelerated checkout; the app block reveals that button only while a valid hold exists.

## Development

- Readable calendar source: `scripts/availability.js`
- Storefront asset: `extensions/booking/assets/availability.js` (minified; keep under Shopify's 10 KB theme app block limit)
- Block markup and settings: `extensions/booking/blocks/calendar-availability.liquid`
- Block styles: `extensions/booking/assets/availability.css`
- App proxy and webhook scopes/subscription: `shopify.app.toml`

When changing `scripts/availability.js`, regenerate the asset with the esbuild executable bundled with Shopify CLI, for example:

```powershell
& "$env:APPDATA\npm\node_modules\@shopify\cli\node_modules\@esbuild\win32-x64\esbuild.exe" scripts/availability.js --minify --outfile=extensions/booking/assets/availability.js
shopify app build
```

Release app extension changes separately from theme changes with `shopify app deploy` and `shopify app release`. The product template and theme buy-button snippet are in the repository root.
