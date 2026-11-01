# Procurement V3 — change log

Builds of the PO3 → Supplier Confirmation → Inbound Shipment → Goods Receipt
chain. The running build is shown on **Procurement Settings V3** and lives in
`metactical/procurement_v3/__init__.py` as `BUILD`.

The app's own `__version__` covers all of metactical and is not touched here.

---

## Build 2 — 2026-11-01 — Inbound shipment: created from the confirmation, many tracking numbers, routes and ETAs

Branch `feature/inbound-shipment-v3`.

### What changed

**The shipment belongs to the confirmation.**
- Submitting a Supplier Order Confirmation V3 opens its inbound shipment
  automatically, so a tracking number always has somewhere to go. The submit is
  never blocked if the shipment cannot be raised.
- **Create → Inbound Shipment** added to the confirmation; a confirmation with
  no shipment carries an orange notice saying so.
- **Create → Inbound Shipment removed from Purchase Order V3.** The PO now
  lists its shipments under a **Shipments** menu instead, each with its state
  and ETA.
- A supplier that holds part of the order back ships the balance later as its
  own delivery. The confirmation offers **Create → Another Shipment** when
  lines are still due, and the shipment works out what is left to carry.

**Many tracking numbers per shipment.**
- New child table **Inbound Shipment V3 Tracking**: number, master flag,
  carrier, service, tracking URL, carrier status, carrier ETA, delivered on,
  last checked.
- **Add Tracking Number** box: type or paste and press Enter. A whole pasted
  list is filed as separate rows in one go; duplicates are dropped.
- Exactly one row is the master. `tracking_no` on the shipment is now a
  read-only mirror of it, so existing prints and reports keep working.

**Destination and route.**
- **Destination Warehouse** (defaults from the PO, walks a bin up to the stock
  warehouse above it) and a read-only **Ship To** address for prints.
- **Route**: Direct / Via Blaine (cross-dock) / Via Other Consolidator, with
  **Extra Days For Route** (defaults to 2) added to the carrier's own arrival
  date to give the final ETA.

**Dates.**
- **Ship Date** fills itself from the confirmation — the promised ship date if
  the supplier gave one, otherwise the invoice date. A date typed by a person
  always wins, and **Ship Date From** records which it was.
- **Carrier Arrival** = ship date + the service's transit days.
  **Expected Arrival** = carrier arrival + route days. This is the date the
  stores read as the ETA, so it is freely editable and a hand-set date is never
  overwritten by the arithmetic; **ETA From** records where it came from.
- Every ETA move is written to the document's timeline with the old date, the
  new date and what moved it.
- **Arrived On** and **Days Late** are stamped when the shipment is received.

**Packing list is optional.**
- The item lines are no longer required. They still fill themselves from the
  confirmation, because `shipped_qty` on the order, the back-order rows and the
  receipt's prefill all read them — nobody has to type or check them.
- Boxes were already optional and are now labelled as such; both sections are
  collapsed by default.

**Carrier APIs — the seam, not the adapters.**
- `metactical/procurement_v3/carriers.py` applies what a carrier reports:
  pickup date onto the ship date, status and ETA onto each tracking row, and
  the latest arrival onto the shipment, overwriting the ETA and logging the
  move. A nightly sweep over In Transit shipments is registered.
- **No adapter ships with credentials.** `ADAPTERS` is empty, so the nightly
  job is a no-op and **Refresh From Carrier** says which carrier has no API set
  up rather than guessing. FedEx, UPS, DHL, Purolator and Canada Post each need
  an adapter and credentials before any of this moves a date.

**Seeing what is coming.**
- Connections panels on PO3, the confirmation and the shipment.
- Calendar view on the shipment list, keyed on expected arrival.
- The arrival date in the list shows **overdue** in red while in transit, and
  **(Nd late)** once received.

### Files

| | |
|---|---|
| New | `procurement_v3/inbound.py`, `procurement_v3/carriers.py`, `doctype/inbound_shipment_v3_tracking/`, three `*_dashboard.py`, `inbound_shipment_v3_calendar.js`, `patches/move_ins3_tracking_to_table.py` |
| Changed | `inbound_shipment_v3` (json, py, js, list.js), `supplier_order_confirmation_v3` (py, js), `purchase_order_v3.js`, `procurement_settings_v3` (json, py), `hooks.py`, `patches.txt`, `procurement_v3/__init__.py` |

### Schema and data

- New doctype `Inbound Shipment V3 Tracking` (child table).
- New fields on `Inbound Shipment V3`: `ship_date_source`, `eta_source`,
  `arrived_on`, `days_late`, `destination_warehouse`, `ship_to_display`,
  `route`, `route_days`, `carrier_eta`, `add_tracking`, `tracking`.
- New field on `Procurement Settings V3`: `procurement_v3_build`.
- `Inbound Shipment V3.items` is no longer mandatory.
- Patch `metactical.patches.move_ins3_tracking_to_table` copies each existing
  shipment's single tracking number into the new table as its master row, sets
  `route = 'Direct'` on existing rows, and marks dates already on those rows as
  `Manual` so the arithmetic does not move them.

### Rolling back to build 1

1. Deploy the commit before this branch and run `bench --site <site> migrate`.
2. Remove the patch's rows — nothing was taken off the shipment to create them,
   so the single `tracking_no` field is still intact and correct:
   ```sql
   DELETE FROM `tabInbound Shipment V3 Tracking`;
   DELETE FROM `tabDocType` WHERE name = 'Inbound Shipment V3 Tracking';
   DELETE FROM `tabPatch Log` WHERE patch = 'metactical.patches.move_ins3_tracking_to_table';
   ```
   Then `DROP TABLE IF EXISTS \`tabInbound Shipment V3 Tracking\`;`
3. The new columns on `tabInbound Shipment V3` are left behind by the rollback
   and are harmless; drop them only if you want the table clean:
   ```sql
   ALTER TABLE `tabInbound Shipment V3`
     DROP COLUMN ship_date_source, DROP COLUMN eta_source,
     DROP COLUMN arrived_on,       DROP COLUMN days_late,
     DROP COLUMN destination_warehouse, DROP COLUMN ship_to_display,
     DROP COLUMN route,            DROP COLUMN route_days,
     DROP COLUMN carrier_eta,      DROP COLUMN add_tracking;
   ```
4. `route`, `ship_date_source` and `eta_source` values written by the patch go
   with those columns. No shipment, order or confirmation loses a date,
   quantity or status in a rollback — everything this build added is either new
   or derived.

### Known limits

- No carrier adapter exists yet, so nothing updates a date automatically. The
  machinery around it is built and tested; each carrier still needs its API
  credentials and a function registered in `carriers.ADAPTERS`.
- `GMAS LOGISTIC` has no tracking URL template on its supplier card, so its
  numbers will not become links.
- The destination warehouse list is narrowed by `warehouse_role = 'Active
  Stock'` where that field exists, falling back to `warehouse_type = 'Active'`
  and then to every non-group warehouse.

---

## Build 1 — the chain as it stood before 2026-11-01

Backfilled when this log was adopted. Purchase Order V3, Supplier Order
Confirmation V3, Inbound Shipment V3, Goods Receipt V3, Supplier Claim V3 and
Item Conversion V3 as merged upstream, with the inbound shipment created from
the PO and carrying a single tracking number.
