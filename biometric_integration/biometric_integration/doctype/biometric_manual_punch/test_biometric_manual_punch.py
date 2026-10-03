# Copyright (c) 2025, NDV and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime


class TestBiometricManualPunch(FrappeTestCase):
    def test_manual_corrections_update_hrms_checkins(self):
        employee = frappe.db.get_value(
            "Employee",
            {"status": "Active"},
            "name",
        )
        if not employee:
            self.skipTest("An active Employee is required for biometric integration tests")

        frappe.db.set_value(
            "Employee", employee, "attendance_device_id", "BIOMETRIC-MANUAL-TEST"
        )
        frappe.db.set_single_value("Biometric Integration Settings", "auto_create_attendance", 0)
        if frappe.get_meta("HR Settings").has_field("allow_geolocation_tracking"):
            frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 0)

        for punch_time in ("09:00:00", "17:00:00"):
            frappe.get_doc(
                {
                    "doctype": "Biometric Manual Punch",
                    "employee": employee,
                    "punch_date": "2099-02-20",
                    "punch_time": punch_time,
                }
            ).insert(ignore_permissions=True)

        checkins = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": employee,
                "time": [
                    "between",
                    [
                        get_datetime("2099-02-20 00:00:00"),
                        get_datetime("2099-02-20 23:59:59"),
                    ],
                ],
            },
            fields=["time", "log_type"],
            order_by="time",
        )

        self.assertEqual(
            [(row.time, row.log_type) for row in checkins],
            [
                (get_datetime("2099-02-20 09:00:00"), "IN"),
                (get_datetime("2099-02-20 17:00:00"), "OUT"),
            ],
        )
