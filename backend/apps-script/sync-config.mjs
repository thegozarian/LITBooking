import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(await readFile(join(here, "booking-config.json"), "utf8"));
const output = [
  "/** Generated from booking-config.json by sync-config.mjs. Do not edit by hand. */",
  `var BOOKING_CONFIG = ${JSON.stringify(config, null, 2)};`,
  ""
].join("\n");

await writeFile(join(here, "BookingConfig.gs"), output, "utf8");
