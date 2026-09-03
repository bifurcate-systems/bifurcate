FROM registry.re8ch.com/library/comfy-agent-python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/bifurcate
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple .
RUN addgroup --system --gid 10001 bifurcate && adduser --system --uid 10001 --ingroup bifurcate bifurcate
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
