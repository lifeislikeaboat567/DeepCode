FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first for layer caching
COPY pyproject.toml ./
COPY deepcode/__init__.py deepcode/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy remaining source
COPY . .

# Create data directory
RUN mkdir -p /data
ENV DEEPCODE_DATA_DIR=/data
ENV DEEPCODE_DB_URL=sqlite+aiosqlite:///data/deepcode.db

# Expose API and UI ports
EXPOSE 8000 8501

CMD ["deepcode", "serve", "--host", "0.0.0.0", "--port", "8000"]
