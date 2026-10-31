/** @jsxImportSource preact */
import '@shopify/ui-extensions/preact';
import {render} from 'preact';
import {BookingDetails} from './BookingDetails.jsx';

export default function extension() {
  render(<BookingDetails />, document.body);
}
