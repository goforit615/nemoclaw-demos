"""
File Upload Routes

Handles PDF file uploads and ingestion to the RAG system.
"""

import os
import sys
import shutil
import fitz  # PyMuPDF
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
try:
    from werkzeug.utils import secure_filename
except ImportError:
    # Fallback when werkzeug is unavailable: keep only basename.
    def secure_filename(filename: str) -> str:
        return Path(filename).name.strip()

# Add parent directory to path
parent_dir = Path(__file__).parent.parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from common.debug import debug_print
router = APIRouter()

# Route file-ingestion diagnostics through debug gate.
print = debug_print

# Default paths
SAVE_TO = os.environ.get("AGENTICTA_SAVE_TO", "/workspace/mnt/")

# File validation constants
MAX_FILES = 10
MAX_FILE_SIZE_MB = 4  # 4 MB file size limit
MAX_FILE_SIZE_KB = MAX_FILE_SIZE_MB * 1024  # Derived from MB (matches frontend limit)
MAX_PAGES = 50  # Maximum pages per PDF
ALLOWED_EXTENSIONS = [".pdf"]


class FileUploadResponse(BaseModel):
    """Response schema for file upload."""
    success: bool
    files: List[dict]
    message: str
    errors: List[dict] = []  # List of validation errors for rejected files


class FileIngestRequest(BaseModel):
    """Request schema for ingesting files to RAG."""
    user_id: str
    filenames: List[str]


class FileIngestResponse(BaseModel):
    """Response schema for file ingestion."""
    success: bool
    collection: str | None = None
    message: str


def _get_backend():
    """Lazy load backend functions."""
    try:
        from nodes import init_user_storage
        return {"init_user_storage": init_user_storage, "available": True}
    except ImportError:
        return {"available": False}


def _mock_init_storage(save_to: str, user_id: str):
    """Mock init_user_storage."""
    user_dir = Path(save_to) / user_id
    user_dir.mkdir(parents=True, exist_ok=True)


def validate_file(file: UploadFile) -> tuple[bool, str]:
    """Validate an uploaded file."""
    file_ext = Path(file.filename or "").suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file type: {file_ext}. Only PDF files are allowed."
    return True, ""


def _is_within_directory(path: Path, directory: Path) -> bool:
    """Return True when resolved path is inside resolved directory."""
    resolved_path = path.resolve()
    resolved_directory = directory.resolve()
    try:
        return resolved_path.is_relative_to(resolved_directory)
    except AttributeError:
        return (
            os.path.commonpath([str(resolved_path), str(resolved_directory)])
            == str(resolved_directory)
        )


@router.post("/upload", response_model=FileUploadResponse)
async def upload_files(
    user_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """
    Upload PDF files for a user.
    """
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum {MAX_FILES} files allowed."
        )
    
    backend = _get_backend()
    if backend["available"]:
        backend["init_user_storage"](SAVE_TO, user_id)
    else:
        _mock_init_storage(SAVE_TO, user_id)
    
    # Create user's PDF directory
    user_pdf_dir = Path(SAVE_TO) / user_id / "pdfs"
    user_pdf_dir.mkdir(parents=True, exist_ok=True)
    resolved_user_pdf_dir = user_pdf_dir.resolve()
    
    uploaded_files = []
    errors = []
    
    for file in files:
        is_valid, error_msg = validate_file(file)
        if not is_valid:
            errors.append({"file": file.filename, "error": error_msg})
            continue
        
        try:
            original_filename = file.filename or ""
            safe_filename = secure_filename(original_filename)
            if not safe_filename:
                errors.append({"file": original_filename, "error": "Invalid filename"})
                continue

            file_path = user_pdf_dir / safe_filename
            if not _is_within_directory(file_path, resolved_user_pdf_dir):
                errors.append({
                    "file": original_filename,
                    "error": "Invalid filename path",
                })
                continue

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            
            # Check file size
            if file_size > MAX_FILE_SIZE_KB * 1024:
                os.remove(file_path)
                errors.append({
                    "file": original_filename, 
                    "error": f"File too large ({file_size_mb:.1f} MB). Maximum {MAX_FILE_SIZE_MB} MB allowed."
                })
                continue

            # Check PDF page count using PyMuPDF (fitz)
            try:
                doc = fitz.open(file_path)
                page_count = len(doc)
                doc.close()
                
                if page_count > MAX_PAGES:
                    os.remove(file_path)
                    errors.append({
                        "file": original_filename,
                        "error": f"Too many pages ({page_count} pages). Maximum {MAX_PAGES} pages allowed."
                    })
                    continue
            except Exception as e:
                # If we can't read the PDF, treat as invalid or log warning
                # For strict validation, we might want to fail
                # But here we'll log it as error
                if os.path.exists(file_path):
                    os.remove(file_path)
                errors.append({
                    "file": original_filename,
                    "error": f"Invalid PDF file: {str(e)}"
                })
                continue
            
            uploaded_files.append({
                "name": safe_filename,
                "size": file_size,
                "path": str(file_path),
            })
            
        except Exception as e:
            errors.append({"file": file.filename or "", "error": str(e)})
    
    if not uploaded_files and errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "All files failed to upload", "errors": errors}
        )

    # Ingest into Milvus — the RAG stack is mandatory, so failures here are fatal.
    if uploaded_files:
        from nemo_retriever_client_utils import (
            fetch_collections, create_collection, upload_files_to_nemo_retriever
        )
        collection_name = user_id
        try:
            collections_response = await fetch_collections()
            existing = [
                c.get("name", c) if isinstance(c, dict) else c
                for c in (collections_response.get("collections", []) if isinstance(collections_response, dict) else [])
            ]
            if collection_name not in existing:
                await create_collection(collection_name)
            file_paths = [f["path"] for f in uploaded_files]
            await upload_files_to_nemo_retriever(file_paths, collection_name)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Ingestion into the RAG vector store failed. "
                               "The RAG stack is mandatory — check ingestor (port 8082) health.",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            ) from exc

    message = f"Successfully uploaded {len(uploaded_files)} file(s) and ingested into vector store"
    if errors:
        message += f". {len(errors)} file(s) failed."

    return FileUploadResponse(
        success=True,
        files=uploaded_files,
        message=message,
        errors=errors,
    )


