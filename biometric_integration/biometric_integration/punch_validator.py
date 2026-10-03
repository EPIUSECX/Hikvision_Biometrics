# -*- coding: utf-8 -*-
"""
Punch Validator - Validates punch sequences and resolves conflicts
"""

import frappe
from frappe.utils import getdate, get_datetime
from datetime import datetime


def validate_punch_sequence(checkins):
    """
    Validate a sequence of Employee Checkin records.

    Args:
        checkins: List of Employee Checkin records (sorted by time)

    Returns:
        dict: {
            "valid": bool,
            "errors": list,
            "warnings": list,
            "corrected_sequence": list
        }
    """
    if not checkins:
        return {"valid": False, "errors": ["No checkins provided"], "warnings": [], "corrected_sequence": []}

    errors = []
    warnings = []

    # Sort by time
    sorted_checkins = sorted(checkins, key=lambda x: x.time)

    # Track expected next type
    expected_type = "IN"
    in_count = 0
    out_count = 0

    for i, checkin in enumerate(sorted_checkins):
        log_type = checkin.log_type

        if log_type == "IN":
            in_count += 1
            if expected_type != "IN":
                errors.append(
                    f"Unexpected IN at {checkin.time} (expected OUT). "
                    f"Previous checkin was {sorted_checkins[i - 1].log_type} at {sorted_checkins[i - 1].time}"
                )
            expected_type = "OUT"
        elif log_type == "OUT":
            out_count += 1
            if expected_type != "OUT":
                if i == 0:
                    errors.append(f"First checkin is OUT at {checkin.time} (expected IN)")
                else:
                    errors.append(
                        f"Unexpected OUT at {checkin.time} (expected IN). "
                        f"Previous checkin was {sorted_checkins[i - 1].log_type} at {sorted_checkins[i - 1].time}"
                    )
            expected_type = "IN"

    # Check for unmatched IN/OUT
    if in_count > out_count:
        warnings.append(
            f"Missing {in_count - out_count} OUT checkin(s). Last checkin is IN at {sorted_checkins[-1].time}"
        )
    elif out_count > in_count:
        warnings.append(
            f"Missing {out_count - in_count} IN checkin(s). First checkin is OUT at {sorted_checkins[0].time}"
        )

    # Auto-correct common patterns
    corrected_sequence = auto_correct_sequence(sorted_checkins, errors, warnings)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "corrected_sequence": corrected_sequence,
    }


def auto_correct_sequence(checkins, errors, warnings):
    """
    Attempt to auto-correct common punch sequence issues.

    Args:
        checkins: List of checkins
        errors: List of errors
        warnings: List of warnings

    Returns:
        list: Corrected sequence (same as input if no corrections needed)
    """
    corrected = list(checkins)

    # If last checkin is IN and it's end of day, suggest adding OUT
    if warnings and "Missing" in str(warnings) and "OUT" in str(warnings):
        # Could add logic to suggest adding OUT at end of shift or end of day
        pass

    # If first checkin is OUT, suggest it might be IN
    if errors and any("First checkin is OUT" in str(e) for e in errors):
        # Could add logic to suggest changing first OUT to IN
        pass

    return corrected


def flag_invalid_sequences_for_review(employee, date, checkins):
    """
    Flag invalid punch sequences for manual review.

    Args:
        employee: Employee name
        date: Date
        checkins: List of checkin records

    Returns:
        str: Exception record name if created
    """
    validation = validate_punch_sequence(checkins)

    if validation["valid"] and not validation["warnings"]:
        return None

    # Create exception record for review
    try:
        # Check if exception doctype exists (would need to be created)
        if not frappe.db.exists("DocType", "Biometric Attendance Exception"):
            # Log to error log instead
            frappe.log_error(
                f"Invalid punch sequence for {employee} on {date}. "
                f"Errors: {validation['errors']}, Warnings: {validation['warnings']}",
                "Biometric Punch Sequence Validation",
            )
            return None

        exception = frappe.new_doc("Biometric Attendance Exception")
        exception.employee = employee
        exception.date = date
        exception.exception_type = "Invalid Punch Sequence"
        exception.description = f"Errors: {', '.join(validation['errors'])}. "
        exception.description += f"Warnings: {', '.join(validation['warnings'])}"
        exception.status = "Pending Review"
        exception.insert(ignore_permissions=True)

        return exception.name
    except Exception as e:
        frappe.log_error(f"Error creating exception record: {str(e)}", "Biometric Exception Creation Error")
        return None


def validate_biometric_punch_table(punch_table):
    """
    Validate punch sequence in Biometric Attendance Punch Table.

    Args:
        punch_table: List of punch table rows

    Returns:
        dict: Validation result
    """
    if not punch_table:
        return {"valid": False, "errors": ["No punches in table"], "warnings": []}

    # Sort by punch_time
    sorted_punches = sorted(punch_table, key=lambda x: x.punch_time or "")

    # Check for duplicate times
    times = [p.punch_time for p in sorted_punches if p.punch_time]
    duplicates = [t for t in times if times.count(t) > 1]

    errors = []
    warnings = []

    if duplicates:
        errors.append(f"Duplicate punch times found: {set(duplicates)}")

    # Check for reasonable time gaps (e.g., not multiple punches within seconds)
    for i in range(len(sorted_punches) - 1):
        if sorted_punches[i].punch_time and sorted_punches[i + 1].punch_time:
            time_diff = (sorted_punches[i + 1].punch_time - sorted_punches[i].punch_time).total_seconds()
            if 0 < time_diff < 60:  # Less than 1 minute apart
                warnings.append(
                    f"Punches very close together: {sorted_punches[i].punch_time} and {sorted_punches[i + 1].punch_time}"
                )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
