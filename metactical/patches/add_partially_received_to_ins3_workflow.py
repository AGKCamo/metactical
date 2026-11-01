# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt
"""Gives Inbound Shipment V3 a Partially Received state.

A container received over two days is one shipment and two receipts. Until this
existed the first count closed the whole shipment as Received, including lines
nobody had seen - which is exactly the case that needs chasing, hidden behind a
state that says everything arrived.

Workflows live in the database rather than in the app, so the state has to be
added to the record rather than shipped in a JSON file.

Reversing this patch:

    DELETE FROM `tabWorkflow Transition`
      WHERE parent = 'Inbound Shipment V3 Flow'
        AND (state = 'Partially Received' OR next_state = 'Partially Received');
    DELETE FROM `tabWorkflow Document State`
      WHERE parent = 'Inbound Shipment V3 Flow' AND state = 'Partially Received';
    DELETE FROM `tabWorkflow State` WHERE name = 'Partially Received';
    UPDATE `tabInbound Shipment V3`
       SET workflow_state = 'In Transit' WHERE workflow_state = 'Partially Received';
"""

import frappe

WORKFLOW = "Inbound Shipment V3 Flow"
STATE = "Partially Received"

# A shipment part-counted is still inbound, so it stays a draft with the same
# people able to work on it as In Transit.
DOC_STATE = {
	"state": STATE,
	"doc_status": "0",
	"allow_edit": "Stock User",
}

TRANSITIONS = [
	# from, action, to, who
	("In Transit", "Mark Partially Received", STATE, "Stock User"),
	(STATE, "Mark Received", "Received", "Stock User"),
	(STATE, "Mark In Transit", "In Transit", "Purchase User"),
]


def ensure_action(action):
	"""A transition's action is a Link to Workflow Action Master.

	Saving the workflow validates every transition, not just the new ones, so a
	single missing action master refuses the whole save - including the rows
	that were already there.
	"""
	if not frappe.db.exists("Workflow Action Master", action):
		frappe.get_doc({
			"doctype": "Workflow Action Master",
			"workflow_action_name": action,
		}).insert(ignore_permissions=True)


def execute():
	if not frappe.db.exists("Workflow", WORKFLOW):
		return

	if not frappe.db.exists("Workflow State", STATE):
		frappe.get_doc({
			"doctype": "Workflow State",
			"workflow_state_name": STATE,
			"style": "Warning",
		}).insert(ignore_permissions=True)

	wf = frappe.get_doc("Workflow", WORKFLOW)

	for t in wf.transitions:
		ensure_action(t.action)
	for _state, action, _next_state, _allowed in TRANSITIONS:
		ensure_action(action)

	if not any(s.state == STATE for s in wf.states):
		wf.append("states", DOC_STATE)

	existing = {(t.state, t.action, t.next_state) for t in wf.transitions}
	for state, action, next_state, allowed in TRANSITIONS:
		if (state, action, next_state) not in existing:
			wf.append("transitions", {
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": allowed,
			})

	wf.flags.ignore_permissions = True
	wf.save()
	frappe.db.commit()
	print("Inbound Shipment V3 Flow: %s state in place" % STATE)
