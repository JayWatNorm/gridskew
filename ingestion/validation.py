def validate_row(row, spec, prefix=""):
    errors = []
    warnings = []

    for field, rules in spec.items():
        field_path = f"{prefix}.{field}" if prefix else field

        if rules["required"]:
            if field not in row:
                errors.append(f"Missing required field: {field_path}")

        if not rules["nullable"]:
            if field in row and row[field] is None:
                errors.append(f"Null not permitted: {field_path}")

    for field in row:
        field_path = f"{prefix}.{field}" if prefix else field

        if field not in spec:
            warnings.append(f"Unexpected field: {field_path}")

    for field, rules in spec.items():
        field_path = f"{prefix}.{field}" if prefix else field

        if not rules["required"] and field not in row:
            warnings.append(f"Optional field is missing: {field_path}")

        if field in row and row[field] is not None:
            allowed_types = rules["type"]

            if not isinstance(allowed_types, tuple):
                allowed_types = (allowed_types,)

            if type(row[field]) not in allowed_types:
                errors.append(f"Invalid data type detected: {field_path}")
            elif "fields" in rules:
                nested_errors, nested_warnings = validate_row(
                    row[field],
                    rules["fields"],
                    field_path,
                )
                errors.extend(nested_errors)
                warnings.extend(nested_warnings)

    return errors, warnings


def validate_rows(rows, spec):
    findings = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors = ["Invalid row type: expected dictionary"]
            warnings = []
        else:
            errors, warnings = validate_row(row, spec)

        findings.append(
            {
                "index": index,
                "row": row,
                "errors": errors,
                "warnings": warnings,
            }
        )
    return findings
