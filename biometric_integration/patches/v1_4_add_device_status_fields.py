import frappe


def execute():
    """
    Add device status fields to Biometric Device table:
    - status (Select: Online, Offline, Error)
    - last_status_check (Datetime)
    - last_error (Small Text)
    """

    dt = "Biometric Device"

    # Status field
    if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": "status"}):
        cf = frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": dt,
                "fieldname": "status",
                "label": "Status",
                "fieldtype": "Select",
                "options": "\nOnline\nOffline\nError",
                "default": "Unknown",
                "insert_after": "is_active",
            }
        )
        cf.insert(ignore_permissions=True)

    # Last status check
    if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": "last_status_check"}):
        cf = frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": dt,
                "fieldname": "last_status_check",
                "label": "Last Status Check",
                "fieldtype": "Datetime",
                "insert_after": "status",
            }
        )
        cf.insert(ignore_permissions=True)

    # Last error
    if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": "last_error"}):
        cf = frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": dt,
                "fieldname": "last_error",
                "label": "Last Error",
                "fieldtype": "Small Text",
                "insert_after": "last_status_check",
            }
        )
        cf.insert(ignore_permissions=True)

    frappe.clear_cache(doctype=dt)
