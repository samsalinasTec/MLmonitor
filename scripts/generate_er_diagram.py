"""Generate ER diagram PNG from SQLAlchemy models."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from eralchemy2 import render_er
from mlmonitor.db.models import Base

output_path = os.path.join(os.path.dirname(__file__), "..", "artifacts", "er_diagram.png")
output_path = os.path.abspath(output_path)

os.makedirs(os.path.dirname(output_path), exist_ok=True)
render_er(Base, output_path)
print(f"ER diagram saved to: {output_path}")
