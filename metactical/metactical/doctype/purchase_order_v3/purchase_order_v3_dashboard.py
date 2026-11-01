# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt


def get_data():
	return {
		"fieldname": "purchase_order_v3",
		"transactions": [
			{
				"label": "Confirmation",
				"items": ["Supplier Order Confirmation V3"],
			},
			{
				"label": "Inbound",
				"items": ["Inbound Shipment V3", "Goods Receipt V3"],
			},
			{
				"label": "Problems",
				"items": ["Supplier Claim V3"],
			},
		],
	}
