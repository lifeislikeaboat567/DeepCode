from typing import List, Tuple

def find_repeating_patterns(sequence: List[int], max_length: int = 3) -> List[Tuple[str, int]]:
    """
    Detects repeating subsequences within the numeric sequence.

    Args:
        sequence: A list of integers.
        max_length: The maximum length of subsequence to check.

    Returns:
        A list of tuples containing the pattern string and its repetition count.
    """
    if not sequence:
        return []
    
    input_str = ''.join(map(str, sequence))
    results = []
    length = len(sequence)
    
    for l in range(2, min(max_length + 1, length // 2 + 1)):
        pattern = input_str[:l]
        full_match = (length % l == 0) and (pattern * (length // l) == input_str)
        
        if full_match:
            results.append((pattern, length // l))
    
    # Sort by repetition count descending
    return sorted(results, key=lambda x: x[1], reverse=True)