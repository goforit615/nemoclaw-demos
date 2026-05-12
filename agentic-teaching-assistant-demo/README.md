# AgenticTA — AI Study Buddy

An AI teaching assistant that turns your PDFs into a personalised study experience.

## Features

- PDF upload → curriculum + study materials
- Study buddy chat with agentic memory
- Quiz generation per subtopic
- Calendar booking (natural language → .ics)
- Image upload + Q&A
- YouTube video search for study topics
- Study break games

## Get Started

```bash
cp .env.example .env   # add INFERENCE_API_KEY and NGC_API_KEY (both required)
make setup
make up                # always starts the RAG stack (Milvus + ingestor + RAG server)
make gradio
# open http://localhost:7860
```

See **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for full setup and troubleshooting.

## Requirements

- Docker + Docker Compose v2
- `INFERENCE_API_KEY` — NVIDIA Inference Hub key, used by the TA app for all LLM calls
- `NGC_API_KEY` — [build.nvidia.com](https://build.nvidia.com) key with NGC registry access; used to pull every [NVIDIA-AI-Blueprints/rag](https://github.com/NVIDIA-AI-Blueprints/rag) Docker image from `nvcr.io`

The RAG stack (Milvus + ingestor + RAG server, following the [NVIDIA-hosted Docker deployment](https://github.com/NVIDIA-AI-Blueprints/rag/blob/main/docs/deploy-docker-nvidia-hosted.md)) is mandatory — there is no direct-PDF fallback.

## Commands

```bash
make help     # all commands
make up       # start full stack (agenticta + RAG stack + Gradio + API)
make fresh    # wipe user data + Milvus, then start
make gradio   # launch UI at http://localhost:7860
make games-up # study break games at http://localhost:8080
make down     # stop everything
```
