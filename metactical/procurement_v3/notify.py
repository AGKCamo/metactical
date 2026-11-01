# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Telling people when a shipment did not arrive whole.

A short delivery is money: it has been invoiced, it may have been paid, and
only the people chasing the supplier can do anything about it. The receipt
already works out what fell short - this is what gets it off the screen of
whoever happened to be counting and in front of accounting and the buyers.

It sends once per shipment. A second receipt against the same shipment does not
start the conversation again.
"""

import frappe

DEFAULT_RECIPIENTS = "accounting@goldenplazadistributors.com, po@goldenplazadistributors.com"

# The supplier told us these were not coming on this shipment, so their absence
# is the plan rather than a problem. Chasing them would be crying wolf.
EXPECTED_ABSENT = ("Back-ordered", "Partial - Balance Back-ordered",
	"Supplier Stock Out", "Discontinued", "Cancelled by Supplier")


def recipients():
	raw = frappe.db.get_single_value("Procurement Settings V3", "short_receipt_notify")
	raw = (raw or DEFAULT_RECIPIENTS).replace(";", ",").replace("\n", ",")
	return [a.strip() for a in raw.split(",") if a.strip() and "@" in a]


def enabled():
	"""On unless somebody turned it off.

	A Check field that has never been written reads back as 0, not as the
	doctype's default, so asking for the value alone would quietly disable
	this on every existing site. The stored row is what says a human decided.
	"""
	stored = frappe.db.get_value("Singles",
		{"doctype": "Procurement Settings V3", "field": "notify_on_short_receipt"},
		"value", order_by=None)
	if stored is None:
		return True
	return bool(frappe.utils.cint(stored))


# ---------------------------------------------------------------------------
# What went missing.
#
# Short means the shipment said it was carrying something and it was not all
# there. Rejected is a different problem - it arrived and failed inspection -
# so it is counted and reported separately rather than folded in.
# ---------------------------------------------------------------------------
def shortfall(shipment):
	from metactical.metactical.doctype.purchase_receipt_v3.purchase_receipt_v3 import (
		shipment_balance, shipment_key)

	doc = frappe.get_doc("Inbound Shipment V3", shipment)
	shipped, taken = shipment_balance(shipment)

	# lines the confirmation already said were not coming on this wave
	excused = set()
	if doc.supplier_order_confirmation_v3:
		for ln in frappe.get_all("Supplier Order Confirmation V3 Item",
				filters={"parent": doc.supplier_order_confirmation_v3,
					"line_status": ("in", EXPECTED_ABSENT)},
				fields=["po3_item", "item_code"]):
			excused.add(shipment_key(ln.po3_item, ln.item_code))

	short = []
	for row in doc.items:
		key = shipment_key(row.po3_item, row.item_code)
		if key in excused:
			continue
		missing = float(shipped.get(key, 0)) - float(taken.get(key, 0))
		if missing > 0.0000001:
			short.append({
				"item_code": row.item_code,
				"item_name": row.item_name or "",
				"carried": float(shipped.get(key, 0)),
				"received": float(taken.get(key, 0)),
				"missing": missing,
			})

	rejected = []
	for r in frappe.get_all("Purchase Receipt V3",
			filters={"inbound_shipment_v3": shipment, "docstatus": 1},
			pluck="name"):
		for gi in frappe.get_all("Purchase Receipt V3 Item", filters={"parent": r},
				fields=["received_item_code", "item_name", "rejected_qty", "reject_reason"]):
			if float(gi.rejected_qty or 0) > 0:
				rejected.append({
					"item_code": gi.received_item_code,
					"item_name": gi.item_name or "",
					"qty": float(gi.rejected_qty),
					"reason": gi.reject_reason or "",
				})

	boxes = list(doc.boxes or [])
	boxes_missing = [b.box_no for b in boxes if not b.received] if boxes else []

	return {"doc": doc, "short": short, "rejected": rejected,
		"boxes_total": len(boxes), "boxes_missing": boxes_missing}


def _table(headers, rows):
	head = "".join("<th align='left' style='padding:4px 10px'>%s</th>" % h for h in headers)
	body = ""
	for r in rows:
		body += "<tr>" + "".join(
			"<td style='padding:4px 10px'>%s</td>" % c for c in r) + "</tr>"
	return ("<table border='1' cellspacing='0' style='border-collapse:collapse;"
		"font-size:13px'><tr>%s</tr>%s</table>" % (head, body))


def compose(found):
	doc = found["doc"]
	po = frappe.db.get_value("Purchase Order V3", doc.purchase_order_v3,
		["supplier_name", "name"], as_dict=True) or frappe._dict()

	bits = []
	bits.append("<p><b>%s</b> has been received and did not arrive whole.</p>"
		% doc.name)

	facts = [
		("Supplier", doc.supplier_name or po.supplier_name or doc.supplier or ""),
		("Purchase order", doc.purchase_order_v3 or ""),
		("Confirmation", doc.supplier_order_confirmation_v3 or ""),
		("Carrier", doc.carrier or ""),
		("Tracking", doc.tracking_no or ""),
		("Expected", frappe.utils.formatdate(doc.expected_arrival) if doc.expected_arrival else ""),
		("Arrived", frappe.utils.formatdate(doc.arrived_on) if doc.arrived_on else ""),
	]
	bits.append(_table(["", ""], [(k, v) for k, v in facts if v]))

	if found["short"]:
		bits.append("<p><b>Short</b> - the shipment said it was carrying these "
			"and they were not all there:</p>")
		bits.append(_table(["Item", "Description", "Carried", "Received", "Short"],
			[(s["item_code"], s["item_name"], "%g" % s["carried"],
			  "%g" % s["received"], "<b>%g</b>" % s["missing"]) for s in found["short"]]))

	if found["boxes_missing"]:
		bits.append("<p><b>Boxes</b> - %d of %d on the packing list were not "
			"checked in: %s</p>" % (len(found["boxes_missing"]), found["boxes_total"],
				", ".join(str(b) for b in found["boxes_missing"])))

	if found["rejected"]:
		bits.append("<p><b>Rejected</b> - these arrived but failed inspection, "
			"which is a separate conversation with the supplier:</p>")
		bits.append(_table(["Item", "Description", "Qty", "Reason"],
			[(r["item_code"], r["item_name"], "%g" % r["qty"], r["reason"])
			 for r in found["rejected"]]))

	site = frappe.utils.get_url()
	bits.append("<p>%s &middot; %s &middot; %s</p>" % (
		"<a href='%s/app/inbound-shipment-v3/%s'>Shipment</a>" % (site, doc.name),
		"<a href='%s/app/purchase-order-v3/%s'>Order</a>" % (site, doc.purchase_order_v3),
		"<a href='%s/app/supplier-order-confirmation-v3/%s'>Confirmation</a>"
			% (site, doc.supplier_order_confirmation_v3) if doc.supplier_order_confirmation_v3 else ""))

	subject = "Short delivery: %s on %s (%s)" % (
		doc.name, doc.purchase_order_v3, doc.supplier_name or doc.supplier or "")
	return subject, "".join(bits)


# ---------------------------------------------------------------------------
# Sent once, when the shipment is settled as Received and something is missing.
# ---------------------------------------------------------------------------
def short_shipment(shipment):
	if not enabled():
		return False

	doc_notified = frappe.db.get_value("Inbound Shipment V3", shipment, "short_notified_on")
	if doc_notified:
		return False

	found = shortfall(shipment)
	if not (found["short"] or found["boxes_missing"] or found["rejected"]):
		return False

	to = recipients()
	if not to:
		return False

	subject, message = compose(found)
	try:
		frappe.sendmail(recipients=to, subject=subject, message=message,
			reference_doctype="Inbound Shipment V3", reference_name=shipment)
	except Exception:
		# Receiving goods must not fail because outgoing mail is misconfigured.
		# The stamp is deliberately not set, so this is tried again on the next
		# receipt, and the shortfall is on the document either way.
		frappe.log_error(frappe.get_traceback(),
			"Short delivery email failed for " + shipment)
		found["doc"].add_comment("Comment",
			"<b>Short delivery</b> on this shipment - the alert to "
			+ ", ".join(to) + " could not be sent. See the error log.")
		return False

	frappe.db.set_value("Inbound Shipment V3", shipment,
		"short_notified_on", frappe.utils.now(), update_modified=False)
	found["doc"].add_comment("Comment",
		"Short delivery reported to " + ", ".join(to) + ".")
	return True
