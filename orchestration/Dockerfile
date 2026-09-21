# Dagster webserver and daemon. Only the dependencies live in the image (installed from uv.lock, so they are the ones the host venv uses);
# the code (scripts, sql, dbt_spark, orchestration) and the landing folder are bind-mounted by docker-compose.yml.
FROM python:3.11-slim
RUN pip install --no-cache-dir uv==0.12.10
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONPATH=/app
RUN uv sync --frozen --no-default-groups --group dbt --group orchestration --no-install-project
