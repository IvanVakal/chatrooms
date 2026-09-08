#!/usr/bin/env bash
set -e

export FLASK_APP=app.py

flask db upgrade
exec gunicorn app:app --bind 0.0.0.0:8000
