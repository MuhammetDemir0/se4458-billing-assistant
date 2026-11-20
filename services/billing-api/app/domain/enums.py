from enum import StrEnum


class UsageType(StrEnum):
    PHONE = "PHONE"
    INTERNET = "INTERNET"


class UsageUnit(StrEnum):
    MINUTE = "MINUTE"
    MB = "MB"


class BillStatus(StrEnum):
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
