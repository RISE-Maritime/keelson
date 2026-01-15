"""MCAP connector specific test utilities."""

from pathlib import Path
from typing import List, Tuple

from mcap.reader import make_reader


def validate_mcap_files(
    mcap_files: List[Path],
    require_messages: bool = False,
    allow_incomplete_last: bool = True,
) -> Tuple[List[Tuple[Path, object]], List[Path]]:
    """
    Validate a list of MCAP files and return valid/invalid file lists.

    Args:
        mcap_files: List of MCAP file paths to validate
        require_messages: If True, files must contain at least one message
        allow_incomplete_last: If True, the last file (by name) may be invalid

    Returns:
        Tuple of (valid_files, invalid_files) where valid_files is a list of
        (path, summary) tuples and invalid_files is a list of paths
    """
    valid_files = []
    invalid_files = []

    sorted_files = sorted(mcap_files)

    for i, mcap_file in enumerate(sorted_files):
        is_last = i == len(sorted_files) - 1
        try:
            with open(mcap_file, "rb") as f:
                reader = make_reader(f)
                summary = reader.get_summary()

                if summary is None:
                    if allow_incomplete_last and is_last:
                        invalid_files.append(mcap_file)
                    else:
                        invalid_files.append(mcap_file)
                    continue

                if require_messages:
                    # Check if file has messages via statistics
                    has_messages = False
                    if summary.statistics and summary.statistics.message_count > 0:
                        has_messages = True
                    # Fallback: check if there are channels (implies messages were expected)
                    elif len(summary.channels) > 0:
                        has_messages = True

                    if not has_messages and not (allow_incomplete_last and is_last):
                        invalid_files.append(mcap_file)
                        continue

                valid_files.append((mcap_file, summary))
        except Exception:
            if allow_incomplete_last and is_last:
                invalid_files.append(mcap_file)
            else:
                invalid_files.append(mcap_file)

    return valid_files, invalid_files
