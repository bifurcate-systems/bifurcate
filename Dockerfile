FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/bifurcate
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
RUN addgroup --system --gid 10001 bifurcate && adduser --system --uid 10001 --ingroup bifurcate bifurcate
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
