# Procurement V3 — change log

Builds of the PO3 → Supplier Confirmation → Inbound Shipment → Purchase Receipt
chain. The running build is shown on **Procurement Settings V3** and lives in
`metactical/procurement_v3/__init__.py` as `BUILD`.

The app's own `__version__` covers all of metactical and is not touched here.

---

## Build 4 — 2026-11-01 — The Partially Received state actually sticks

Same branch as builds 2 and 3. **Build 3's main behaviour did not work on a
migrated site**; this is the fix, found by restoring deverp's own database
locally and verifying against it.

### What was wrong

`Partially Received` was added by a patch, because workflows live in the
database rather than in the app. But `Workflow` is a registered fixture
(`hooks.py`), and `bench migrate` syncs fixtures **after** it runs patches - so
the exported `fixtures/workflow.json`, which predates the state, overwrote it.

The patch reported success, because it does succeed: it saves and commits, and
only then prints. The fixture import that undid it minutes later says nothing at
all. On the restored deverp data the result was a workflow with its original four
states and no transition into Partially Received - build 3's headline behaviour
silently inert, and a receipt that counted short would have gone straight back
to closing the shipment as Received.

Proven rather than inferred, on the restored site:

```
after patch   : [Draft, In Transit, Received, Cancelled, Partially Received] 6 transitions
after fixtures: [Draft, In Transit, Received, Cancelled]                     3 transitions
```

### What changed

The state now lives in the fixture as well as the patch, so the two agree
whichever runs last:

| | |
|---|---|
| `fixtures/workflow.json` | `Partially Received` document state and its three transitions on Inbound Shipment V3 Flow |
| `fixtures/workflow_action_master.json` | the `Mark Partially Received` action |
| `hooks.py` | that action added to the Workflow Action Master fixture filter |

The patch is kept and unchanged. It is what gives the state to a site that is
already running, and its `ensure_action` is what lets the fixture's workflow
import at all on deverp, where the action master does not yet exist. Fixtures
import action masters before workflows, so a fresh install works from the
fixtures alone.

### Files

| | |
|---|---|
| Changed | `fixtures/workflow.json`, `fixtures/workflow_action_master.json`, `hooks.py`, `procurement_v3/__init__.py` |

### Schema and data

No schema change. The fixture carries the same workflow rows the build 3 patch
created, so a site that already ran build 3 ends up in exactly the same state.

### Rolling back to build 3

Build 3's code plus this build's fixtures is the broken combination, so roll the
fixtures back with the code:

1. Deploy build 3's commit and run `bench --site <site> migrate`.
2. The build 3 fixtures then remove the state on the next fixture sync by
   themselves. To do it immediately, use build 3's rollback SQL.

### Known limits

- `fixtures/workflow_action_master.json` is still missing five actions its own
  workflows reference (`Confirm`, `Flag Discrepancy`, `Mark In Transit`,
  `Mark Received`, `Submit for Approval`). They exist on deverp from manual
  setup, so a fresh site install would likely fail importing the workflows.
  Pre-existing and left alone here - it is the devs' fixture export to correct.
- Any workflow change made in the Workflow UI on a site is overwritten by the
  next migrate for the same reason. That applies to all five V3 flows, not just
  this one.

---

## Build 3 — 2026-11-01 — Shipments settle themselves, and a short delivery tells somebody

Same branch as build 2.

### What changed

**A shipment is Received when it has all arrived, not when someone starts counting.**

Until now the first receipt counted against a shipment closed the whole thing as
Received — including lines nobody had seen. A container received over two days
is one shipment and two receipts, so the state is now settled from what has
actually been counted across *every* receipt against it:

| | |
|---|---|
| Some lines still due | **Partially Received** — stays open, a note says how many are outstanding |
| Everything counted | **Received**, submitted, arrival stamped |
| Lines counted short and then closed short on the order | **Received** — what did not arrive is not coming |

That last row matters: without it a short line would hold the shipment open
forever and the short delivery would never be reported, which is the one case
that most needs reporting.

**Partially Received** is a new workflow state (draft, Stock User), added by a
patch because workflows live in the database rather than in the app. It sits
between In Transit and Received, and can be moved back to In Transit.

