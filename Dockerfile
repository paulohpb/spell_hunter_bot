FROM python:3.11-slim

# Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libfontconfig1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Setup non-root user for security
RUN useradd -m botuser
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
RUN chown -R botuser:botuser /app

USER botuser

# Auto-detect docker environment
ENV DOCKER_CONTAINER=true

CMD ["python", "-m", "app.main"]