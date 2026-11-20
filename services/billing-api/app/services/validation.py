from app.core.errors import BadRequestError

MIN_YEAR = 2000
MAX_YEAR = 2100


def ensure_period(year: int, month: int) -> None:
    """Services validate their own inputs too; they must not rely on the HTTP layer."""
    if not 1 <= month <= 12:
        raise BadRequestError("month must be between 1 and 12.")
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise BadRequestError(f"year must be between {MIN_YEAR} and {MAX_YEAR}.")
