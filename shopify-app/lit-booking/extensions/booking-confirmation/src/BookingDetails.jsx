/** @jsxImportSource preact */
import policy from '../../../../../backend/aws-lambda/src/booking-policy.json';

// Legacy orders store an Ireland wall clock. Never parse it in the viewer's zone.
export function legacyEnd(start, minutes) {
  const match = /^(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}), (\d{2}):(\d{2})$/.exec(start);
  if (!match || !minutes) return '';
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const date = new Date(Date.UTC(+match[3], months.indexOf(match[2]), +match[1], +match[4], +match[5]) + minutes * 60000);
  return new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short'}).format(date);
}

export function BookingDetails() {
  const bookings = shopify.lines.value.flatMap((line) => {
    const attributes = Object.fromEntries(line.attributes.map(({key, value}) => [key, value]));
    const start = attributes['Booking start'];
    if (!start) return [];
    const minutes = policy.booking_types[attributes['_Booking type']]?.duration_minutes;
    return [{id: line.id, title: line.merchandise.product.title, start,
      end: attributes['Booking end'] || legacyEnd(start, minutes),
      duration: attributes['Booking duration'] || (minutes ? `${minutes} minutes` : ''),
    }];
  });
  if (!bookings.length) return null;
  return (
    <s-section heading="Appointment details">
      <s-stack gap="base">
        {bookings.map((booking) => (
          <s-stack key={booking.id} gap="small">
            <s-text type="strong">{booking.title}</s-text>
            <s-text>Start: {booking.start}</s-text>
            {booking.end && <s-text>End: {booking.end}</s-text>}
            {booking.duration && <s-text>Duration: {booking.duration}</s-text>}
            <s-text color="subdued">Ireland time (Europe/Dublin)</s-text>
          </s-stack>
        ))}
      </s-stack>
    </s-section>
  );
}
