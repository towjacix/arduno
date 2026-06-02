from pathlib import Path
from typing import Any

import tomllib


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.toml"


class AppConfig:
    def __init__(self):
        self._config = self._load_toml()

    def _load_toml(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return {"mode": "manual", "temp_offset": 10, "window_size": 30}
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        data = self._config
        for k in keys:
            if isinstance(data, dict):
                data = data.get(k)
            else:
                return default
        return data if data is not None else default

    def __getitem__(self, key: str) -> Any:
        return self._config.get(key)


CONFIG = AppConfig()
