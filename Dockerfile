# Multi-stage build for optimized production image
FROM python:3.12-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Create and use a non-root user
RUN useradd --create-home --shell /bin/bash app

# Set work directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Switch to app user for pip install
USER app

# Install Python dependencies
RUN pip install --user --no-warn-script-location -r requirements.txt

# Production stage
FROM python:3.12-slim AS production

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:$PATH" \
    PYTHONPATH="/app"

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create and use a non-root user
RUN useradd --create-home --shell /bin/bash app

# Copy Python dependencies from builder stage
COPY --from=builder /home/app/.local /home/app/.local

# Set work directory and ownership
WORKDIR /app
RUN chown app:app /app

# Switch to non-root user
USER app

# Copy application code
COPY --chown=app:app . .

# Create directory for logs
RUN mkdir -p /app/logs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Default command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
