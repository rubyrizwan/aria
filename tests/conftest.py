from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ.setdefault("APICHECKER_MASTER_KEY", Fernet.generate_key().decode())
os.environ.setdefault("APICHECKER_DATABASE_URL", "sqlite:///:memory:")
