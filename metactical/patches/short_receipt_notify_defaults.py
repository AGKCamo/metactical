# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Writes the short-delivery alert defaults into Procurement Settings V3.

A field's default in the doctype JSON only applies when a document is created.
Procurement Settings V3 already exists on every site, so without this the
recipients would come up blank and the alert would look switched off.

Reversing this patch:

    DELETE FROM `tabSingles`
     WHERE doctype = 'Procurement Settings V3'
       AND field IN ('notify_on_short_receipt', 'short_receipt_notify');
"""

import frappe

from metactical.procurement_v3.notify import DEFAULT_RECIPIENTS


def execute():
	if not frappe.db.exists("DocType", "Procurement Settings V3"):
		return

	settings = frappe.get_single("Procurement Settings V3")
	changed = False

	if not settings.get("short_receipt_notify"):
		settings.short_receipt_notify = DEFAULT_RECIPIENTS
		changed = True

	# only set the switch if nobody has ever touched it
	stored = frappe.db.get_value("Singles",
		{"doctype": "Procurement Settings V3", "field": "notify_on_short_receipt"}, "value",
		order_by=None)
	if stored is None:
		settings.notify_on_short_receipt = 1
		changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.db.commit()
		print("Procurement Settings V3: short delivery alerts on, going to "
			+ settings.short_receipt_notify)
