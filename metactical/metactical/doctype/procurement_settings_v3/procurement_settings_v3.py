# Copyright (c) 2026, Storebuilder Commerce Inc and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from metactical.procurement_v3 import BUILD


class ProcurementSettingsV3(Document):
	def onload(self):
		# read from the code, not the database, so it is always the build that
		# is actually running rather than the one someone last saved
		self.procurement_v3_build = str(BUILD)
