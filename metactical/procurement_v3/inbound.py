# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Carrier, tracking, destination and date handling for Inbound Shipment V3.

The shipment is raised from the supplier confirmation, which is the record of
what the supplier invoiced and is sending. Everything here is about getting the
shipment from the supplier's door to a stock warehouse without anyone having to
retype what another document already knows.
"""

import re

import frappe

ROUTE_DEFAULT_DAYS = 2


# ---------------------------------------------------------------------------
# Carrier tracking links.
#
# The URL template lives on the carrier's Supplier card so one edit fixes every
# shipment, past and future.
# ---------------------------------------------------------------------------
def track_url(carrier, tracking_no):
	if not carrier or not tracking_no:
		return None
	tpl = frappe.db.get_value("Supplier", carrier, "custom_tracking_url_template")
	if not tpl:
		return None
	num = str(tracking_no).strip()
	if "{tracking_no}" in tpl:
		return tpl.replace("{tracking_no}", num)
	return tpl + num


def check_service(carrier, service, where):
	if not service:
		return
	owner = frappe.db.get_value("Carrier Service", service, "carrier")
	if carrier and owner and owner != carrier:
		frappe.throw(where + ": service '" + str(service) + "' belongs to "
			+ str(owner) + ", not " + str(carrier) + ".")


# ---------------------------------------------------------------------------
# A shipment carries as many tracking numbers as the carrier chose to issue.
# One of them is the master the rest hang off - that is the first by default -
# and it is mirrored back onto tracking_no so older prints and reports that
# still read the single field keep working.
# ---------------------------------------------------------------------------
def sync_tracking(doc):
	# a number typed into the quick-add box, or several pasted at once
	if doc.add_tracking:
		seen = set((r.tracking_no or "").strip() for r in (doc.tracking or []))
		for raw in re.split(r"[\s,;]+", str(doc.add_tracking)):
			num = raw.strip()
			if num and num not in seen:
				doc.append("tracking", {"tracking_no": num})
				seen.add(num)
		doc.add_tracking = None

	# drop blanks and de-duplicate, keeping the first of each number
	keep, seen = [], set()
	for r in (doc.tracking or []):
		num = (r.tracking_no or "").strip()
		if not num or num in seen:
			continue
		r.tracking_no = num
		seen.add(num)
		keep.append(r)

	# exactly one master: whichever row is flagged, else the first
	master_rows = [r for r in keep if r.is_master]
	for r in keep:
		r.is_master = 0
	if keep:
		(master_rows[0] if master_rows else keep[0]).is_master = 1

	for idx, r in enumerate(keep, start=1):
		r.idx = idx
		# a number with no carrier of its own rides on the shipment's
		if not r.carrier:
			r.carrier = doc.carrier
		if not r.carrier_service and r.carrier == doc.carrier:
			r.carrier_service = doc.carrier_service
		check_service(r.carrier, r.carrier_service, "Tracking " + r.tracking_no)
		r.tracking_url = track_url(r.carrier, r.tracking_no)

	doc.tracking = keep

	master = next((r for r in keep if r.is_master), None)
	if master:
		if not doc.carrier:
			doc.carrier = master.carrier
		doc.tracking_no = master.tracking_no
		doc.tracking_url = master.tracking_url
	else:
		doc.tracking_no = None
		doc.tracking_url = None

	# boxes keep their own numbers; they just inherit the carrier
	for b in (doc.boxes or []):
		if not b.carrier:
			b.carrier = doc.carrier
		if not b.carrier_service and b.carrier == doc.carrier:
			b.carrier_service = doc.carrier_service
		check_service(b.carrier, b.carrier_service, "Box " + str(b.box_no or b.idx))
		b.tracking_url = track_url(b.carrier, b.tracking_no)


# ---------------------------------------------------------------------------
# Where the goods are going, and how they get there.
#
# The destination is the parent stock warehouse - bins are chosen at receiving,
# so naming one here would only be a guess. Freight that crosses at Blaine is
# re-shipped from there, which costs a day or two on top of the carrier's own
# transit time; the route carries that openly rather than leaving people to
# quietly pad the arrival date.
# ---------------------------------------------------------------------------
def resolve_destination(doc, po):
	if not doc.destination_warehouse:
		doc.destination_warehouse = po.set_warehouse

	doc.destination_warehouse = stock_warehouse(doc.destination_warehouse)
	doc.ship_to_display = warehouse_address(doc.destination_warehouse)

	if doc.route and doc.route != "Direct":
		if not doc.route_days:
			doc.route_days = ROUTE_DEFAULT_DAYS
	else:
		doc.route_days = 0


def stock_warehouse(warehouse):
	"""Walk a bin up to the stock warehouse it belongs to.

	Orders are routinely saved against a bin rather than the warehouse above
	it, and a shipment is destined for the warehouse - the bin is a receiving
	decision. Sites without warehouse_role keep whatever was given.
	"""
	if "warehouse_role" not in frappe.db.get_table_columns("Warehouse"):
		return warehouse

	seen = set()
	while warehouse and warehouse not in seen:
		seen.add(warehouse)
		row = frappe.db.get_value("Warehouse", warehouse,
			["is_group", "parent_warehouse", "warehouse_role"], as_dict=True)
		if not row:
			break
		if row.warehouse_role == "Active Stock" and not row.is_group:
			break
		if not row.parent_warehouse:
			break
		warehouse = row.parent_warehouse
	return warehouse


# Goods land in a stock warehouse and the bin is chosen at receiving, so the
# destination field must offer the handful of stock warehouses and not the
# thousands of bin records sitting under them. warehouse_role is how this ERP
# marks them; a site that does not use it falls back to the standard type, and
# a site using neither gets every warehouse rather than an empty list.
def stock_warehouse_filter():
	def pick():
		cols = frappe.db.get_table_columns("Warehouse")
		if "warehouse_role" in cols and frappe.db.exists(
				"Warehouse", {"warehouse_role": "Active Stock", "is_group": 0}):
			return "w.warehouse_role = 'Active Stock'"
		if "warehouse_type" in cols and frappe.db.exists(
				"Warehouse", {"warehouse_type": "Active", "is_group": 0}):
			return "IFNULL(w.warehouse_type, '') = 'Active'"
		return ""

	return frappe.cache().get_value("ins3_stock_warehouse_filter", pick)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def destination_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	where, values = ["w.is_group = 0", "IFNULL(w.disabled, 0) = 0"], {}

	narrow = stock_warehouse_filter()
	if narrow:
		where.append(narrow)

	if (filters or {}).get("company"):
		where.append("w.company = %(company)s")
		values["company"] = filters["company"]
	if txt:
		where.append("w.name LIKE %(txt)s")
		values["txt"] = "%" + txt + "%"

	values["start"] = start
	values["page_len"] = page_len
	return frappe.db.sql("""
		SELECT w.name, w.company
		FROM `tabWarehouse` w
		WHERE """ + " AND ".join(where) + """
		ORDER BY w.name
		LIMIT %(start)s, %(page_len)s
	""", values)


def warehouse_address(warehouse):
	if not warehouse:
		return None
	try:
		from frappe.contacts.doctype.address.address import (
			get_address_display, get_default_address)
		addr = get_default_address("Warehouse", warehouse)
		if addr:
			return get_address_display(addr)
	except Exception:
		# a warehouse with no address mapped is not a reason to refuse the save
		frappe.clear_last_message()
	return warehouse


# ---------------------------------------------------------------------------
# Ship date.
#
# Nobody wants to type it, so it fills itself from whatever the confirmation
# knows: the promised date if the supplier gave one, otherwise the invoice
# date, since a confirmation raised off a supplier invoice means the goods are
# leaving. A carrier pickup date, once tracking reports one, beats both. A date
# a person typed beats everything.
# ---------------------------------------------------------------------------
def resolve_ship_date(doc):
	if doc.ship_date_source == "Manual":
		return

	before = doc.get_doc_before_save()
	changed_by_hand = (doc.ship_date and before
		and str(before.ship_date or "") != str(doc.ship_date or ""))
	if changed_by_hand:
		doc.ship_date_source = "Manual"
		return
	if doc.ship_date and doc.ship_date_source:
		return
	if doc.ship_date and not before:
		# typed on a brand new shipment
		doc.ship_date_source = "Manual"
		return

	soc = doc.supplier_order_confirmation_v3
	if not soc:
		return
	row = frappe.db.get_value("Supplier Order Confirmation V3", soc,
		["promised_ship_date", "confirmation_date"], as_dict=True)
	if not row:
		return
	if row.promised_ship_date:
		doc.ship_date = row.promised_ship_date
		doc.ship_date_source = "Promised Ship Date"
	elif row.confirmation_date:
		doc.ship_date = row.confirmation_date
		doc.ship_date_source = "Supplier Invoice"


# ---------------------------------------------------------------------------
# Expected arrival is the date the stores read as the ETA on the POS, so it is
# always editable and a date somebody typed is never overwritten by the
# arithmetic here. Left alone it is the carrier's own arrival plus whatever the
# route adds.
# ---------------------------------------------------------------------------
def resolve_eta(doc):
	before = doc.get_doc_before_save()
	days = transit_days(doc)

	if doc.ship_date and days is not None:
		doc.carrier_eta = frappe.utils.add_days(doc.ship_date, days)

	computed = None
	if doc.carrier_eta:
		computed = frappe.utils.add_days(doc.carrier_eta, int(doc.route_days or 0))

	changed_by_hand = (before
		and str(before.expected_arrival or "") != str(doc.expected_arrival or ""))
	if changed_by_hand:
		doc.eta_source = "Manual"
	elif doc.expected_arrival and not before:
		doc.eta_source = "Manual"
	elif doc.eta_source in ("Manual", "Carrier API") and doc.expected_arrival:
		pass
	elif computed:
		doc.expected_arrival = computed
		doc.eta_source = "Transit Days"

	log_eta_change(doc, before)


def transit_days(doc):
	svc = doc.carrier_service
	if not svc:
		for r in (doc.tracking or []):
			if r.carrier_service:
				svc = r.carrier_service
				break
	if not svc:
		return None
	days = frappe.db.get_value("Carrier Service", svc, "transit_days")
	return int(days) if days else None


# ---------------------------------------------------------------------------
# The ETA is a promise the stores plan around, so every move is on the record
# with who moved it.
# ---------------------------------------------------------------------------
def log_eta_change(doc, before, who=None):
	if not before or not doc.name:
		return
	old, new = before.expected_arrival, doc.expected_arrival
	if str(old or "") == str(new or ""):
		return
	if not who:
		if doc.eta_source == "Transit Days":
			who = ("the carrier's transit time plus the route"
				if doc.route_days else "the carrier's transit time")
		else:
			who = {"Carrier API": "the carrier", "Manual": "a manual edit"}.get(
				doc.eta_source, "an update")
	doc.add_comment("Comment", eta_message(old, new, who))


def eta_message(old, new, who):
	fmt = frappe.utils.formatdate
	if old and new:
		moved = frappe.utils.date_diff(new, old)
		way = "later" if moved > 0 else "earlier"
		plural = "s" if abs(moved) != 1 else ""
		return ("ETA moved from <b>" + fmt(old) + "</b> to <b>" + fmt(new) + "</b> - "
			+ str(abs(moved)) + " day" + plural + " " + way + ", per " + who + ".")
	if new:
		return "ETA set to <b>" + fmt(new) + "</b>, per " + who + "."
	return "ETA cleared, per " + who + "."


# ---------------------------------------------------------------------------
# Marking a shipment Received submits it, so that is where the arrival is
# stamped. Keeping the promised date beside the real one is what turns a pile
# of shipments into a view of which carriers and suppliers deliver when they
# said they would.
# ---------------------------------------------------------------------------
def stamp_arrival(doc):
	if not doc.arrived_on:
		doc.arrived_on = frappe.utils.nowdate()
	doc.days_late = (frappe.utils.date_diff(doc.arrived_on, doc.expected_arrival)
		if doc.expected_arrival else 0)
	# on_submit runs after the row is written, so these need writing themselves
	doc.db_set("arrived_on", doc.arrived_on, update_modified=False)
	doc.db_set("days_late", doc.days_late, update_modified=False)


# ---------------------------------------------------------------------------
# One confirmation, one shipment.
#
# A confirmation is raised off the supplier's invoice, so submitting one means
# the goods are going. The shipment is created there and then, which is what
# gives the tracking number somewhere to live the moment it arrives by email.
# A supplier holding part of the order back is handled the other way round: a
# second confirmation is raised off the PO for the outstanding lines, and that
# one gets a shipment of its own.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# What is still coming on an order.
#
# This is the one place that answers it, because two callers have to agree: the
# shipment, when it lays out its own lines, and the confirmation's button, when
# it decides whether raising another shipment would carry anything. A back-
# ordered balance shipping weeks after the first wave is the whole reason the
# answer is not simply "everything on the order".
# ---------------------------------------------------------------------------
TERMINAL_LINE = ("Received", "Closed Short", "Cancelled",
	"Supplier Stock Out", "Discontinued")
NOT_COMING = ("Supplier Stock Out", "Discontinued", "Cancelled by Supplier")


def shipping_context(po, soc_name, exclude_name=None):
	promised, not_coming = {}, {}
	if soc_name:
		for ln in frappe.get_all("Supplier Order Confirmation V3 Item",
				filters={"parent": soc_name},
				fields=["po3_item", "item_code", "confirmed_qty", "line_status"]):
			promised[ln.po3_item] = ln
			if ln.line_status in NOT_COMING:
				not_coming[ln.po3_item] = ln.line_status

	rows, by_item = {}, {}
	for r in po.items:
		rows[r.name] = r
		by_item.setdefault(r.item_code, r.name)

	# goods already spoken for by another open shipment on this order
	others = frappe.get_all("Inbound Shipment V3",
		filters={"purchase_order_v3": po.name, "docstatus": 0,
			"name": ("!=", exclude_name or "")},
		pluck="name", limit_page_length=0)
	in_transit = {}
	for other in others:
		for si in frappe.get_all("Inbound Shipment V3 Item",
				filters={"parent": other}, fields=["po3_item", "qty"],
				limit_page_length=0):
			if si.po3_item:
				in_transit[si.po3_item] = in_transit.get(si.po3_item, 0) + float(si.qty or 0)

	return {"promised": promised, "not_coming": not_coming, "rows": rows,
		"by_item": by_item, "in_transit": in_transit, "open_shipments": others}


def lines_to_ship(po, ctx):
	"""The lines a new shipment on this order would carry, and how many."""
	out = []
	for r in po.items:
		if r.line_status in TERMINAL_LINE or r.name in ctx["not_coming"]:
			continue
		p = ctx["promised"].get(r.name)
		already = float(r.shipped_qty or 0) + ctx["in_transit"].get(r.name, 0)

		# A split line ships in two waves. Offer the confirmed-now portion until
		# that has actually shipped; after that the outstanding balance is what
		# is coming, so the whole ordered qty becomes the basis.
		backordered = (r.line_status == "Back-ordered") or (p and p.line_status in
			("Back-ordered", "Partial - Balance Back-ordered"))
		if p and backordered:
			now_qty = float(p.confirmed_qty or 0)
			base = now_qty if already < now_qty else float(r.qty or 0)
		elif p:
			base = float(p.confirmed_qty or 0)
		else:
			base = float(r.qty or 0)

		qty = base - already
		if qty > 0:
			out.append({"po3_item": r.name, "item_code": r.item_code, "qty": qty})
	return out


@frappe.whitelist()
def shipment_state(confirmation):
	"""What the confirmation's button should offer: open one, or raise another."""
	soc = frappe.db.get_value("Supplier Order Confirmation V3", confirmation,
		["purchase_order_v3", "docstatus"], as_dict=True)
	if not soc or soc.docstatus != 1:
		return {"shipments": [], "balance": 0}

	shipments = frappe.get_all("Inbound Shipment V3",
		filters={"supplier_order_confirmation_v3": confirmation, "docstatus": ("<", 2)},
		fields=["name", "workflow_state", "expected_arrival"], order_by="creation asc")

	po = frappe.get_doc("Purchase Order V3", soc.purchase_order_v3)
	if po.docstatus != 1:
		return {"shipments": shipments, "balance": 0}
	ctx = shipping_context(po, confirmation)
	return {"shipments": shipments, "balance": len(lines_to_ship(po, ctx))}


