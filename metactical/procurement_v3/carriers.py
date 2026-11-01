# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Carrier tracking lookups for Inbound Shipment V3.

What a carrier tells us - picked up on this date, now at this status, now
expected on that date - is the only source that keeps an ETA honest once the
freight is moving. This module is the seam: everything that happens to a
shipment when a carrier reports in is written and tested here, and each carrier
needs only an adapter that turns its own API into the small result below.

No adapter ships with credentials. Until one is registered and configured, a
lookup says so plainly instead of guessing, and nothing on the shipment moves.
"""

import frappe

# What every adapter returns. Any field it cannot fill stays None, and None
# always means "the carrier did not say", never "no".
FIELDS = ("status", "picked_up_on", "expected_on", "delivered_on")


class NotConfigured(Exception):
	"""No adapter for this carrier, or it has no credentials yet."""


# Carrier Supplier name -> dotted path of a function(tracking_no) -> dict.
# Filled in as each carrier's API is set up; FedEx, UPS, DHL, Purolator and
# Canada Post are the ones that matter for inbound freight.
ADAPTERS = {}


def adapter_for(carrier):
	path = ADAPTERS.get(carrier)
	if not path:
		raise NotConfigured("No tracking API is set up for " + str(carrier) + " yet.")
	return frappe.get_attr(path)


def lookup(carrier, tracking_no):
	"""Ask the carrier about one number. Raises NotConfigured if we cannot."""
	fn = adapter_for(carrier)
	raw = fn(tracking_no) or {}
	return {k: raw.get(k) for k in FIELDS}


# ---------------------------------------------------------------------------
# Applying what the carrier said.
#
# The expected arrival is what the stores read as the ETA on the POS, so when
# the carrier moves it we move it too, rather than leaving the floor planning
# around a date the carrier has already abandoned. Every move is left on the
# document's timeline with the old date, the new one and who moved it.
# ---------------------------------------------------------------------------
@frappe.whitelist()
def refresh(shipment):
	"""Re-check every tracking number on a shipment and apply what comes back."""
	from metactical.procurement_v3 import inbound

	doc = frappe.get_doc("Inbound Shipment V3", shipment)
	before_eta = doc.expected_arrival
	checked, problems, latest = 0, [], None

	for row in (doc.tracking or []):
		carrier = row.carrier or doc.carrier
		if not carrier or not row.tracking_no:
			continue
		try:
			said = lookup(carrier, row.tracking_no)
		except NotConfigured as e:
			problems.append(str(e))
			continue
		except Exception as e:
			problems.append(row.tracking_no + ": " + str(e))
			continue

		checked += 1
		row.carrier_status = said["status"]
		row.carrier_eta = said["expected_on"]
		row.delivered_on = said["delivered_on"]
		row.last_checked = frappe.utils.now()

		# the carrier's own pickup date beats anything we inferred, but never
		# a date somebody typed
		if said["picked_up_on"] and doc.ship_date_source != "Manual":
			doc.ship_date = said["picked_up_on"]
			doc.ship_date_source = "Carrier Pickup"

		# the shipment arrives when its last piece does
		if said["expected_on"] and (not latest or said["expected_on"] > latest):
			latest = said["expected_on"]

	if latest:
		doc.carrier_eta = latest
		doc.expected_arrival = frappe.utils.add_days(latest, int(doc.route_days or 0))
		doc.eta_source = "Carrier API"

	if checked:
		doc.save()
		if str(before_eta or "") != str(doc.expected_arrival or ""):
			doc.add_comment("Comment", inbound.eta_message(
				before_eta, doc.expected_arrival, "the carrier"))

	# one carrier with no adapter yet is one message, not one per number
	seen, unique = set(), []
	for p in problems:
		if p not in seen:
			seen.add(p)
			unique.append(p)

	return {"checked": checked, "problems": unique,
		"expected_arrival": doc.expected_arrival}


# ---------------------------------------------------------------------------
# Nightly sweep over everything still moving. Shipments that have arrived or
# been cancelled are left alone - there is nothing left for a carrier to say.
# ---------------------------------------------------------------------------
def poll_in_transit():
	if not ADAPTERS:
		return
	names = frappe.get_all("Inbound Shipment V3",
		filters={"docstatus": 0, "workflow_state": "In Transit"},
		pluck="name", limit_page_length=0)
	for name in names:
		try:
			refresh(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "Carrier poll failed for " + name)
