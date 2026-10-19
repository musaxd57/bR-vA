"""Explicit integration points for the additive Briva v2 domain.

register_core(app) registers routes only. create_core_schema() creates missing
briva_* tables; it never alters, replaces or removes a legacy table or file.
"""
from extensions import db
from . import models


def register_core(app):
    from .api import blueprint
    if "briva_core" not in app.blueprints:
        app.register_blueprint(blueprint)


def create_core_schema():
    from flask import current_app
    tables = [table for table in db.metadata.sorted_tables if table.name in models.CORE_TABLE_NAMES]
    db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)
    current_app.extensions["briva_core_schema_ready"] = True
    return [table.name for table in tables]
