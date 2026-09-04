def validate_row(row, spec):
    errors = []
    warnings = []

    for field, rules in spec.items():
        if rules["required"]:
            if field not in row:
                errors.append(f"Missing required field: {field}")

        if not rules["nullable"]:
            if field in row and row[field] is None:
                errors.append(f"Null not permitted: {field}")

    for field in row:
        if field not in spec:
            warnings.append(f"Unexpected field: {field}")

    for field, rules in spec.items():
        if not rules["required"] and field not in row:
            warnings.append(f"Optional field is missing: {field}")

        if field in row and row[field] is not None:
            allowed_types = rules["type"]

            if not isinstance(allowed_types, tuple):
                allowed_types = (allowed_types,)

            if type(row[field]) not in allowed_types:
                errors.append(f"Invalid data type detected: {field}")

    return errors, warnings


def run(results, spec):
    findings = []
    for index, row in enumerate(results):
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
