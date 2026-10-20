FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY tbs_social ./tbs_social
RUN pip install --no-cache-dir .

# brand.toml and .env are mounted at runtime; the database lives on a volume
COPY brand.toml ./brand.toml
ENV TBS_DB_PATH=/data/tbs_social.db
VOLUME /data
EXPOSE 8000

# Binding to 0.0.0.0 requires TBS_DASHBOARD_PASSWORD; put HTTPS in front of it.
CMD ["python", "-m", "tbs_social", "serve", "--host", "0.0.0.0", "--port", "8000", "--monitor-every", "15"]
