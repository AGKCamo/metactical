# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Move each shipment's single tracking number into the tracking table.

Carriers issue a master number with the rest hanging off it, so the number
became a table. Every existing shipment has at most one, which becomes its
master row; tracking_no itself is kept, now as a read-only mirror of that row,
so older prints and reports carry on working.

Reversing this patch: the child rows can be dropped, because nothing was taken
off the shipment to create them -

    DELETE FROM `tabInbound Shipment V3 Tracking`;
"""

import frappe


def execute():
	frappe.reload_doc("metactical", "doctype", "inbound_shipment_v3_tracking")
	frappe.reload_doc("metactical", "doctype", "inbound_shipment_v3")

	rows = frappe.db.sql("""
		SELECT name, tracking_no, tracking_url, carrier, carrier_service
		FROM `tabInbound Shipment V3`
		WHERE IFNULL(tracking_no, '') != ''
	""", as_dict=True)

	already = set(frappe.db.sql_list("""
		SELECT DISTINCT parent FROM `tabInbound Shipment V3 Tracking`
	"""))

	moved = 0
	for r in rows:
		if r.name in already:
			continue
		child = frappe.new_doc("Inbound Shipment V3 Tracking")
		child.update({
			"parent": r.name,
			"parenttype": "Inbound Shipment V3",
			"parentfield": "tracking",
			"idx": 1,
			"tracking_no": r.tracking_no,
			"is_master": 1,
			"carrier": r.carrier,
			"carrier_service": r.carrier_service,
			"tracking_url": r.tracking_url,
		})
		child.db_insert()
		moved += 1

	# existing rows predate the route, and every one of them went direct
	frappe.db.sql("""
		UPDATE `tabInbound Shipment V3`
		SET route = 'Direct', route_days = 0
		WHERE IFNULL(route, '') = ''
	""")

	# the arrival date they already carry was typed by a person
	frappe.db.sql("""
		UPDATE `tabInbound Shipment V3`
		SET eta_source = 'Manual'
		WHERE expected_arrival IS NOT NULL AND IFNULL(eta_source, '') = ''
	""")
	frappe.db.sql("""
		UPDATE `tabInbound Shipment V3`
		SET ship_date_source = 'Manual'
		WHERE ship_date IS NOT NULL AND IFNULL(ship_date_source, '') = ''
	""")

	frappe.db.commit()
	print("Inbound Shipment V3: moved %d tracking number(s) into the table" % moved)
