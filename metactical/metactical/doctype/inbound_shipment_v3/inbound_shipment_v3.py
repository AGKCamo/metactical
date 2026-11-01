# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from metactical.procurement_v3 import inbound
from metactical.procurement_v3.utils import F, mirror_po3_status


class InboundShipmentV3(Document):
	def validate(self):
		validate(self)

	def after_insert(self):
		auto_in_transit(self)

	def on_submit(self):
		inbound.stamp_arrival(self)
		record_shipment(self)

	def before_cancel(self):
		cancel_guard(self)


# ---------------------------------------------------------------------------
# Migrated from Server Script "INS3 Validate"
# (DocType Event / Before Save on Inbound Shipment V3).
#
# Lays out what this shipment is carrying from the confirmation's promises less
# whatever is already in transit, warns on lines the supplier said were not
# coming and on over-shipment, totals the boxes, and validates the carrier /
# service pair and tracking link.
#
# track_url / check_service stay nested: they are defined mid-script in the
# original and read the enclosing scope.
# ---------------------------------------------------------------------------
def validate(doc):
	po = frappe.get_doc("Purchase Order V3", doc.purchase_order_v3)
	if po.docstatus != 1:
		frappe.throw("Purchase Order V3 " + po.name + " is not approved yet.")

	# there is exactly one confirmation per PO - attach it rather than asking
	if not doc.supplier_order_confirmation_v3:
		hit = frappe.get_all("Supplier Order Confirmation V3",
			filters={"purchase_order_v3": po.name, "docstatus": ("<", 2)},
			fields=["name"], order_by="creation desc", limit_page_length=1)
		if hit:
			doc.supplier_order_confirmation_v3 = hit[0].name

	ctx = inbound.shipping_context(po, doc.supplier_order_confirmation_v3, doc.name)
	promised = ctx["promised"]
	not_coming = ctx["not_coming"]
	rows = ctx["rows"]
	by_item = ctx["by_item"]
	in_transit = ctx["in_transit"]

	# empty grid -> lay out what is still expected to arrive
	if not doc.items:
		for line in inbound.lines_to_ship(po, ctx):
			d = doc.append("items", {})
			d.po3_item = line["po3_item"]
			d.item_code = line["item_code"]
			d.qty = line["qty"]
		if not doc.items:
			msg = ("Nothing left to ship on " + po.name
				+ " - every line is received, closed, already on another shipment, "
				+ "or the supplier said it is not coming.")
			if ctx["open_shipments"]:
				msg = msg + "<br><br>Open shipments on this order already carry: " \
					+ ", ".join(ctx["open_shipments"])
			frappe.throw(msg)

	total_qty = 0.0
	box_counts = {}
	warn_not_coming = []
	warn_over = []
	for d in doc.items:
		if not d.po3_item and d.item_code in by_item:
			d.po3_item = by_item[d.item_code]
		total_qty += F(d.qty)
		if d.box_no:
			box_counts[d.box_no] = box_counts.get(d.box_no, 0) + 1
		if d.po3_item in not_coming:
			warn_not_coming.append((d.item_code or "?") + " (" + not_coming[d.po3_item] + ")")
		p = promised.get(d.po3_item)
		if p:
			r = rows.get(d.po3_item)
			already = (F(r.shipped_qty) + in_transit.get(d.po3_item, 0)) if r else 0
			split = (r and r.line_status == "Back-ordered") or (p.line_status in
				("Back-ordered", "Partial - Balance Back-ordered"))
			# a split line is promised in full, just in two waves
			promised_total = F(r.qty) if (split and r) else F(p.confirmed_qty)
			allowed = promised_total - already
			if F(d.qty) > allowed + 0.000001:
				warn_over.append((d.item_code or "?") + ": shipping " + str(F(d.qty))
					+ " but only " + str(allowed) + " still due")

	# information, not obstruction - the goods are physically here either way
	if warn_not_coming:
		frappe.msgprint("<b>Heads up:</b> the confirmation says these were NOT coming, "
			+ "but they are on this shipment:<br>" + "<br>".join(warn_not_coming))
	if warn_over:
		frappe.msgprint("<b>Heads up:</b> more than the supplier confirmed:<br>"
			+ "<br>".join(warn_over))

	doc.total_qty = total_qty
	doc.total_boxes = len(doc.boxes or [])
	tw = 0.0
	for b in (doc.boxes or []):
		b.item_count = box_counts.get(b.box_no, 0)
		tw += F(b.weight_kg)
	doc.total_weight_kg = tw

	mirror_po3_status(po.name)

	# ---- carrier, tracking, destination and dates -------------------
	inbound.check_service(doc.carrier, doc.carrier_service, "Shipment")
	inbound.sync_tracking(doc)
	inbound.resolve_destination(doc, po)
	inbound.resolve_ship_date(doc)
	inbound.resolve_eta(doc)

	# ---- a shipment with shipping details on it is not a draft ------
	if (doc.docstatus == 0 and doc.workflow_state in (None, "", "Draft")
			and frappe.db.exists("Inbound Shipment V3", doc.name)):
		# a new doc cannot be moved here - Frappe rejects the transition on
		# insert, so INS3 Auto In Transit picks it up afterwards instead
		has_track = 1 if doc.tracking_no else 0
		for b in doc.boxes:
			if b.tracking_no:
				has_track = 1
		if has_track or (doc.carrier and doc.ship_date):
			doc.workflow_state = "In Transit"
			frappe.msgprint("Shipping details added - moved to <b>In Transit</b>. "
				"It stays fully editable.")


