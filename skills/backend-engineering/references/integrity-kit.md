# Money, Booking, and Inventory Integrity Kit

Status: Active
Applies to: projects with the `money` capability
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner reservation, marketplace, and finance systems (2026)

Non-negotiable even in crunch mode.

- **Idempotency**: create, reserve, and payment endpoints accept an idempotency key;
  a unique constraint guarantees one effect per key.
- **Amounts**: integer minor units or `numeric`; never floating point. Store the
  currency.
- **Snapshots**: persist prices, discounts, fees, and totals as they were at
  transaction time; never recompute history from current prices.
- **State machine**: explicit statuses with allowed transitions enforced in code
  and, where practical, by database constraints.
- **Locks**: inventory, quota, and settlement updates use row or advisory locks
  inside one transaction; check availability and decrement in the same transaction.
- **Audit events**: append-only table recording entity, action, before and after
  values, actor, and time for every state change; never updated or deleted.
- **Webhooks**: verify signature and source, process idempotently, store the raw
  event id, and reconcile periodically against the provider.
- **Manual settlement**: manual payment confirmations require an authenticated role,
  record the evidence reference, and go through the same state machine.
- **Tests**: critical-path tests for reserve -> pay -> confirm -> cancel/refund,
  duplicate callbacks, concurrent purchase of the last unit, and expiry.
- **Auth**: every admin and money route is authenticated and authorized.
