FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

USER 65532:65532

EXPOSE 8765
ENTRYPOINT ["overwatch", "--host", "0.0.0.0"]
CMD ["--demo"]
