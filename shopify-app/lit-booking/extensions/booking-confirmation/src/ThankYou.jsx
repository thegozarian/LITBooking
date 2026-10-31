/** @jsxImportSource preact */
import '@shopify/ui-extensions/preact';
import {render} from 'preact';

export default function extension() {
  render(<BookingDetails />, document.body);
}

function BookingDetails() {
  const bookings = shopify.lines.value.flatMap((line) => {
    const start = line.attributes.find(({key}) => key === 'Booking start')?.value;
    return start ? [{id: line.id, title: line.merchandise.product.title, start}] : [];
  });

  if (!bookings.length) return null;

  return (
    <s-section heading="Your appointment">
      <s-stack gap="base">
        {bookings.map((booking) => (
          <s-stack key={booking.id} gap="small">
            <s-text type="strong">{booking.title}</s-text>
            <s-text>{booking.start}</s-text>
            <s-text color="subdued">Ireland time (Europe/Dublin)</s-text>
          </s-stack>
        ))}
      </s-stack>
    </s-section>
  );
}
