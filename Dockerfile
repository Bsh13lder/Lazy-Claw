FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lazyclaw/ ./lazyclaw/
COPY pyproject.toml ./
RUN pip install -e .
COPY .env.example .env.example
EXPOSE 18789
CMD ["lazyclaw", "start"]
