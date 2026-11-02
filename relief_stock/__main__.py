"""Start the app: python -m relief_stock [data.db] [--port 8034]"""
import sys

from .fieldkit import main_cli
from .web import create_app

sys.exit(main_cli(create_app, "stock.db", 8034, sys.argv))
