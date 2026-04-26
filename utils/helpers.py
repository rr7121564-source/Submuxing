import os
import shutil

def clean_temp_files(path):
    try:
        if os.path.isdir(path): shutil.rmtree(path)
        elif os.path.exists(path): os.remove(path)
    except Exception as e:
        print(f"Cleanup error: {e}")
