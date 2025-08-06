# Base image with python
FROM python:3.11-slim

# Set DEBIAN_FRONTEND to noninteractive
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for Playwright and Chromium
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Install Chromium browser and its dependencies
    chromium \
    # Dependencies for Playwright
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libpangocairo-1.0-0 libasound2 libxshmfence1 wget gnupg ca-certificates \
    # Extra dependencies that might be missing
    libxtst6 libxss1 libgconf-2-4 libnss3-tools libgdk-pixbuf2.0-0 libx11-xcb1 && \
    # Clean up
    rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

# Copy source code
COPY . /app

# Expose port
EXPOSE 8000

# Start server
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
