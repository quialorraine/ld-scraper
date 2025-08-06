from fastapi import FastAPI, HTTPException, BackgroundTasks, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import glob
import json
import uuid
import asyncio
import aiofiles

from linkedin_scraper import LinkedInScraper

app = FastAPI(title="LinkedIn Scraper API", version="1.0.0")

# ------------------------------
# New Storage Structure
# ------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
TASKS_DIR = os.path.join(DATA_DIR, "tasks")

# Ensure directories exist
os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(TASKS_DIR, exist_ok=True)

# ------------------------------
# Pydantic models (no changes)
# ------------------------------
class ProfileBasic(BaseModel):
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None

class ScrapeRequest(BaseModel):
    url: str
    cookie: str
    scrape_posts: bool = False
    scrape_comments: bool = False
    scrape_reactions: bool = False

# ------------------------------
# File-based Helper Functions
# ------------------------------
def _slug_from_url(url: str) -> str:
    """Extract a safe filename slug from a LinkedIn profile URL."""
    return url.strip("/").split("/")[-1].split("?")[0]

async def _write_task_status(task_id: str, status_data: Dict[str, Any]):
    """Asynchronously write task status to a file."""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    async with aiofiles.open(task_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(status_data, indent=4))

async def _read_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Asynchronously read task status from a file."""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(task_file):
        return None
    async with aiofiles.open(task_file, "r", encoding="utf-8") as f:
        content = await f.read()
        return json.loads(content)

# ------------------------------
# Main Scraper Task (modified for file-based status)
# ------------------------------
async def _run_scraper_task(task_id: str, req: ScrapeRequest) -> None:
    """
    Wrapper that executes the scraper and updates the task status file.
    """
    profile_id = _slug_from_url(req.url)
    await _write_task_status(task_id, {"status": "running", "profile_id": profile_id})

    try:
        scraper = LinkedInScraper(req.cookie, req.url)
        scraped_data = await scraper.run(
            scrape_posts=req.scrape_posts,
            scrape_comments=req.scrape_comments,
            scrape_reactions=req.scrape_reactions,
        )
        await scraper.close_browser()

        if scraped_data:
            profile_file = os.path.join(PROFILES_DIR, f"{profile_id}.json")
            
            # Read existing data if it exists
            existing_data: Dict[str, Any] = {}
            if os.path.exists(profile_file):
                async with aiofiles.open(profile_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    existing_data = json.loads(content)
            
            # Merge and write back
            existing_data.update(scraped_data)
            async with aiofiles.open(profile_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(existing_data, ensure_ascii=False, indent=4))
        
        await _write_task_status(task_id, {"status": "completed", "profile_id": profile_id})

    except Exception as e:
        error_info = {"status": "error", "profile_id": profile_id, "error": str(e)}
        await _write_task_status(task_id, error_info)
        print(f"[ERROR] Task {task_id} failed: {e}")


# ------------------------------
# API Routes (modified for file-based logic)
# ------------------------------
@app.post("/profiles/scrape", status_code=status.HTTP_202_ACCEPTED)
async def scrape_profile(req: ScrapeRequest, background_tasks: BackgroundTasks):
    """
    Run scraping in the background and return task ID.
    Task status is stored in a file.
    """
    task_id = str(uuid.uuid4())
    profile_id = _slug_from_url(req.url)
    
    # Immediately create the task file
    await _write_task_status(task_id, {"status": "queued", "profile_id": profile_id})
    
    background_tasks.add_task(_run_scraper_task, task_id, req)
    return {"task_id": task_id, "profile_id": profile_id}


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Return the status of a task by reading its status file.
    """
    task_data = await _read_task_status(task_id)
    if not task_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task_data


@app.get("/profiles", response_model=List[ProfileBasic])
async def list_profiles() -> List[Dict[str, Any]]:
    """
    Return a list of all stored profiles by reading from the profiles directory.
    """
    profiles: List[Dict[str, Any]] = []
    profile_files = glob.glob(os.path.join(PROFILES_DIR, "*.json"))
    for file_path in profile_files:
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
            profile_id = os.path.basename(file_path).replace(".json", "")
            profiles.append({
                "id": profile_id,
                "first_name": data.get("first_name"),
                "last_name": data.get("last_name"),
                "headline": data.get("headline"),
            })
        except Exception as e:
            print(f"[WARN] Failed to load profile {file_path}: {e}")
    return profiles


@app.get("/profiles/search", response_model=List[ProfileBasic])
async def search_profiles(query: str) -> List[Dict[str, Any]]:
    """
    Search profiles by reading all files and checking for a substring match.
    """
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []
    profile_files = glob.glob(os.path.join(PROFILES_DIR, "*.json"))
    
    for file_path in profile_files:
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                p = json.loads(content)
            
            concat_fields = " ".join(filter(None, [p.get("first_name"), p.get("last_name"), p.get("headline")])).lower()
            if query_lower in concat_fields:
                profile_id = os.path.basename(file_path).replace(".json", "")
                results.append({
                    "id": profile_id,
                    "first_name": p.get("first_name"),
                    "last_name": p.get("last_name"),
                    "headline": p.get("headline"),
                })
        except Exception as e:
            print(f"[WARN] Failed to search profile {file_path}: {e}")
            
    return results


@app.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> Dict[str, Any]:
    """
    Return a full JSON profile by reading the corresponding file.
    """
    profile_file = os.path.join(PROFILES_DIR, f"{profile_id}.json")
    if not os.path.exists(profile_file):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    
    async with aiofiles.open(profile_file, "r", encoding="utf-8") as f:
        content = await f.read()
        return json.loads(content)
