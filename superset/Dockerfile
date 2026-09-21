FROM apache/superset:6.1.0
USER root
# Trino driver + SQLAlchemy dialect, same client version as the rest of the project
RUN uv pip install --python /app/.venv/bin/python "trino[sqlalchemy]==0.339.0"
USER superset
