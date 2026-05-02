"""Flask blueprints for the dashboard.

Each module here exposes a `bp` Blueprint that the main app registers.
Extracted from dashboard/app.py as part of Phase C.
"""
from .pipeline_routes import bp as pipeline_bp
from .vods_routes import bp as vods_bp
from .models_routes import bp as models_bp
from .hardware_routes import bp as hardware_bp
from .paths_routes import bp as paths_bp
from .originality_routes import bp as originality_bp
from .music_routes import bp as music_bp
from .assets_routes import bp as assets_bp

ALL_BLUEPRINTS = (
    pipeline_bp, vods_bp, models_bp, hardware_bp,
    paths_bp, originality_bp, music_bp, assets_bp,
)
