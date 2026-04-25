import os

def clean_temp_files(*filepaths):
    """Safely removes temporary files."""
    for path in filepaths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                print(f"Failed to delete {path}: {e}")