@router.post("/ingest", response_model=FileIngestResponse)
async def ingest_files(request: FileIngestRequest):
    """
    Ingest uploaded PDF files into the RAG system (NeMo Retriever).
    
    This function:
    1. Checks if collection exists for user
    2. Creates collection if needed (new user)
    3. Uploads files to NeMo Retriever for vectorization
    """
    user_id = request.user_id
    user_pdf_dir = Path(SAVE_TO) / user_id / "pdfs"
    
    if not user_pdf_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No PDF directory found for user '{user_id}'. Upload files first."
        )
    
    if request.filenames:
        files_to_ingest = [
            user_pdf_dir / f for f in request.filenames 
            if (user_pdf_dir / f).exists()
        ]
    else:
        files_to_ingest = list(user_pdf_dir.glob("*.pdf"))
    
    if not files_to_ingest:
        raise HTTPException(
            status_code=404,
            detail="No PDF files found to ingest"
        )
    
    try:
        from nemo_retriever_client_utils import (
            fetch_collections,
            create_collection,
            upload_files_to_nemo_retriever,
        )
        
        # Use user_id as collection name
        collection_name = user_id
        
        # Step 1: Check if collection already exists
        print(f"📋 Checking if collection '{collection_name}' exists...")
        collections_response = await fetch_collections()
        existing_collections = []
        
        if isinstance(collections_response, dict):
            existing_collections = [
                c.get("name", c) if isinstance(c, dict) else c 
                for c in collections_response.get("collections", [])
            ]
        
        # Step 2: Create collection if it doesn't exist (new user)
        if collection_name not in existing_collections:
            print(f"📁 Creating new collection '{collection_name}'...")
            await create_collection(collection_name)
        else:
            print(f"✅ Collection '{collection_name}' already exists")
        
        # Step 3: Upload files to NeMo Retriever for vectorization
        file_paths = [str(f) for f in files_to_ingest]
        print(f"📤 Uploading {len(file_paths)} file(s) to NeMo Retriever...")
        
        await upload_files_to_nemo_retriever(file_paths, collection_name)
        
        return FileIngestResponse(
            success=True,
            collection=collection_name,
            message=f"Successfully uploaded {len(files_to_ingest)} file(s) to collection '{collection_name}' for vectorization",
        )
        
    except ImportError as e:
        print(f"⚠️ NeMo Retriever module not available: {e}")
        return FileIngestResponse(
            success=True,
            collection=user_id,
            message=f"RAG ingestion skipped (module not available). {len(files_to_ingest)} file(s) ready for curriculum generation.",
        )
    except Exception as e:
        print(f"❌ Error during ingestion: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error during RAG ingestion: {str(e)}"
        )


