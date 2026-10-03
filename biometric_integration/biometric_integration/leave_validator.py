# -*- coding: utf-8 -*-
"""
Leave and Holiday Validator - Checks leave applications and holidays
Uses HRMS standard utility functions
"""

import frappe
from frappe.utils import getdate
from frappe.query_builder import DocType

# HRMS standard imports
from hrms.hr.utils import get_holidays_for_employee, get_holiday_dates_for_employee


def check_leave_status(employee, date):
    """
    Check if employee is on leave for the given date using HRMS pattern.
    Uses the same logic as Attendance.check_leave_record().

    Args:
        employee: Employee name
        date: Date to check

    Returns:
        dict: {
            "on_leave": bool,
            "half_day": bool,
            "leave_type": str or None
        }
    """
    if not frappe.db.exists("DocType", "Leave Application"):
        return {"on_leave": False, "half_day": False, "leave_type": None}

    date = getdate(date)

    # Use HRMS pattern (same as Attendance.check_leave_record)
    LeaveApplication = DocType("Leave Application")
    leave_record = (
        frappe.qb.from_(LeaveApplication)
        .select(
            LeaveApplication.leave_type,
            LeaveApplication.half_day,
            LeaveApplication.half_day_date,
            LeaveApplication.name,
        )
        .where(
            (LeaveApplication.employee == employee)
            & (date >= LeaveApplication.from_date)
            & (date <= LeaveApplication.to_date)
            & (LeaveApplication.status == "Approved")
            & (LeaveApplication.docstatus == 1)
        )
    ).run(as_dict=True)

    if not leave_record:
        return {"on_leave": False, "half_day": False, "leave_type": None}

    # Check if it's a half day
    leave_app = leave_record[0]
    is_half_day = False

    if leave_app.half_day and leave_app.half_day_date == date:
        is_half_day = True

    return {"on_leave": True, "half_day": is_half_day, "leave_type": leave_app.leave_type}


def check_holiday(employee, date, holiday_list=None):
    """
    Check if the given date is a holiday for the employee using HRMS function.

    Args:
        employee: Employee name
        date: Date to check
        holiday_list: Holiday list name (optional, will fetch from employee if not provided)

    Returns:
        bool: True if date is a holiday
    """
    date = getdate(date)

    # Use HRMS function to get holiday dates
    holiday_dates = get_holiday_dates_for_employee(employee, date, date)

    return date in holiday_dates


def get_holiday_list_for_employee(employee):
    """
    Get holiday list for an employee using HRMS utility.

    Args:
        employee: Employee name

    Returns:
        str: Holiday list name or None
    """
    from erpnext.setup.doctype.employee.employee import (
        get_holiday_list_for_employee as erpnext_get_holiday_list,
    )

    return erpnext_get_holiday_list(employee)
