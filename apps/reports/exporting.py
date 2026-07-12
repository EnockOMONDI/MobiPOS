CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def safe_csv_row(values):
    return [safe_csv_cell(value) for value in values]
