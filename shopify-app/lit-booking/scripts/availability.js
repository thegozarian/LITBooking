(() => {
  const timezone = "Europe/Dublin";
  const windowDays = 7;
  const bookingHorizonDays = 28;
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

    const previousButton = block.querySelector("[data-lit-booking-previous]");
    const nextButton = block.querySelector("[data-lit-booking-next]");
    const weekLabel = block.querySelector("[data-lit-booking-week-label]");
    const status = block.querySelector("[data-lit-booking-status]");
    const weekGrid = block.querySelector("[data-lit-booking-week]");
    const bookingType = block.dataset.bookingType;
    const productId = block.dataset.productId;
    const holdStorageKey = `lit-booking-hold:${productId}`;
    const form = productFormFor(block);
    const checkoutButton = form?.querySelector("[data-lit-booking-checkout]");
    if (checkoutButton) checkoutButton.hidden = true;

    if (!previousButton || !nextButton || !weekLabel || !status || !weekGrid) return;

    if (!bookingType) {
      previousButton.disabled = true;
      nextButton.disabled = true;
      status.textContent = "This product needs the custom.booking_type metafield before times can be shown.";
      weekGrid.replaceChildren();
      return;
    }

    if (form && form.dataset.litBookingHoldGuard !== "true") {
      form.dataset.litBookingHoldGuard = "true";
      form.addEventListener("submit", (event) => {
        const token = form.querySelector('input[data-lit-booking-property="_Booking hold token"]');
        const expiry = Number(form.dataset.litBookingHoldExpiry || 0);
        if (!token?.value || expiry <= Date.now()) {
          event.preventDefault();
          event.stopImmediatePropagation();
          status.textContent = "Choose and hold an available time before continuing to checkout.";
          block.scrollIntoView({ behavior: "smooth", block: "center" });
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        form.submit();
      }, { capture: true });
    }

    const today = localDateValue(new Date());
    let weekOffset = 0;
    let activeSlotButton = null;
    let loading = false;

    function updateNavigation() {
      previousButton.disabled = loading || weekOffset === 0;
      nextButton.disabled = loading || weekOffset + windowDays >= bookingHorizonDays;
    }

    function setBusy(isBusy) {
      loading = isBusy;
      updateNavigation();
      weekGrid.querySelectorAll("button").forEach((button) => {
        button.disabled = isBusy || button.dataset.available !== "true";
      });
    }

    function saveHold(hold, slotButton) {
      const state = {
        ...hold,
        booking_type: bookingType,
        start: hold.start,
        request_id: hold.request_id,
      };
      sessionStorage.setItem(holdStorageKey, JSON.stringify(state));
      if (!form) {
        status.textContent = "The time is held, but the product form was not found. Please contact the shop before retrying.";
        return;
      }

      form.dataset.litBookingHoldExpiry = String(new Date(state.expires_at).getTime());
      setLineProperty(form, "Booking start", formatPeriod(state.start));
      if (state.end) {
        setLineProperty(form, "Booking end", formatPeriod(state.end));
        setLineProperty(form, "Booking duration", `${Math.round((new Date(state.end) - new Date(state.start)) / 60000)} minutes`);
      }
      setLineProperty(form, "_Booking type", state.booking_type);
      setLineProperty(form, "_Booking hold id", state.hold_id);
      setLineProperty(form, "_Booking hold token", state.hold_token);
      if (checkoutButton) checkoutButton.hidden = false;
      if (slotButton) {
        slotButton.textContent = `Held · ${formatTime(state.start)}`;
        slotButton.classList.add("lit-booking-availability__slot--held");
        slotButton.dataset.available = "false";
        slotButton.disabled = true;
        activeSlotButton = slotButton;
      }
      status.textContent = `Time held until ${formatPeriod(state.expires_at)}. Continue to checkout before the hold expires.`;
      window.setTimeout(() => {
        const raw = sessionStorage.getItem(holdStorageKey);
        if (!raw) return;
        try {
          if (JSON.parse(raw).hold_id === state.hold_id) {
            clearHold();
            status.textContent = "Your time hold expired. Choose and hold a time again.";
          }
        } catch (_error) {
          clearHold();
        }
      }, Math.max(0, new Date(state.expires_at).getTime() - Date.now()));
    }

    function clearHold() {
      sessionStorage.removeItem(holdStorageKey);
      if (form) {
        delete form.dataset.litBookingHoldExpiry;
        clearLineProperties(form);
      }
      if (checkoutButton) checkoutButton.hidden = true;
      if (activeSlotButton) {
        activeSlotButton.textContent = formatTime(activeSlotButton.dataset.start);
        activeSlotButton.classList.remove("lit-booking-availability__slot--held");
        activeSlotButton.dataset.available = "true";
        activeSlotButton.disabled = false;
        activeSlotButton = null;
      }
    }

    async function releaseCurrentHold() {
      const raw = sessionStorage.getItem(holdStorageKey);
      if (!raw) return;
      const current = JSON.parse(raw);
      if (new Date(current.expires_at).getTime() <= Date.now()) {
        clearHold();
        return;
      }
      await proxyRequest(
        { action: "release", hold_id: current.hold_id, hold_token: current.hold_token },
        "POST",
      );
      clearHold();
    }

    async function selectSlot(slot, slotButton) {
      setBusy(true);
      status.textContent = "Holding that time…";
      try {
        await releaseCurrentHold();
        const requestId = crypto.randomUUID();
        const hold = await proxyRequest(
          {
            action: "hold",
            booking_type: bookingType,
            start: slot.start,
            request_id: requestId,
          },
          "POST",
        );
        saveHold({ ...hold, start: slot.start, end: slot.end, request_id: requestId }, slotButton);
      } catch (error) {
        status.textContent = error.status === 409
          ? "That time was just taken. Refresh the week to see the latest availability."
          : "The time could not be held. Please try again.";
      } finally {
        setBusy(false);
      }
    }

    function renderWeek(slots, startDate) {
      weekGrid.replaceChildren();
      for (let dayOffset = 0; dayOffset < windowDays; dayOffset += 1) {
        const dateValue = addCalendarDays(startDate, dayOffset);
        const daySlots = slots.filter((slot) => localDateValue(new Date(slot.start)) === dateValue);
        const dayCard = document.createElement("section");
        dayCard.className = "lit-booking-availability__day";

        const dayHeading = document.createElement("h3");
        dayHeading.className = "lit-booking-availability__day-heading";
        dayHeading.textContent = `${formatDay(dateValue, { weekday: "short" })} ${formatDay(dateValue, { day: "numeric", month: "short" })}`;
        dayCard.append(dayHeading);

        const slotList = document.createElement("div");
        slotList.className = "lit-booking-availability__day-slots";
        if (daySlots.length === 0) {
          const empty = document.createElement("p");
          empty.className = "lit-booking-availability__empty-day";
          empty.textContent = "No times";
          slotList.append(empty);
        }

        for (const slot of daySlots) {
          const slotButton = document.createElement("button");
          slotButton.type = "button";
          slotButton.className = "lit-booking-availability__slot";
          slotButton.dataset.start = slot.start;
          slotButton.dataset.available = slot.available === true ? "true" : "false";
          slotButton.textContent = slot.available === true ? formatTime(slot.start) : "Booked";
          slotButton.disabled = slot.available !== true;
          slotButton.setAttribute(
            "aria-label",
            `${formatTime(slot.start)} ${slot.available === true ? "available" : "booked or unavailable"}`,
          );
          if (slot.available === true) {
            slotButton.addEventListener("click", () => selectSlot(slot, slotButton));
          } else {
            slotButton.classList.add("lit-booking-availability__slot--unavailable");
          }
          slotList.append(slotButton);
        }

        dayCard.append(slotList);
        weekGrid.append(dayCard);
      }
    }

    async function loadWeek() {
      const startDate = addCalendarDays(today, weekOffset);
      const endDate = addCalendarDays(startDate, windowDays);
      const endLabelDate = addCalendarDays(startDate, windowDays - 1);
      setBusy(true);
      status.textContent = "Loading bookable times…";

      try {
        const start = dublinMidnight(startDate);
        const end = dublinMidnight(endDate);
        const payload = await proxyRequest({
          action: "slots",
          booking_type: bookingType,
          start: start.toISOString(),
          end: end.toISOString(),
        });
        if (!Array.isArray(payload.slots)) throw new Error("invalid_slots");
        weekGrid.style.setProperty("--booking-slide", weekOffset === 0 ? "0" : "10px");
        weekGrid.classList.add("is-changing");
        await new Promise((resolve) => window.setTimeout(resolve, 120));
        renderWeek(payload.slots, startDate);
        weekLabel.textContent = `${formatDay(startDate, { day: "numeric", month: "short" })} – ${formatDay(endLabelDate, { day: "numeric", month: "short", year: "numeric" })}`;
        requestAnimationFrame(() => weekGrid.classList.remove("is-changing"));
        status.textContent = "Choose an available time. Grey slots are booked or unavailable.";
      } catch (_error) {
        weekGrid.classList.remove("is-changing");
        status.textContent = "Bookable times could not be loaded. Please try again later.";
      } finally {
        setBusy(false);
      }
    }

    async function changeWeek(offset) {
      if (loading || offset < 0 || offset + windowDays > bookingHorizonDays) return;
      try {
        await releaseCurrentHold();
      } catch (_error) {
        status.textContent = "Your existing time could not be released. Wait for it to expire before changing weeks.";
        return;
      }
      weekOffset = offset;
      await loadWeek();
    }

    previousButton.addEventListener("click", () => changeWeek(weekOffset - windowDays));
    nextButton.addEventListener("click", () => changeWeek(weekOffset + windowDays));

    const savedHold = sessionStorage.getItem(holdStorageKey);
    if (savedHold) {
      try {
        const current = JSON.parse(savedHold);
        if (new Date(current.expires_at).getTime() > Date.now()) {
          proxyRequest(
            {
              action: "hold",
              booking_type: current.booking_type,
              start: current.start,
              request_id: current.request_id,
            },
            "POST",
          )
            .then((hold) => {
              saveHold({ ...hold, start: current.start, end: current.end, request_id: current.request_id });
            })
            .catch(() => {
              clearHold();
              status.textContent = "Your previous hold expired or is no longer available. Choose a time again.";
            });
        } else {
          clearHold();
        }
      } catch (_error) {
        clearHold();
      }
    }

    loadWeek();
  }

  function init() {
    document.querySelectorAll("[data-lit-booking-availability]").forEach(initAvailabilityBlock);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  document.addEventListener("shopify:section:load", init);
})();
