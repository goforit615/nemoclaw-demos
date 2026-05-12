.PHONY: help setup up fresh down restart build rebuild gradio api stop-gradio stop-api test test-cov clean status logs logs-gradio logs-api shell games-up games-down games-restart games-logs games-build rag-up rag-down rag-build rag-health

# ============================================================================
# RAG Stack compose variables (defined early — used in multiple targets)
# ============================================================================

# Individual RAG compose files — run in order per NVIDIA docs:
#   vectordb → ingestor-server → rag-server
RAG_VECTORDB  := -f rag/deploy/compose/vectordb.yaml
RAG_INGESTOR  := -f rag/deploy/compose/docker-compose-ingestor-server.yaml
RAG_SERVER    := -f rag/deploy/compose/docker-compose-rag-server.yaml
# On CPU-only hosts append the override that removes the GPU device reservation
ifeq ($(shell nvidia-smi >/dev/null 2>&1 && echo gpu || echo cpu),cpu)
  RAG_VECTORDB += -f rag/deploy/compose/cpu-override.yaml
endif
RAG_ENV := --env-file rag/deploy/compose/.env
# Combined alias used only for rag-down / rag-build
RAG_COMPOSE := $(RAG_VECTORDB) $(RAG_INGESTOR) $(RAG_SERVER)

# ============================================================================
# Help
# ============================================================================

help:
	@echo "╔═══════════════════════════════════════════════════════════════╗"
	@echo "║             AgenticTA Study Buddy — Commands                 ║"
	@echo "╚═══════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Start:"
	@echo "  make up              Start agenticta + RAG stack + Gradio + API"
	@echo "  make fresh           Wipe vector store & user files, then start"
	@echo ""
	@echo "Stop / Restart:"
	@echo "  make down            Stop all services (agenticta + RAG + games)"
	@echo "  make restart         Stop then start"
	@echo ""
	@echo "Individual services:"
	@echo "  make gradio          Start Gradio UI       → http://localhost:7860"
	@echo "  make api             Start FastAPI backend → http://localhost:8000"
	@echo "  make games-up        Start Study Break Games → http://localhost:8080"
	@echo ""
	@echo "Build:"
	@echo "  make build           Build ta_master image"
	@echo "  make rebuild         Full clean rebuild from scratch"
	@echo "  make rag-build       Build RAG service images"
	@echo ""
	@echo "Monitoring:"
	@echo "  make status          Show running containers"
	@echo "  make logs            Tail agenticta container log"
	@echo "  make logs-gradio     Tail Gradio log"
	@echo "  make logs-api        Tail FastAPI log"
	@echo "  make shell           Enter agenticta container shell"
	@echo "  make rag-health      Check RAG service health"
	@echo ""
	@echo "Testing:"
	@echo "  make test            Run unit tests"
	@echo "  make test-cov        Run tests with coverage"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean           Remove all containers and volumes"
	@echo ""

# ============================================================================
# Setup
# ============================================================================

setup:
	@echo "Creating required directories..."
	@mkdir -p mnt/pdfs mnt/users
	@mkdir -p rag/deploy/compose/volumes/{milvus,etcd,minio,elasticsearch,ingestor-server}
	@echo "✅ Directories created!"

# ============================================================================
# Core start targets
# ============================================================================

# Start agenticta + RAG stack + Gradio + API
up: setup
	@echo "Starting agenticta + RAG stack..."
	@echo "Note: NGC_API_KEY required to pull RAG blueprint images from nvcr.io"
	@docker compose up -d
	@docker compose $(RAG_VECTORDB)  $(RAG_ENV) up -d
	@docker compose $(RAG_INGESTOR)  $(RAG_ENV) up -d
	@docker compose $(RAG_SERVER)    $(RAG_ENV) up -d
	@docker network connect nvidia-rag agenticta 2>/dev/null || true
	@$(MAKE) -s _start_gradio_and_api
	@echo ""
	@echo "✅ AgenticTA + RAG ready!"
	@echo "  Gradio UI:  http://localhost:7860"
	@echo "  API + Docs: http://localhost:8000/docs"
	@echo "  RAG Server: http://localhost:8081"
	@echo "  Ingestor:   http://localhost:8082"
	@echo "  Milvus:     http://localhost:19530"
	@echo "  Logs:       make logs-gradio | make logs-api"

# Wipe ALL user data + Milvus vector store, then start
fresh: setup
	@echo "🧹 Wiping all user data before start..."
	@rm -rf mnt/*/pdfs/*.pdf 2>/dev/null || true
	@rm -rf mnt/*/state.txt 2>/dev/null || true
	@rm -rf mnt/*/global_state.json 2>/dev/null || true
	@rm -rf mnt/*/user_store/*.json 2>/dev/null || true
	@rm -rf mnt/*/memory/ 2>/dev/null || true
	@echo "✅ User data cleared (PDFs, state, memory)"
	@$(MAKE) up
	@sleep 5
	@echo "Clearing Milvus collections..."
	@curl -s -X DELETE http://localhost:8000/api/files/collections > /dev/null 2>&1 || true
	@echo "✅ Vector store cleared"

# Internal: start Gradio and API inside the running container
_start_gradio_and_api:
	@echo "Starting Gradio UI..."
	@docker compose exec agenticta pkill -f gradioUI.py 2>/dev/null || true
	@docker compose exec -d agenticta bash -c "cd /workspace && python gradioUI.py > /tmp/gradio.log 2>&1"
	@echo "Starting FastAPI backend..."
	@docker compose exec agenticta pkill -f "uvicorn api.main" 2>/dev/null || true
	@docker compose exec -d agenticta bash -c "cd /workspace && AGENTICTA_REQUIRE_STORAGE_MOUNT=false INGESTOR_SERVER_HOST=http://ingestor-server:8082 RAG_SERVER_HOST=http://rag-server:8081 uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/api.log 2>&1"
	@sleep 4