def shipment_for(soc_name):
	return frappe.db.get_value("Inbound Shipment V3",
		{"supplier_order_confirmation_v3": soc_name, "docstatus": ("<", 2)}, "name")


@frappe.whitelist()
def open_shipment(confirmation, another=0):
	"""Return the confirmation's shipment, creating the draft if there isn't one.

	A supplier holding part of the order back ships the balance weeks later, as
	its own delivery with its own carrier and tracking. That second delivery is
	a second shipment on the same confirmation - `another` asks for it, and the
	shipment itself works out what is left to carry.
	"""
	existing = shipment_for(confirmation)
	if existing and not frappe.utils.cint(another):
		return {"name": existing, "created": False}

	soc = frappe.get_doc("Supplier Order Confirmation V3", confirmation)
	if soc.docstatus == 2:
		frappe.throw(confirmation + " is cancelled.")

	doc = frappe.new_doc("Inbound Shipment V3")
	doc.purchase_order_v3 = soc.purchase_order_v3
	doc.supplier_order_confirmation_v3 = soc.name
	doc.source_type = soc.source_type or "Manual"
	doc.insert()
	return {"name": doc.name, "created": True}


def auto_create_shipment(soc):
	"""Called when a confirmation is submitted. Never blocks the submit."""
	if shipment_for(soc.name):
		return
	frappe.db.savepoint("auto_shipment")
	try:
		doc = frappe.new_doc("Inbound Shipment V3")
		doc.purchase_order_v3 = soc.purchase_order_v3
		doc.supplier_order_confirmation_v3 = soc.name
		doc.source_type = soc.source_type or "Manual"
		doc.insert()
	except Exception:
		# nothing left to ship, or the order is not in a state that allows it -
		# the confirmation still stands, and the shipment can be raised by hand
		frappe.db.rollback(save_point="auto_shipment")
		frappe.log_error(frappe.get_traceback(),
			"INS3 auto-create skipped for " + soc.name)
		return
	frappe.msgprint("Inbound shipment <b>" + doc.name + "</b> opened for this "
		"confirmation. Add the carrier and tracking numbers as they come in.",
		indicator="blue", alert=True)
