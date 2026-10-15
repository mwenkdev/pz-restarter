# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --no-create-home app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080
USER 10001
EXPOSE 8080
CMD ["python", "-m", "pz_restarter"]
