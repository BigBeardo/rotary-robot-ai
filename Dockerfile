# Use a lightweight Python base image
FROM python:3.11-slim

# Install ffmpeg and festival for audio processing
RUN apt-get update && \
    apt-get install -y ffmpeg festival && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
# (We copy this first to cache the pip install step)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the application code, audio files, and templates into the image
COPY . .

# Run the web dashboard and VoIP engine
CMD ["python", "app.py"]
