"""casa-certa: compare Airbnb houses for a family trip."""

import os

# Set before pydantic_ai is imported anywhere: it prints a promotional banner unless this variable is present.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