**A short delivery is emailed to accounting and the buyers.** Once per shipment,
when it settles as Received with something missing:

- **Short** — what the shipment said it carried against what was counted
- **Boxes** — any on the packing list that were never checked in
- **Rejected** — arrived but failed inspection, reported separately because it
  is a different conversation with the supplier
- supplier, order, confirmation, carrier, tracking, expected and actual arrival,
  and links to all three documents

Lines the confirmation already recorded as back-ordered, cancelled, discontinued
or out of stock are **never** reported as short — their absence is the plan, and
reporting them would train people to ignore the alert.

Recipients default to `accounting@goldenplazadistributors.com` and
`po@goldenplazadistributors.com`, and are editable on Procurement Settings V3
along with a switch to turn the alert off.

**Receiving never fails because of mail.** If the send throws, it is logged, a
note goes on the shipment, and the stamp is deliberately left unset so the next
receipt tries again. The goods are booked in either way.

### Files

| | |
|---|---|
| New | `procurement_v3/notify.py`, `patches/add_partially_received_to_ins3_workflow.py`, `patches/short_receipt_notify_defaults.py` |
| Changed | `procurement_v3/inbound.py` (settle_shipment, outstanding_on_shipment), `purchase_receipt_v3.py`, `inbound_shipment_v3.json`, `procurement_settings_v3.json`, `inbound_shipment_v3_list.js`, `inbound_shipment_v3_calendar.js`, `patches.txt`, `procurement_v3/__init__.py` |

### Schema and data

- New workflow state `Partially Received` on `Inbound Shipment V3 Flow`, with
  transitions to and from In Transit and on to Received.
- New field on `Inbound Shipment V3`: `short_notified_on`.
- New fields on `Procurement Settings V3`: `notify_on_short_receipt`,
  `short_receipt_notify`.
- `short_receipt_notify_defaults` writes the recipients and switches the alert
  on, because a field default in a doctype JSON only applies to a document
  being created and this Single already exists everywhere.

### Rolling back to build 2

1. Deploy build 2's commit and run `bench --site <site> migrate`.
2. Drop the workflow state and put any shipment using it back:
   ```sql
   DELETE FROM `tabWorkflow Transition`
     WHERE parent = 'Inbound Shipment V3 Flow'
       AND (state = 'Partially Received' OR next_state = 'Partially Received');
   DELETE FROM `tabWorkflow Document State`
     WHERE parent = 'Inbound Shipment V3 Flow' AND state = 'Partially Received';
   DELETE FROM `tabWorkflow State` WHERE name = 'Partially Received';
   UPDATE `tabInbound Shipment V3`
      SET workflow_state = 'In Transit' WHERE workflow_state = 'Partially Received';
   DELETE FROM `tabPatch Log`
     WHERE patch IN ('metactical.patches.add_partially_received_to_ins3_workflow',
                     'metactical.patches.short_receipt_notify_defaults');
   ```
3. The settings and `short_notified_on` are additive; clear them only if you
   want the tables clean:
   ```sql
   DELETE FROM `tabSingles` WHERE doctype = 'Procurement Settings V3'
     AND field IN ('notify_on_short_receipt', 'short_receipt_notify');
   ALTER TABLE `tabInbound Shipment V3` DROP COLUMN short_notified_on;
   ```
4. Build 2's behaviour returns: a receipt closes its shipment as Received on the
   first count. No order, receipt or shipment loses a quantity or a status.

### Known limits

- Rejected quantities are reported but do not themselves hold a shipment open.
- The alert is email only. If these should also land in Rocket.Chat, that is the
  same hook and a second recipient.

---

## Build 2 — 2026-11-01 — Inbound shipment: created from the confirmation, many tracking numbers, routes and ETAs

Branch `feature/inbound-shipment-v3-develop`, cut from `version-15-develop`.

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
Confirmation V3, Inbound Shipment V3, Purchase Receipt V3, Supplier Claim V3 and
Item Conversion V3 as merged upstream, with the inbound shipment created from
the PO and carrying a single tracking number.
