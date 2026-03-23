from typing import List, Dict

def calculate_sum(sequence: List[int]) -> int:
    """
    Calculates the sum of a numeric sequence.

    Args:
        sequence: A list of integers.

    Returns:
        The sum of the integers.
    """
    return sum(sequence)

def calculate_average(sequence: List[int]) -> float:
    """
    Calculates the arithmetic mean of the sequence.

    Args:
        sequence: A list of integers.

    Returns:
        The average value as a float.
    """
    if not sequence:
        return 0.0
    return sum(sequence) / len(sequence)

def calculate_median(sequence: List[int]) -> float:
    """
    Calculates the median value of the sequence.

    Args:
        sequence: A list of integers.

    Returns:
        The median value as a float.
    """
    if not sequence:
        return 0.0
    sorted_seq = sorted(sequence)
    n = len(sorted_seq)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_seq[mid])
    else:
        return (sorted_seq[mid - 1] + sorted_seq[mid]) / 2.0

def get_frequency_distribution(sequence: List[int]) -> Dict[int, int]:
    """
    Returns a dictionary mapping each digit to its occurrence count.

    Args:
        sequence: A list of integers.

    Returns:
        A dictionary where keys are digits and values are counts.
    """
    distribution = {}
    for num in sequence:
        distribution[num] = distribution.get(num, 0) + 1
    return distribution