# Setup Guide

This guide covers how to set up and run Open WebUI locally and using Docker.

## Prerequisites

- **Python 3.11+** (for local installation)
- **Docker** (for Docker installation)
- **Docker Compose** (optional, for compose setup)

## Local Installation

### Option 1: Using pip

1. **Install Open WebUI**:
   ```bash
   pip install open-webui
   ```

2. **Run Open WebUI**:
   ```bash
   open-webui serve
   ```

   Access the application at [http://localhost:8080](http://localhost:8080)

### Option 2: Using uv (Development)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/open-webui/open-webui.git
   cd open-webui
   ```

2. **Install dependencies with uv**:
   ```bash
   uv sync
   ```

3. **Run the development server**:
   ```bash
   ./backend/dev.sh
   ```

   Access the application at [http://localhost:8080](http://localhost:8080)

### Configuration (Local)

Create a `.env` file in the project root based on `.env.example`:

```bash
cp .env.example .env
```

Key configuration options:
- `OLLAMA_BASE_URL`: Ollama server URL (default: `http://localhost:11434`)
- `OPENAI_API_KEY`: OpenAI API key (if using OpenAI)
- `CORS_ALLOW_ORIGIN`: CORS allowed origins (default: `*`)

## Docker Installation

### Option 1: Quick Start (Default)

**If Ollama is on your computer**:
```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

**If Ollama is on a different server**:
```bash
docker run -d -p 3000:8080 \
  -e OLLAMA_BASE_URL=https://example.com \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

Access the application at [http://localhost:3000](http://localhost:3000)

### Option 2: With OpenAI API Only

```bash
docker run -d -p 3000:8080 \
  -e OPENAI_API_KEY=your_secret_key \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

### Option 3: With NVIDIA GPU Support

```bash
docker run -d -p 3000:8080 \
  --gpus all \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:cuda
```

**Note**: Requires [NVIDIA CUDA container toolkit](https://docs.nvidia.com/dgx/nvidia-container-runtime-upgrade/)

### Option 4: With Bundled Ollama

**With GPU support**:
```bash
docker run -d -p 3000:8080 \
  --gpus=all \
  -v ollama:/root/.ollama \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:ollama
```

**CPU only**:
```bash
docker run -d -p 3000:8080 \
  -v ollama:/root/.ollama \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:ollama
```

## Docker Compose Setup

The repository includes a `docker-compose.yaml` for SearXNG (web search) integration.

1. **Start SearXNG**:
   ```bash
   docker-compose up -d
   ```

2. **Access SearXNG**:
   - SearXNG: [http://localhost:8090](http://localhost:8090)

3. **Configure Open WebUI**:
   - In Open WebUI settings, set the web search URL to `http://localhost:8090`

## Troubleshooting

### Docker Connection Issues

If you experience connection issues between Open WebUI and Ollama, use the `--network=host` flag:

```bash
docker run -d --network=host \
  -v open-webui:/app/backend/data \
  -e OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

Note: With `--network=host`, the port changes to 8080: [http://localhost:8080](http://localhost:8080)

### Offline Mode

For offline environments, set the environment variable:

```bash
export HF_HUB_OFFLINE=1
```

### Data Persistence

**Important**: Always include `-v open-webui:/app/backend/data` in your Docker command to ensure your database is properly mounted and prevent data loss.

## Updating

### Docker

Pull the latest image and recreate the container:

```bash
docker pull ghcr.io/open-webui/open-webui:main
docker stop open-webui
docker rm open-webui
# Run your preferred docker run command again
```

### Local (pip)

```bash
pip install --upgrade open-webui
```

### Local (Development)

```bash
git pull
uv sync
```

## Additional Resources

- [Full Documentation](https://docs.openwebui.com/)
- [Troubleshooting Guide](https://docs.openwebui.com/troubleshooting/)
- [Discord Community](https://discord.gg/5rJgQTnV4s)
