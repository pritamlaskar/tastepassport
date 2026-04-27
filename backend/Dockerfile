FROM python:3.11-slim

# Install system dependencies needed for Playwright and Scrapy
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium system deps manually — avoids ttf-unifont/ttf-ubuntu-font-family
# which were renamed/removed in Debian Trixie (python:3.11-slim base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-unifont \
    fonts-liberation \
    fonts-noto-color-emoji \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libasound2t64 \
    libdbus-1-3 \
    libx11-6 \
    libxcb1 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright browser (system deps already installed above)
RUN playwright install chromium

COPY . .

EXPOSE 8000
