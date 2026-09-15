"""Load local configuration without logging credentials or overwriting shell values."""
import os
from pathlib import Path


def load_environment(root=None):
    from dotenv import dotenv_values
    root = Path(root or Path(__file__).resolve().parents[1])
    local = root / '.env'
    reference = root / '.env.example'
    loaded = []
    # Compatibility for the user's existing Neo4j settings in .env.example.
    # Only Neo4j keys are accepted there, not example model/provider values.
    for path, prefix in ((local, 'KG_'), (reference, 'KG_NEO4J_')):
        if not path.is_file():
            continue
        for key, value in dotenv_values(path, interpolate=False).items():
            if key.startswith(prefix) and value and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded
