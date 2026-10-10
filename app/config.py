"""Configuración principal de la aplicación."""

import os

from dotenv import load_dotenv


load_dotenv()


APP_NAME = os.getenv("APP_NAME", "AI Inventory Assistant")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")
