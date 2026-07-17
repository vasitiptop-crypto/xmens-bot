FROM python:3.12-slim

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY . .

# Ensure standard write permissions inside app directory
RUN mkdir -p /app/downloads && chmod -R 777 /app

# Expose port 8080 (the default port Koyeb expects)
EXPOSE 8080

# Run the Koyeb launcher script
CMD ["python", "koyeb_run.py"]
