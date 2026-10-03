# -*- coding: utf-8 -*-
"""
Payroll Integration - Links attendance to payroll processing
"""

import frappe
from frappe.utils import getdate
from datetime import datetime, timedelta


def link_attendance_to_payroll(employee, start_date, end_date, payroll_entry=None):
    """
    Link attendance records to a payroll entry.

    Args:
        employee: Employee name
        start_date: Start date of payroll period
        end_date: End date of payroll period
        payroll_entry: Payroll Entry name (optional, will create if not provided)

    Returns:
        str: Payroll Entry name
    """
    if not frappe.db.exists("DocType", "Payroll Entry"):
        frappe.logger().warning("Payroll Entry doctype not found. Payroll module may not be installed.")
        return None

    try:
        # Get attendance records for the period
        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "employee": employee,
                "attendance_date": ["between", [start_date, end_date]],
                "docstatus": 1,
            },
            fields=["name", "attendance_date", "status", "working_hours"],
        )

        if not attendance_records:
            return None

        # Link to payroll entry if provided
        if payroll_entry:
            for att in attendance_records:
                if frappe.db.has_column("Attendance", "payroll_entry"):
                    frappe.db.set_value("Attendance", att.name, "payroll_entry", payroll_entry)

        return payroll_entry

    except Exception as e:
        frappe.log_error(f"Error linking attendance to payroll: {str(e)}", "Payroll Integration Error")
        return None


def get_attendance_summary_for_payroll(employee, start_date, end_date):
    """
    Get attendance summary for payroll processing.

    Args:
        employee: Employee name
        start_date: Start date of payroll period
        end_date: End date of payroll period

    Returns:
        dict: Attendance summary
    """
    try:
        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "employee": employee,
                "attendance_date": ["between", [start_date, end_date]],
                "docstatus": 1,
            },
            fields=["attendance_date", "status", "working_hours", "check_in", "check_out"],
        )

        total_days = len(attendance_records)
        present_days = sum(1 for att in attendance_records if att.status == "Present")
        half_days = sum(1 for att in attendance_records if att.status == "Half Day")
        absent_days = sum(1 for att in attendance_records if att.status == "Absent")

        total_hours = sum((att.working_hours or 0) for att in attendance_records)

        # Calculate overtime if available
        total_overtime = 0
        for att in attendance_records:
            if frappe.db.has_column("Attendance", "custom_overtime_hours"):
                overtime = frappe.db.get_value("Attendance", att.name, "custom_overtime_hours") or 0
                total_overtime += overtime

        return {
            "total_days": total_days,
            "present_days": present_days,
            "half_days": half_days,
            "absent_days": absent_days,
            "total_hours": total_hours,
            "total_overtime": total_overtime,
            "attendance_records": attendance_records,
        }

    except Exception as e:
        frappe.log_error(f"Error getting attendance summary: {str(e)}", "Payroll Integration Error")
        return {
            "total_days": 0,
            "present_days": 0,
            "half_days": 0,
            "absent_days": 0,
            "total_hours": 0,
            "total_overtime": 0,
            "attendance_records": [],
        }


def export_attendance_for_payroll(payroll_period_start, payroll_period_end):
    """
    Export attendance data for payroll processing.

    Args:
        payroll_period_start: Start date of payroll period
        payroll_period_end: End date of payroll period

    Returns:
        list: List of attendance records with employee details
    """
    try:
        attendance_data = frappe.db.sql(
            """
            SELECT
                a.employee,
                e.employee_name,
                a.attendance_date,
                a.status,
                a.working_hours,
                a.check_in,
                a.check_out
            FROM `tabAttendance` a
            INNER JOIN `tabEmployee` e ON e.name = a.employee
            WHERE a.attendance_date BETWEEN %(start_date)s AND %(end_date)s
            AND a.docstatus = 1
            ORDER BY a.employee, a.attendance_date
        """,
            {"start_date": payroll_period_start, "end_date": payroll_period_end},
            as_dict=True,
        )

        return attendance_data

    except Exception as e:
        frappe.log_error(f"Error exporting attendance for payroll: {str(e)}", "Payroll Integration Error")
        return []
