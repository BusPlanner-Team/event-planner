#!/bin/bash
# Azure App Service startup command
# Using 1 worker to avoid duplicate scheduler instances
gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 1 wsgi:app
