"""Check source rows against a field contract."""


def validate_rows(rows, spec):
    findings = []
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            errors, warnings = validate_row(row, spec)
        else:
            errors = ["Invalid row type: expected dictionary"]
            warnings = []

        findings.append(
            {
                "index": index,
                "row": row,
                "errors": errors,
                "warnings": warnings,
            }
        )
    return findings


def validate_row(row, spec, prefix=""):
    errors = _missing_and_null_errors(row, spec, prefix)
    warnings = _unexpected_field_warnings(row, spec, prefix)

    value_errors, value_warnings = _optional_and_value_findings(row, spec, prefix)
    errors.extend(value_errors)
    warnings.extend(value_warnings)
    return errors, warnings


def _missing_and_null_errors(row, spec, prefix):
    errors = []
    for field, rules in spec.items():
        field_path = _field_path(prefix, field)

        if rules["required"] and field not in row:
            errors.append(f"Missing required field: {field_path}")

        if not rules["nullable"] and field in row and row[field] is None:
            errors.append(f"Null not permitted: {field_path}")
    return errors


def _unexpected_field_warnings(row, spec, prefix):
    warnings = []
    for field in row:
        if field not in spec:
            warnings.append(f"Unexpected field: {_field_path(prefix, field)}")
    return warnings


def _optional_and_value_findings(row, spec, prefix):
    errors = []
    warnings = []
    for field, rules in spec.items():
        field_path = _field_path(prefix, field)

        if field not in row:
            if not rules["required"]:
                warnings.append(f"Optional field is missing: {field_path}")
            continue

        value = row[field]
        if value is None:
            continue

        if not _has_allowed_type(value, rules["type"]):
            errors.append(f"Invalid data type detected: {field_path}")
            continue

        if "fields" in rules:
            nested_errors, nested_warnings = validate_row(
                value, rules["fields"], field_path
            )
            errors.extend(nested_errors)
            warnings.extend(nested_warnings)
    return errors, warnings


def _has_allowed_type(value, allowed_types):
    if not isinstance(allowed_types, tuple):
        allowed_types = (allowed_types,)
    # Exact type, not isinstance: bool is a subclass of int, and True must not
    # pass as an integer.
    return type(value) in allowed_types


def _field_path(prefix, field):
    if prefix:
        return f"{prefix}.{field}"
    return field
