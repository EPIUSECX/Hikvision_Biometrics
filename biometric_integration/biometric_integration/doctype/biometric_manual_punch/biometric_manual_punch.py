# Copyright (c) 2025, NDV and contributors
# For license information, please see license.txt

from datetime import datetime, timedelta

import frappe
from frappe.model.document import Document

from biometric_integration.employee_checkin_sync import (
    _punch_time_key,
    sync_punches_to_employee_checkin,
)


class BiometricManualPunch(Document):
    def validate(self):
        attendance_device_id = frappe.db.get_value(
            "Employee", self.employee, "attendance_device_id"
        )
        if not attendance_device_id:
            frappe.throw("Set an Attendance Device ID on this Employee before adding a correction.")

        punch_time = str(self.punch_time).split(".")[0]
        event_time = datetime.strptime(punch_time, "%H:%M:%S").time()
        attendance_log = frappe.db.get_value(
            "Biometric Attendance Log",
            {"employee_no": attendance_device_id, "event_date": self.punch_date},
            "name",
        )
        if not attendance_log:
            return

        existing_times = frappe.get_all(
            "Biometric Attendance Punch Table",
            filters={"parent": attendance_log},
            pluck="punch_time",
        )
        if any(_punch_time_key(value) == _punch_time_key(event_time) for value in existing_times):
            frappe.throw("A device event already exists for this employee at that date and time.")

    def after_insert(self):
        result = _add_manual_punch(self.employee, self.punch_date, self.punch_time, commit=False)
        if result["status"] != "success":
            frappe.throw(result["message"])

        created, reconciled = sync_punches_to_employee_checkin(commit=False)
        frappe.msgprint(
            f"{result['message']} HRMS check-ins updated: {created} created, "
            f"{reconciled} reconciled.",
            title="Manual Correction Applied",
            indicator="green",
        )

@frappe.whitelist()
def add_manual_punch(employee, punch_date, punch_time):
    return _add_manual_punch(employee, punch_date, punch_time, commit=True)


def _add_manual_punch(employee, punch_date, punch_time, commit=False):
    try:
        employee_name = frappe.db.get_value('Employee', employee, 'employee_name')
        attendance_device_id = frappe.db.get_value('Employee', employee, 'attendance_device_id')
        
        if not attendance_device_id:
            return {'status': 'error', 'message': f"Attendance Device ID not found for {employee_name}"}

        # Remove fractional seconds, if any, from punch_time
        punch_time = str(punch_time).split('.')[0]
        punch_datetime = datetime.strptime(f"{punch_date} {punch_time}", '%Y-%m-%d %H:%M:%S')

        query = """
            SELECT name FROM `tabBiometric Attendance Log`
            WHERE employee_no = %s AND event_date = %s
        """
        attendance_log = frappe.db.sql(query, (attendance_device_id, punch_datetime.date()), as_dict=True)

        if attendance_log:
            doc = frappe.get_doc('Biometric Attendance Log', attendance_log[0].name)
        else:
            doc = frappe.get_doc({'doctype': 'Biometric Attendance Log', 'employee_no': attendance_device_id, 'event_date': punch_datetime.date()})

        punches = []
        for punch in doc.get('punch_table', []):
            punch_time_value = punch.punch_time
            if isinstance(punch_time_value, str):
                punch_time_value = datetime.strptime(punch_time_value, '%H:%M:%S').time()
            elif isinstance(punch_time_value, timedelta):
                punch_time_value = (datetime.min + punch_time_value).time()
            punches.append({'punch_time': punch_time_value, 'punch_type': punch.punch_type})

        # Check if the punch time already exists (ignoring fractional seconds)
        if any(p['punch_time'] == punch_datetime.time() for p in punches):
            return {'status': 'error', 'message': f"Manual punch for {employee_name} on {punch_date} at {punch_time} already exists. Document will not be saved."}
        
        punches.append({'punch_time': punch_datetime.time(), 'punch_type': 'Manual'})
        punches.sort(key=lambda x: x['punch_time'])

        doc.set('punch_table', [])
        for punch in punches:
            doc.append('punch_table', punch)

        doc.save(ignore_permissions=True)
        if commit:
            frappe.db.commit()

        return {'status': 'success', 'message': f"Manual punch for {employee_name} on {punch_date} at {punch_time} added successfully."}

    except frappe.ValidationError as e:
        return {'status': 'error', 'message': str(e)}
    except Exception as e:
        return {'status': 'error', 'message': f"Error adding manual punch: {str(e)}"}


def delete_manual_punch(doc, method):
    try:
        employee = doc.employee
        punch_date = doc.punch_date
        punch_time = doc.punch_time

        # Fetch the attendance_device_id from the Employee doctype
        employee_name = frappe.db.get_value('Employee', employee, 'employee_name')
        attendance_device_id = frappe.db.get_value('Employee', employee, 'attendance_device_id')
        if not attendance_device_id:
            frappe.throw(f"Attendance Device ID not found for employee {employee}")

        # Check if an Attendance Log exists for the given employee and date
        attendance_log = frappe.db.sql("""
            SELECT name 
            FROM `tabBiometric Attendance Log` 
            WHERE employee_no = %s AND event_date = %s
        """, (attendance_device_id, punch_date), as_dict=True)

        if not attendance_log:
            frappe.throw(f"No attendance log found for employee {employee_name} on {punch_date}")

        attendance_log_name = attendance_log[0].name

        # Delete the punch from the punch_table
        frappe.db.sql("""
            DELETE FROM `tabBiometric Attendance Punch Table` 
            WHERE parent = %s AND punch_time = %s AND punch_type = 'Manual'
        """, (attendance_log_name, punch_time))

        frappe.db.commit()

        frappe.msgprint(f"Manual punch for employee {employee_name} on {punch_date} at {punch_time} deleted successfully.")
        return

    except Exception as e:
        frappe.throw(f"Error deleting manual punch: {str(e)}")
