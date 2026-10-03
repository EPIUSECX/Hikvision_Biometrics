# Copyright (c) 2025, NDV and Contributors
# See license.txt

from datetime import time, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime
from hrms.hr.doctype.employee_checkin.employee_checkin import calculate_working_hours

from biometric_integration.employee_checkin_sync import (
    _punch_time_key,
    sync_punches_to_employee_checkin,
)


class TestBiometricIntegrationSettings(FrappeTestCase):
    def test_device_and_database_time_values_have_same_key(self):
        self.assertEqual(
            _punch_time_key(time(11, 30, 15)),
            _punch_time_key(timedelta(hours=11, minutes=30, seconds=15)),
        )

    def test_multiple_daily_checkin_checkout_pairs(self):
        device_employee_id = "BIOMETRIC-MULTI-PUNCH-TEST"
        employee = frappe.db.get_value(
            "Employee",
            {"status": "Active"},
            "name",
        )
        if not employee:
            self.skipTest("An active Employee is required for biometric integration tests")

        frappe.db.set_value("Employee", employee, "attendance_device_id", device_employee_id)

        frappe.db.set_single_value("Biometric Integration Settings", "auto_create_attendance", 0)
        if frappe.get_meta("HR Settings").has_field("allow_geolocation_tracking"):
            frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 0)

        attendance_log = frappe.get_doc(
            {
                "doctype": "Biometric Attendance Log",
                "employee_no": device_employee_id,
                "event_date": "2099-01-15",
                "punch_table": [
                    {"punch_time": "08:00:00", "punch_type": "Auto"},
                    {"punch_time": "08:00:00", "punch_type": "Auto"},
                    {"punch_time": "10:00:00", "punch_type": "Auto"},
                    {"punch_time": "10:00:00", "punch_type": "Auto"},
                    {"punch_time": "11:00:00", "punch_type": "Auto"},
                    {"punch_time": "11:00:00", "punch_type": "Auto"},
                    {"punch_time": "12:00:00", "punch_type": "Auto"},
                    {"punch_time": "12:00:00", "punch_type": "Auto"},
                ],
            }
        ).insert(ignore_permissions=True)

        sync_punches_to_employee_checkin(commit=False)
        self._assert_checkin_sequence(
            employee,
            [
                ("2099-01-15 08:00:00", "IN"),
                ("2099-01-15 10:00:00", "OUT"),
                ("2099-01-15 11:00:00", "IN"),
                ("2099-01-15 12:00:00", "OUT"),
            ],
        )

        # Both copies of a duplicate device event must link to one check-in.
        punch_links = frappe.get_all(
            "Biometric Attendance Punch Table",
            filters={"parent": attendance_log.name},
            fields=["punch_time", "employee_checkin", "synced_to_employee_checkin"],
            order_by="punch_time, creation",
        )
        self.assertEqual(len({row.employee_checkin for row in punch_links}), 4)
        self.assertTrue(all(row.synced_to_employee_checkin for row in punch_links))

        # A later sync must continue the day's sequence rather than restarting at IN.
        attendance_log.reload()
        attendance_log.append("punch_table", {"punch_time": "13:00:00", "punch_type": "Auto"})
        attendance_log.append("punch_table", {"punch_time": "14:00:00", "punch_type": "Auto"})
        attendance_log.save(ignore_permissions=True)

        sync_punches_to_employee_checkin(commit=False)
        expected_sequence = [
            ("2099-01-15 08:00:00", "IN"),
            ("2099-01-15 10:00:00", "OUT"),
            ("2099-01-15 11:00:00", "IN"),
            ("2099-01-15 12:00:00", "OUT"),
            ("2099-01-15 13:00:00", "IN"),
            ("2099-01-15 14:00:00", "OUT"),
        ]
        checkins = self._assert_checkin_sequence(employee, expected_sequence)

        working_hours, in_time, out_time = calculate_working_hours(
            checkins,
            "Strictly based on Log Type in Employee Checkin",
            "Every Valid Check-in and Check-out",
        )
        self.assertEqual(working_hours, 4.0)
        self.assertEqual(in_time, get_datetime("2099-01-15 08:00:00"))
        self.assertEqual(out_time, get_datetime("2099-01-15 14:00:00"))

        # Once every punch is linked, another run is a no-op.
        self.assertEqual(sync_punches_to_employee_checkin(commit=False), (0, 0))

    def _assert_checkin_sequence(self, employee, expected):
        checkins = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": employee,
                "time": [
                    "between",
                    [get_datetime("2099-01-15 00:00:00"), get_datetime("2099-01-15 23:59:59")],
                ],
            },
            fields=["name", "time", "log_type"],
            order_by="time",
        )
        self.assertEqual(
            [(row.time, row.log_type) for row in checkins],
            [(get_datetime(timestamp), log_type) for timestamp, log_type in expected],
        )
        return [frappe.get_doc("Employee Checkin", row.name) for row in checkins]
