"""Configuración principal de la aplicación."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


APP_NAME = os.getenv("APP_NAME", "AI Inventory Assistant")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
"""Raíz del proyecto, derivada de la ubicación de este módulo, no del CWD."""

DATA_DIRECTORY: Path = PROJECT_ROOT / "data"
"""Directorio de datos locales del proyecto, ignorado por Git."""

DEFAULT_DATABASE_PATH: Path = DATA_DIRECTORY / "inventory.sqlite3"
"""Ruta predeterminada de la base durable, absoluta e independiente del CWD."""

INVENTORY_DB_PATH: str = (
    os.getenv("INVENTORY_DB_PATH") or str(DEFAULT_DATABASE_PATH)
)
"""Ruta configurada de la base del inventario, en texto.

Un valor ausente o vacío en el entorno usa la ruta predeterminada del proyecto.
"""


def resolve_database_path() -> Path:
    """
    Devuelve la ruta de la base del inventario y prepara su directorio (R2-R6).

    La ruta se toma de `INVENTORY_DB_PATH`; sin configuración —o con un valor
    vacío— se usa el archivo predeterminado del proyecto, derivado de
    `Path(__file__)` y nunca del directorio de trabajo, de modo que la
    aplicación abra siempre la misma base se inicie desde donde se inicie. Se
    crea el directorio padre cuando hace falta, para que la conexión pueda
    abrir el archivo.

    Returns:
        La ruta del archivo de la base, con su directorio padre creado.

    Raises:
        OSError: Si el directorio padre no se puede crear.
    """
    path = Path(INVENTORY_DB_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    return path