# ============================================================================
# Stop / Restart
# ============================================================================

down:
	@echo "Stopping all services..."
	@docker compose down
	@docker compose $(RAG_COMPOSE) $(RAG_ENV) down 2>/dev/null || true
	@if docker ps -a --filter "name=study-break-games" --format "{{.Names}}" | grep -q "study-break-games" 2>/dev/null; then \
		cd StudyBreakGames && docker compose down 2>/dev/null || true; \
	fi
	@echo "✅ All services stopped"

restart: down up

# ============================================================================
# Individual service controls
# ============================================================================

gradio:
	@echo "Starting Gradio UI..."
	@docker compose exec agenticta pkill -f gradioUI.py 2>/dev/null || true
	@docker compose exec -d agenticta bash -c "cd /workspace && python gradioUI.py > /tmp/gradio.log 2>&1"
	@sleep 3
	@echo "✅ Gradio UI started → http://localhost:7860"
	@echo "  Logs: make logs-gradio"

api:
	@echo "Starting FastAPI backend..."
	@docker compose exec agenticta pkill -f "uvicorn api.main" 2>/dev/null || true
	@docker compose exec -d agenticta bash -c "cd /workspace && AGENTICTA_REQUIRE_STORAGE_MOUNT=false INGESTOR_SERVER_HOST=http://ingestor-server:8082 RAG_SERVER_HOST=http://rag-server:8081 uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/api.log 2>&1"
	@sleep 2
	@echo "✅ FastAPI started → http://localhost:8000/docs"

stop-gradio:
	@docker compose exec agenticta pkill -f gradioUI.py || echo "Gradio not running"

stop-api:
	@docker compose exec agenticta pkill -f "uvicorn api.main" || echo "API not running"

# ============================================================================
# Build
# ============================================================================

build:
	@echo "Building ta_master image..."
	@docker build -t ta_master:latest .
	@echo "✅ Image built"

rebuild: clean
	@docker build --no-cache -t ta_master:latest .
	@$(MAKE) up
	@echo "✅ Rebuild complete!"

# ============================================================================
# Monitoring
# ============================================================================

status:
	@docker compose ps
	@docker compose $(RAG_COMPOSE) $(RAG_ENV) ps 2>/dev/null || true

logs:
	@docker compose logs -f agenticta

logs-gradio:
	@docker compose exec agenticta tail -f /tmp/gradio.log

logs-api:
	@docker compose exec agenticta tail -f /tmp/api.log

shell:
	@docker compose exec agenticta /bin/bash

# ============================================================================
# Testing
# ============================================================================

test:
	@echo "Running unit tests..."
	@docker compose exec agenticta pytest -v -m "not integration and not slow"

test-cov:
	@echo "Running tests with coverage..."
	@docker compose exec -e COVERAGE_FILE=/tmp/.coverage agenticta pytest --cov=. --cov-report=term-missing

# ============================================================================
# Cleanup
# ============================================================================

clean:
	@echo "Cleaning up..."
	@docker compose down -v --remove-orphans
	@docker compose $(RAG_COMPOSE) $(RAG_ENV) down -v --remove-orphans 2>/dev/null || true
	@cd StudyBreakGames && docker compose down -v --remove-orphans 2>/dev/null || true
	@docker system prune -f
	@echo "✅ Cleanup complete"

# ============================================================================
# Study Break Games
# ============================================================================

GAMES_VERSION := $(shell grep VERSION StudyBreakGames/version.py 2>/dev/null | cut -d'"' -f2 || echo "0.0.1")
GAMES_LOCAL_TAG := study-break-games

games-up:
	@echo "Starting Study Break Games..."
	@cd StudyBreakGames && docker compose up -d
	@echo "✅ Study Break Games → http://localhost:8080"

games-down:
	@cd StudyBreakGames && docker compose down
	@echo "✅ Study Break Games stopped"

games-restart: games-down games-up

games-logs:
	@cd StudyBreakGames && docker compose logs -f

games-build:
	@echo "Building Study Break Games..."
	@cd StudyBreakGames && docker build --network=host -t $(GAMES_LOCAL_TAG):$(GAMES_VERSION) .
	@echo "✅ Study Break Games built"

# ============================================================================
# RAG Stack
# ============================================================================

rag-up: setup
	@echo "Starting RAG services (vectordb → ingestor → rag-server)..."
	@docker compose $(RAG_VECTORDB)  $(RAG_ENV) up -d
	@docker compose $(RAG_INGESTOR)  $(RAG_ENV) up -d
	@docker compose $(RAG_SERVER)    $(RAG_ENV) up -d
	@docker network connect nvidia-rag agenticta 2>/dev/null || true
	@echo "✅ RAG services started"
	@echo "  Ingestor: http://localhost:8082/v1/health"
	@echo "  RAG:      http://localhost:8081/v1/health"

rag-down:
	@docker compose $(RAG_COMPOSE) $(RAG_ENV) down
	@echo "✅ RAG services stopped"

rag-build:
	@echo "Building RAG service images..."
	@docker compose $(RAG_COMPOSE) $(RAG_ENV) build
	@echo "✅ RAG images built"

rag-health:
	@echo "Checking RAG service health..."
	@curl -s http://localhost:8082/v1/health || echo "  ❌ Ingestor not responding"
	@curl -s http://localhost:8081/v1/health || echo "  ❌ RAG server not responding"
	@curl -s http://localhost:9091/healthz 2>/dev/null || echo "  ❌ Milvus not responding"