@router.get("/ingest-status/{user_id}")
async def ingest_status(user_id: str):
    """
    Check whether PDFs for a user have been fully ingested into the vector store.

    Returns:
        ready: bool   — True when at least one chunk exists in the collection
        chunk_count: int — number of vector chunks stored (more = more complete)
        exists: bool  — whether the collection has been created at all
    """
    ingestor_url = os.environ.get("INGESTOR_SERVER_HOST", "http://localhost:8082")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ingestor_url}/v1/collections", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return {"user_id": user_id, "ready": False, "chunk_count": 0, "exists": False, "message": f"Ingestor returned {resp.status}"}
                data = await resp.json()
                collections = data.get("collections", [])
                match = next((c for c in collections if c.get("collection_name") == user_id), None)
                if not match:
                    return {"user_id": user_id, "ready": False, "chunk_count": 0, "exists": False, "message": "Collection not found — PDF not yet ingested"}
                num_entities = match.get("num_entities", 0)
                ingestion_status = match.get("collection_info", {}).get("ingestion_status", "")
                ready = num_entities > 0 and ingestion_status == "Success"
                return {
                    "user_id": user_id,
                    "ready": ready,
                    "chunk_count": num_entities,
                    "exists": True,
                    "message": "Ready for curriculum generation" if ready else f"Ingestion status: {ingestion_status} ({num_entities} chunks)",
                }
    except Exception as e:
        return {"user_id": user_id, "ready": False, "chunk_count": 0, "exists": False, "message": f"Ingestor not reachable: {e}"}


def _delete_user_files_on_disk(user_id: str) -> int:
    """Delete uploaded PDFs for a user from disk. Returns number of files removed."""
    user_pdf_dir = Path(SAVE_TO) / user_id / "pdfs"
    count = 0
    if user_pdf_dir.exists():
        for f in user_pdf_dir.glob("*.pdf"):
            f.unlink()
            count += 1
    return count


@router.delete("/collections")
async def delete_all_collections():
    """
    Full reset: delete ALL Milvus collections AND uploaded PDF files from disk.
    Run this before re-uploading and re-ingesting from scratch.
    """
    ingestor_url = os.environ.get("INGESTOR_SERVER_HOST", "http://localhost:8082")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # 1. List all collections
            async with session.get(f"{ingestor_url}/v1/collections", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail=f"Ingestor returned {resp.status} listing collections")
                data = await resp.json()
                collections = [c["name"] if isinstance(c, dict) else c for c in data.get("collections", [])]

            # 2. Delete Milvus collections
            if collections:
                async with session.delete(
                    f"{ingestor_url}/v1/collections",
                    json=collections,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as del_resp:
                    if del_resp.status != 200:
                        raise HTTPException(status_code=502, detail=f"Ingestor returned {del_resp.status} deleting collections")

        # 3. Delete PDF files from disk — scan all user dirs, not just known collections
        #    (handles the case where vector store is empty but files are still on disk)
        mnt = Path(SAVE_TO)
        all_users = {p.name for p in mnt.iterdir() if p.is_dir()} if mnt.exists() else set()
        users_to_wipe = set(collections) | all_users
        files_removed = sum(_delete_user_files_on_disk(u) for u in users_to_wipe)

        return {
            "deleted": collections,
            "files_removed": files_removed,
            "message": f"Deleted {len(collections)} collection(s) and {files_removed} PDF file(s) from disk",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestor not reachable: {e}")


@router.delete("/collections/{user_id}")
async def delete_user_collection(user_id: str):
    """Delete a user's Milvus collection AND their uploaded PDF files from disk."""
    ingestor_url = os.environ.get("INGESTOR_SERVER_HOST", "http://localhost:8082")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{ingestor_url}/v1/collections",
                json=[user_id],
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail=f"Ingestor returned {resp.status}")

        files_removed = _delete_user_files_on_disk(user_id)
        return {
            "deleted": [user_id],
            "files_removed": files_removed,
            "message": f"Deleted collection '{user_id}' and {files_removed} PDF file(s) from disk",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestor not reachable: {e}")


@router.get("/list/{user_id}")
async def list_files(user_id: str):
    """
    List uploaded PDF files for a user.
    """
    user_pdf_dir = Path(SAVE_TO) / user_id / "pdfs"
    
    if not user_pdf_dir.exists():
        return {"files": [], "message": "No files uploaded yet"}
    
    files = []
    for file_path in user_pdf_dir.glob("*.pdf"):
        files.append({
            "name": file_path.name,
            "size": os.path.getsize(file_path),
            "path": str(file_path),
        })
    
    return {"files": files, "count": len(files)}


@router.delete("/{user_id}/{filename}")
async def delete_file(user_id: str, filename: str):
    """
    Delete a specific uploaded file.
    """
    file_path = Path(SAVE_TO) / user_id / "pdfs" / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")
    
    try:
        os.remove(file_path)
        return {"success": True, "message": f"File '{filename}' deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")
