from typing import List, Optional

def parse_numeric_sequence(input_string: str) -> Optional[List[int]]:
    """
    Parses a string of digits into a list of integers.

    Args:
        input_string: The raw string to parse (e.g., '212323123123').

    Returns:
        A list of integers representing the digits, or None if input is invalid.
    """
    if not input_string.isdigit():
        return None
    return [int(char) for char in input_string]