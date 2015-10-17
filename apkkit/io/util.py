"""I/O-related utility functions for APK Kit."""

try:
    from os import scandir  # Python 3.5! :D
except ImportError:
    from scandir import scandir  # Python older-than-3.5!


def recursive_size(path):
    """Calculate the size of an entire directory tree, starting at `path`."""
    running_total = 0

    for entry in scandir(path):
        if entry.is_file():
            running_total += entry.stat().st_size
        elif entry.is_dir():
            running_total += recursive_size(entry.path)

    return running_total
