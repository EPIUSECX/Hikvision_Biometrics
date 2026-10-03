import frappe
from frappe.tests.utils import FrappeTestCase

from biometric_integration.biometric_integration.report.biometric_daily_report.biometric_daily_report import (
    execute,
)


class TestBiometricDailyReport(FrappeTestCase):
    def test_duplicate_device_events_are_displayed_once(self):
        employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not employee:
            self.skipTest("An active Employee is required for biometric integration tests")

        device_id = "BIOMETRIC-REPORT-TEST"
        frappe.db.set_value("Employee", employee, "attendance_device_id", device_id)

        frappe.get_doc(
            {
                "doctype": "Biometric Attendance Log",
                "employee_no": device_id,
                "event_date": "2099-02-21",
                "punch_table": [
                    {"punch_time": "09:00:00", "punch_type": "Auto"},
                    {"punch_time": "09:00:00", "punch_type": "Auto"},
                    {"punch_time": "17:00:00", "punch_type": "Auto"},
                    {"punch_time": "17:00:00", "punch_type": "Auto"},
                ],
            }
        ).insert(ignore_permissions=True)

        columns, data = execute({"date": "2099-02-21"})
        row = next(item for item in data if item.get("employee_id") == device_id)

        self.assertEqual(row["punch_1"], "09:00")
        self.assertEqual(row["punch_2"], "17:00")
        self.assertNotIn("punch_3", row)
        self.assertEqual(row["total_duration"], "08:00")
        self.assertEqual(row["review_status"], "Complete")
        self.assertEqual(
            [column["fieldname"] for column in columns if column["fieldname"].startswith("punch_")],
            ["punch_1", "punch_2"],
        )