# ---------------------------------------------------------------------------
# Migrated from Server Script "INS3 Auto In Transit"
# (DocType Event / After Insert on Inbound Shipment V3).
#
# A shipment created with tracking (or a carrier plus a ship date) is already
# on its way, so it opens as In Transit rather than Draft.
# ---------------------------------------------------------------------------
def auto_in_transit(doc):
	if doc.docstatus == 0 and doc.workflow_state in (None, "", "Draft"):
		has_track = 1 if doc.tracking_no else 0
		for b in doc.boxes:
			if b.tracking_no:
				has_track = 1
		if has_track or (doc.carrier and doc.ship_date):
			# set_value writes past the workflow transition check, which refuses
			# any state but Draft on a document that has just been created
			frappe.db.set_value("Inbound Shipment V3", doc.name,
				"workflow_state", "In Transit", update_modified=False)
			frappe.msgprint("Shipping details present - created as <b>In Transit</b>. "
				"It stays fully editable.")


# ---------------------------------------------------------------------------
# Migrated from Server Script "INS3 On Submit"
# (DocType Event / After Submit on Inbound Shipment V3).
#
# Adds this shipment's quantities to each PO3 line's shipped_qty, back-fills
# customs / origin / weight onto the Item where those were still blank, and
# marks the confirmation's Back Order rows this shipment covers as Shipped.
# ---------------------------------------------------------------------------
def record_shipment(doc):
	shipped = {}
	for d in doc.items:
		if d.po3_item:
			shipped[d.po3_item] = shipped.get(d.po3_item, 0.0) + F(d.qty)
	for po3_item, q in shipped.items():
		prev = F(frappe.db.get_value("Purchase Order V3 Item", po3_item, "shipped_qty"))
		frappe.db.set_value("Purchase Order V3 Item", po3_item, {"shipped_qty": prev + q})

	for d in doc.items:
		if not d.item_code:
			continue
		item = frappe.db.get_value("Item", d.item_code,
			["customs_tariff_number", "country_of_origin", "weight_per_unit"], as_dict=True)
		upd = {}
		if d.hs_code and not item.customs_tariff_number:
			upd["customs_tariff_number"] = d.hs_code
		if d.country_of_origin and not item.country_of_origin:
			upd["country_of_origin"] = d.country_of_origin
		if F(d.unit_weight) and not F(item.weight_per_unit):
			upd["weight_per_unit"] = F(d.unit_weight)
		if upd:
			frappe.db.set_value("Item", d.item_code, upd)


	# --- mark the Back Orders rows this shipment covers ---
	soc = doc.supplier_order_confirmation_v3
	if not soc:
		soc = frappe.db.get_value("Supplier Order Confirmation V3",
			{"purchase_order_v3": doc.purchase_order_v3, "docstatus": ("<", 2)}, "name")
	if soc:
		flagged = []
		for d in doc.items:
			if not d.po3_item:
				continue
			shipped_so_far = F(frappe.db.get_value("Purchase Order V3 Item", d.po3_item, "shipped_qty"))
			for b in frappe.get_all("Supplier Order Confirmation V3 Backorder",
					filters={"parent": soc, "po3_item": d.po3_item, "status": "Open"},
					fields=["name", "shipping_now"]):
				# the confirmed-now portion shipping is NOT the back-order shipping
				if shipped_so_far <= F(b.shipping_now):
					continue
				frappe.db.set_value("Supplier Order Confirmation V3 Backorder", b.name, {
					"status": "Shipped",
					"shipment": doc.name,
					"shipped_on": doc.ship_date or frappe.utils.nowdate(),
					"eta": doc.expected_arrival or frappe.db.get_value(
						"Supplier Order Confirmation V3 Backorder", b.name, "eta")})
				flagged.append(d.item_code or "?")
		if flagged:
			frappe.msgprint("Back Orders on " + soc + " marked <b>Shipped</b>: " + ", ".join(flagged))


# ---------------------------------------------------------------------------
# Migrated from Server Script "INS3 Cancel Guard"
# (DocType Event / Before Cancel on Inbound Shipment V3).
#
# A shipment that already has a receipt booked against it cannot be cancelled;
# the receipt has to go first.
# ---------------------------------------------------------------------------
def cancel_guard(doc):
	blockers = []
	for g in frappe.get_all("Purchase Receipt V3",
			filters={"inbound_shipment_v3": doc.name, "docstatus": ("<", 2)},
			fields=["name", "workflow_state"], limit_page_length=0):
		blockers.append("receipt " + g.name + " (" + str(g.workflow_state)
			+ ") was booked against this shipment")
	if blockers:
		frappe.throw("<b>" + doc.name + " cannot be cancelled.</b><br><br>"
			+ "<br>".join(blockers)
			+ "<br><br>Cancel or delete the receipt first.")
