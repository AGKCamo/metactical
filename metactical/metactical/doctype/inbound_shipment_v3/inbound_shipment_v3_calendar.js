// Copyright (c) 2026, Storebuilder Commerce Inc and contributors
// For license information, please see license.txt

// Receiving plans the week around what is arriving, so the shipments sit on a
// calendar by their expected arrival rather than only in a list by creation.
frappe.views.calendar['Inbound Shipment V3'] = {
    field_map: {
        start: 'expected_arrival',
        end: 'expected_arrival',
        id: 'name',
        title: 'supplier_name',
        allDay: 'allDay',
        status: 'workflow_state'
    },
    get_css_class: function (data) {
        if (data.workflow_state === 'Received') return 'success';
        if (data.workflow_state === 'Cancelled') return 'danger';
        if (data.workflow_state === 'In Transit'
            || data.workflow_state === 'Partially Received') return 'warning';
        return 'info';
    },
    get_events_method: 'frappe.desk.calendar.get_events'
};
