FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/bifurcate
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple .
RUN addgroup --system --gid 10001 bifurcate && adduser --system --uid 10001 --ingroup bifurcate bifurcate
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
