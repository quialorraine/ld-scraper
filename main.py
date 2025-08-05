from fastapi import FastAPI, HTTPException, BackgroundTasks, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import glob
import json
import uuid
import asyncio

from linkedin_scraper import LinkedInScraper

app = FastAPI(title="LinkedIn Scraper API", version="1.0.0")

# ------------------------------
# Storage helpers
# ------------------------------
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATTERN: str = os.path.join(BASE_DIR, "linkedin_profile_*.json")

_profiles: Dict[str, Dict[str, Any]] = {}
_tasks: Dict[str, Dict[str, Any]] = {}


def _slug_from_path(path: str) -> str:
    """Extract slug from file path like linkedin_profile_<slug>.json"""
    name = os.path.basename(path)
    if name.startswith("linkedin_profile_") and name.endswith(".json"):
        return name[len("linkedin_profile_") : -len(".json")]
    return name


def load_profiles() -> None:
    """Load every JSON file that matches the pattern into _profiles dict."""
    _profiles.clear()
    for file_path in glob.glob(PROFILE_PATTERN):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            slug = _slug_from_path(file_path)
            data["id"] = slug
            _profiles[slug] = data
        except Exception as e:
            # Skip corrupted files but log to stderr
            print(f"[WARN] Failed to load {file_path}: {e}")


# Load profiles at startup
load_profiles()


# ------------------------------
# Pydantic models
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
# Routes
# ------------------------------
@app.get("/profiles", response_model=List[ProfileBasic])
async def list_profiles() -> List[Dict[str, Any]]:
    """Return a list of all stored profiles with minimal fields."""
    return [
        {
            "id": p["id"],
            "first_name": p.get("first_name"),
            "last_name": p.get("last_name"),
            "headline": p.get("headline"),
        }
        for p in _profiles.values()
    ]


# Search must be defined BEFORE /profiles/{profile_id} to avoid route shadowing
@app.get("/profiles/search", response_model=List[ProfileBasic])
async def search_profiles(query: str) -> List[Dict[str, Any]]:
    """Search profiles by substring in first/last name or headline (case-insensitive)."""
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []
    for p in _profiles.values():
        concat_fields = " ".join(filter(None, [p.get("first_name"), p.get("last_name"), p.get("headline")])).lower()
        if query_lower in concat_fields:
            results.append({
                "id": p["id"],
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "headline": p.get("headline"),
            })
    return results


@app.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> Dict[str, Any]:
    """Return a full JSON profile or 404 if not found."""
    if profile_id not in _profiles:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _profiles[profile_id]





@app.post("/profiles/scrape", status_code=status.HTTP_202_ACCEPTED)
async def scrape_profile(req: ScrapeRequest, background_tasks: BackgroundTasks):
    """Run scraping in the background and return task ID."""
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "queued"}

    background_tasks.add_task(_run_scraper_task, task_id, req)
    profile_id = req.url.strip("/").split("/")[-1].split("?")[0]
    return {"task_id": task_id, "profile_id": profile_id}


async def _run_scraper_task(task_id: str, req: ScrapeRequest) -> None:
    """Wrapper that actually executes the LinkedInScraper and updates task status."""
    try:
        profile_name = req.url.strip("/").split("/")[-1].split("?")[0]
        _tasks[task_id].update({"status": "running", "profile_id": profile_name})
        scraper = LinkedInScraper(req.cookie, req.url)
        scraped_data = await scraper.run(
            scrape_posts=req.scrape_posts,
            scrape_comments=req.scrape_comments,
            scrape_reactions=req.scrape_reactions,
        )
        await scraper.close_browser()

        if scraped_data:
            profile_name = req.url.strip("/").split("/")[-1].split("?")[0]
            file_name = os.path.join(BASE_DIR, f"linkedin_profile_{profile_name}.json")
            existing_data: Dict[str, Any] = {}
            if os.path.exists(file_name):
                with open(file_name, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            existing_data.update(scraped_data)
            with open(file_name, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=4)
        # Reload profiles so that new/updated data is available immediately
        load_profiles()
        _tasks[task_id]["status"] = "completed"
    except Exception as e:
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = str(e)


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
