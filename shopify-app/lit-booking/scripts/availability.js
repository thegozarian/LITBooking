(() => {
  const timezone = "Europe/Dublin";


  const proxyPath = "apps/lit-booking";

  function localDateValue(date) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
    return `${values.year}-${values.month}-${values.day}`;
  }

  function dublinMidnight(dateValue) {
    const utcGuess = new Date(`${dateValue}T00:00:00Z`);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(utcGuess);
    const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
    const dublinTimeAtGuess = Date.UTC(
      Number(values.year),
      Number(values.month) - 1,
      Number(values.day),
      Number(values.hour),
      Number(values.minute),
      Number(values.second),
    );
    return new Date(utcGuess.getTime() - (dublinTimeAtGuess - utcGuess.getTime()));
  }

  function addCalendarDays(dateValue, days) {
    const date = new Date(`${dateValue}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  }

  function formatDay(dateValue, options) {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      ...options,
    }).format(new Date(`${dateValue}T12:00:00Z`));
  }

  function formatTime(isoDateTime) {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(isoDateTime));
  }

  function formatPeriod(isoDateTime) {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(isoDateTime));
  }

  function proxyUrl(params) {
    const root = window.Shopify?.routes?.root || "/";
    const url = new URL(`${root.replace(/\/?$/, "/")}${proxyPath}`, window.location.origin);
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }
    return url;
  }

  async function proxyRequest(params, method = "GET") {
    const response = await fetch(proxyUrl(params), {
      method,
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    });
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload?.error || "request_failed");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function productFormFor(block) {
    const scope = block.closest("product-info") || document;
    return (
      scope.querySelector('product-form form[action*="/cart/add"]') ||
      scope.querySelector('form[action*="/cart/add"]')
    );
  }

  function setLineProperty(form, key, value) {
    if (!form) return;
    let input = form.querySelector(`input[data-lit-booking-property="${key}"]`);
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = `properties[${key}]`;
      input.dataset.litBookingProperty = key;
      form.append(input);
    }
    input.value = value;
  }

  function clearLineProperties(form) {
    if (form) {
      form.querySelectorAll("[data-lit-booking-property]").forEach((input) => input.remove());
    }
  }

  function initAvailabilityBlock(block) {
    if (block.dataset.initialized === "true") return;
    block.dataset.initialized = "true";
    const find = (name) => block.querySelector(`[data-booking-${name}]`);
    const panel = find("panel"), grid = find("days"), slots = find("slots");
    const monthLabel = find("month"), dayLabel = find("day"), status = find("status");
    const previous = find("previous"), next = find("next"), check = find("check");
    const openButton = find("open"), summary = find("summary"), dialog = find("confirm");
    const confirmButton = find("confirm-button"), confirmText = find("confirm-text");
    const form = productFormFor(block), checkout = form?.querySelector("[data-lit-booking-checkout]");
    const buyButton = checkout?.querySelector('button[type="submit"]');
    const bookingType = block.dataset.bookingType;
    const storageKey = `lit-booking-hold:${block.dataset.productId}`;
    const today = localDateValue(new Date());
    const monthStart = (date) => `${date.slice(0, 7)}-01`;
    function shiftMonth(date, amount) {
      const value = new Date(`${monthStart(date)}T12:00:00Z`);
      value.setUTCMonth(value.getUTCMonth() + amount);
      return value.toISOString().slice(0, 10);
    }
    const count = Math.max(1, Math.min(12, Number(block.dataset.horizonCount) || 2));
    const horizonEnd = block.dataset.horizonUnit === "weeks"
      ? addCalendarDays(today, count * 7 - 1)
      : addCalendarDays(shiftMonth(today, count), -1);
    let shownMonth = monthStart(today), selectedDay = today, draft = null, held = null;
    let busy = false, requestVersion = 0, expiryTimer, retryRequest = null;
    const cache = new Map();
    if (checkout) checkout.hidden = false;
    if (buyButton) buyButton.querySelector("span").textContent = block.dataset.bookLabel || "Book now";

    function enableCheckout(enabled) {
      if (buyButton) { buyButton.disabled = !enabled; buyButton.setAttribute("aria-disabled", String(!enabled)); }
    }
    function updateControls() {
      previous.disabled = busy || shownMonth <= monthStart(today);
      next.disabled = busy || shiftMonth(shownMonth, 1) > monthStart(horizonEnd);
      check.disabled = busy || !draft;
      confirmButton.disabled = busy;
      openButton.disabled = busy;
      find("back").disabled = busy;
      find("close").disabled = busy;
      grid.querySelectorAll("button").forEach((button) => { button.disabled = busy || button.dataset.unavailable === "true"; });
      slots.querySelectorAll("button").forEach((button) => { button.disabled = busy || button.dataset.unavailable === "true"; });
    }
    function setBusy(value) { busy = value; updateControls(); }
    function clearHold() {
      clearTimeout(expiryTimer);
      held = null;
      sessionStorage.removeItem(storageKey);
      clearLineProperties(form);
      if (form) delete form.dataset.litBookingHoldExpiry;
      summary.textContent = block.dataset.emptyLabel || "Choose a date and time for your appointment.";
      openButton.textContent = block.dataset.chooseLabel || "Choose date & time";
      enableCheckout(false);
    }
    function selectionText(slot) {
      const minutes = Math.round((new Date(slot.end) - new Date(slot.start)) / 60000);
      return `${formatDay(localDateValue(new Date(slot.start)), {weekday: "short", day: "numeric", month: "short", year: "numeric"})} · ${formatTime(slot.start)}–${formatTime(slot.end)} · ${minutes} minutes`;
    }
    function saveHold(value) {
      held = value;
      sessionStorage.setItem(storageKey, JSON.stringify(value));
      form.dataset.litBookingHoldExpiry = String(new Date(value.expires_at).getTime());
      setLineProperty(form, "Booking start", formatPeriod(value.start));
      setLineProperty(form, "Booking end", formatPeriod(value.end));
      setLineProperty(form, "Booking duration", `${Math.round((new Date(value.end) - new Date(value.start)) / 60000)} minutes`);
      setLineProperty(form, "_Booking type", bookingType);
      setLineProperty(form, "_Booking hold id", value.hold_id);
      setLineProperty(form, "_Booking hold token", value.hold_token);
      summary.textContent = selectionText(value);
      openButton.textContent = block.dataset.changeLabel || "Change";
      enableCheckout(true);
      panel.hidden = true;
      status.textContent = `Reserved until ${formatTime(value.expires_at)}. Continue to checkout before then. Ireland time.`;
      clearTimeout(expiryTimer);
      expiryTimer = setTimeout(() => {
        clearHold(); cache.clear(); draft = null;
        status.textContent = "Your reservation expired. Please choose a time again.";
      }, Math.max(0, new Date(value.expires_at).getTime() - Date.now()));
    }
    async function releaseHold() {
      if (!held) return;
      if (new Date(held.expires_at).getTime() > Date.now()) {
        await proxyRequest({action: "release", hold_id: held.hold_id, hold_token: held.hold_token}, "POST");
      }
      clearHold(); cache.clear();
    }
    function renderMonth() {
      monthLabel.textContent = formatDay(shownMonth, {month: "long", year: "numeric"});
      grid.replaceChildren();
      const startOffset = (new Date(`${shownMonth}T12:00:00Z`).getUTCDay() + 6) % 7;
      const firstCell = addCalendarDays(shownMonth, -startOffset);
      for (let index = 0; index < 42; index++) {
        const day = addCalendarDays(firstCell, index);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = String(Number(day.slice(-2)));
        button.dataset.unavailable = String(day < today || day > horizonEnd || monthStart(day) !== shownMonth);
        button.disabled = button.dataset.unavailable === "true";
        button.className = "lit-booking__date";
        button.setAttribute("aria-label", formatDay(day, {weekday:"long", day:"numeric", month:"long", year:"numeric"}));
        button.setAttribute("aria-pressed", String(day === selectedDay));
        if (day === today) button.setAttribute("aria-current", "date");
        button.addEventListener("click", () => { if (!busy) { selectedDay = day; draft = null; renderMonth(); loadDay(); } });
        grid.append(button);
      }
      updateControls();
    }
    function renderSlots(daySlots) {
      slots.replaceChildren();
      dayLabel.textContent = formatDay(selectedDay, {weekday:"short", day:"numeric", month:"short"});
      if (!daySlots.length) {
        const empty = document.createElement("p"); empty.className = "lit-booking__empty";
        empty.textContent = "No appointments available on this day. Choose another date.";
        slots.append(empty); return;
      }
      let currentHour = "", row;
      for (const slot of daySlots) {
        const hour = formatTime(slot.start).split(":")[0];
        if (hour !== currentHour) { row = document.createElement("div"); row.className = "lit-booking__slot-row"; slots.append(row); currentHour = hour; }
        const button = document.createElement("button");
        button.type = "button"; button.className = "lit-booking__slot";
        button.textContent = formatTime(slot.start);
        button.dataset.unavailable = String(!slot.available); button.disabled = !slot.available;
        button.setAttribute("aria-label", `${formatTime(slot.start)} ${slot.available ? "available" : "booked or unavailable"}`);
        button.setAttribute("aria-pressed", String(draft?.start === slot.start));
        if (!slot.available) button.title = "Booked or unavailable";
        button.addEventListener("click", () => {
          if (busy) return;
          draft = slot; retryRequest = null; renderSlots(daySlots); updateControls();
          status.textContent = `${selectionText(slot)}. Use the green check to review your selection.`;
        });
        row.append(button);
      }
      updateControls();
    }
    async function loadDay() {
      const version = ++requestVersion, day = selectedDay;
      draft = null; setBusy(true);
      dayLabel.textContent = formatDay(day, {weekday:"short", day:"numeric", month:"short"});
      slots.textContent = "Loading available times…";
      status.textContent = "Loading available times…";
      try {
        let daySlots = cache.get(day);
        if (!daySlots) {
          const payload = await proxyRequest({action:"slots", booking_type:bookingType, start:dublinMidnight(day).toISOString(), end:dublinMidnight(addCalendarDays(day, 1)).toISOString()});
          if (!Array.isArray(payload.slots)) throw Error("invalid_slots");
          daySlots = payload.slots; cache.set(day, daySlots);
        }
        if (version !== requestVersion) return;
        renderSlots(daySlots);
        status.textContent = "Select a time, then use the green check. Times are shown in Ireland time.";
      } catch (_) {
        if (version !== requestVersion) return;
        slots.textContent = "Times could not be loaded.";
        status.textContent = "Please choose the date again to retry.";
      } finally { if (version === requestVersion) setBusy(false); }
    }
    function positionPanel() {
      panel.style.removeProperty("--booking-offset");
      const rect = panel.getBoundingClientRect();
      const offset = Math.max(12 - rect.left, Math.min(0, window.innerWidth - 12 - rect.right));
      panel.style.setProperty("--booking-offset", `${offset}px`);
    }
    async function openPicker() {
      if (busy) return;
      enableCheckout(false); setBusy(true);
      try { await releaseHold(); }
      catch (_) { status.textContent = "Your previous reservation could not be released. Please try Change again."; setBusy(false); return; }
      setBusy(false); cache.clear(); panel.hidden = false; draft = null;
      renderMonth(); positionPanel(); await loadDay();
      find("close").focus();
    }
    function closePicker() {
      if (busy) return;
      panel.hidden = true; openButton.focus();
    }
    previous.addEventListener("click", () => { if (!busy) { shownMonth = shiftMonth(shownMonth, -1); selectedDay = shownMonth < today ? today : shownMonth; renderMonth(); loadDay(); } });
    next.addEventListener("click", () => { if (!busy) { shownMonth = shiftMonth(shownMonth, 1); selectedDay = shownMonth; renderMonth(); loadDay(); } });
    openButton.addEventListener("click", openPicker);
    find("close").addEventListener("click", closePicker);
    panel.addEventListener("keydown", (event) => { if (event.key === "Escape" && !dialog.open) { event.preventDefault(); closePicker(); } });
    check.addEventListener("click", () => {
      if (!draft || busy) return;
      confirmText.textContent = selectionText(draft);
      dialog.showModal(); find("back").focus();
    });
    find("back").addEventListener("click", () => { if (!busy) { dialog.close(); check.focus(); } });
    dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    confirmButton.addEventListener("click", async () => {
      if (!draft || busy || !form) return;
      setBusy(true); confirmButton.textContent = "Reserving…";
      retryRequest ||= crypto.randomUUID();
      try {
        const hold = await proxyRequest({action:"hold", booking_type:bookingType, start:draft.start, request_id:retryRequest}, "POST");
        saveHold({...hold, booking_type:bookingType, start:draft.start, end:draft.end, request_id:retryRequest});
        dialog.close(); openButton.focus();
      } catch (error) {
        dialog.close(); cache.delete(selectedDay);
        const message = error.status === 409 ? "That time was just taken. Please choose another." : "We could not confirm this time. Please try again.";
        await loadDay(); status.textContent = message;
      } finally { confirmButton.textContent = block.dataset.confirmLabel || "Confirm appointment"; setBusy(false); }
    });
    if (form) {
      if (form.litBookingSubmitGuard) form.removeEventListener("submit", form.litBookingSubmitGuard, true);
      form.litBookingSubmitGuard = (event) => {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!held || new Date(held.expires_at).getTime() <= Date.now() || buyButton?.disabled) {
          enableCheckout(false); status.textContent = "Choose and confirm a date and time before booking."; return;
        }
        form.submit();
      };
      form.addEventListener("submit", form.litBookingSubmitGuard, {capture:true});
    }
    clearLineProperties(form); enableCheckout(false);
    if (!bookingType || !form) {
      openButton.disabled = true;
      status.textContent = !bookingType ? "Set this product’s custom.booking_type metafield to enable booking." : "The booking product form was not found.";
      return;
    }
    const saved = sessionStorage.getItem(storageKey);
    if (saved) {
      try {
        const current = JSON.parse(saved);
        if (current.end && current.booking_type === bookingType && new Date(current.expires_at).getTime() > Date.now()) {
          setBusy(true);
          proxyRequest({action:"hold", booking_type:bookingType, start:current.start, request_id:current.request_id}, "POST")
            .then((value) => saveHold({...current,...value}))
            .catch(() => { clearHold(); status.textContent = "Your previous reservation is no longer available. Please choose again."; })
            .finally(() => setBusy(false));
        } else clearHold();
      } catch (_) { clearHold(); }
    }
    window.addEventListener("resize", () => { if (!panel.hidden) positionPanel(); });
  }
  function init() { document.querySelectorAll("[data-lit-booking-availability]").forEach(initAvailabilityBlock); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true}); else init();
  document.addEventListener("shopify:section:load", init);
})();
