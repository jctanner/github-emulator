"""Validate release tags without interpreting CalVer as SemVer."""
import datetime
import re
import sys


def validate(value: str) -> str:
    if not re.fullmatch(r"20[0-9]{2}\.[0-9]{2}\.[0-9]{2}(?:\.[1-9][0-9]*)?", value):
        raise ValueError("expected YYYY.MM.DD or YYYY.MM.DD.N (N >= 1)")
    datetime.date.fromisoformat(value[:10].replace(".", "-"))
    return value


if __name__ == "__main__":
    print(validate(sys.argv[1]))
